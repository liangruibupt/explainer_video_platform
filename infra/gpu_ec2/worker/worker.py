#!/usr/bin/env python3
"""EVP GPU worker poll loop (runs on the EC2 instance, started by bootstrap.sh).

Queue = S3 under <jobs_prefix> (see infra/gpu_ec2/modules/<module>.json):
  in/<id>.json       request, written by the controller
  claimed/<id>.json  claim marker, written with a conditional put (If-None-Match) so only one
                     worker wins; the worker then deletes in/<id>.json when it may (the
                     controller cleans up in/ and claimed/ after the result lands)
  out/<id>.json      result metadata; the MP4 itself is out/<id>.mp4
  fail/<id>.json     failure reason
  heartbeat.json     {instance_id, state, ts, instance_type, az, lifecycle, ...}

One job at a time: POST the request to the local adapter's /invocations, write the result.
Idle shutdown: idle_shutdown_s without a job -> `shutdown -h now` (instance terminates,
because it is launched with InstanceInitiatedShutdownBehavior=terminate).
Spot interruption notice -> the job in flight goes back to in/ and the worker stops.

  worker.py                      run the loop
  worker.py --heartbeat STATE    write one heartbeat and exit (used by bootstrap.sh)
"""
import argparse, base64, json, os, subprocess, sys, threading, time, urllib.request

import boto3

CFG = json.load(open("/opt/evp/config.json"))
ST = "/opt/evp/state"
B, P = CFG["bucket"], CFG["jobs_prefix"]
PORT = CFG["container_port"]
CNAME = CFG.get("container_name", "h3")
s3 = boto3.client("s3", region_name=CFG["region"])


def log(*a):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "[worker]", *a, flush=True)


def imds(path):
    try:
        tok = urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"}), timeout=2).read().decode()
        return urllib.request.urlopen(urllib.request.Request(
            f"http://169.254.169.254/latest/meta-data/{path}",
            headers={"X-aws-ec2-metadata-token": tok}), timeout=2).read().decode()
    except Exception:
        return None


META = {"instance_id": imds("instance-id"), "instance_type": imds("instance-type"),
        "az": imds("placement/availability-zone"),
        "lifecycle": imds("instance-life-cycle") or "on-demand"}


def state_files():
    """Bootstrap timings (seconds) and markers, for the result/heartbeat."""
    out = {}
    for k in ("pull_s", "weights_s", "load_s", "driver", "ready_s", "boot"):
        try:
            v = open(f"{ST}/{k}").read().strip()
            out[k] = int(v) if v.isdigit() else v
        except OSError:
            pass
    try:  # when bootstrap started (instance boot done), for launch->boot timing
        out["bootstrap_ts"] = round(os.path.getmtime(f"{ST}/cap"), 1)
    except OSError:
        pass
    return out


def upload_log():
    """No inbound access to the box, so its log goes to S3: logs/<instance_id>.log."""
    try:
        txt = open("/var/log/evp-worker.log", "rb").read()[-400_000:]
        dl = subprocess.run(["docker", "logs", "--tail", "300", CNAME], capture_output=True)
        txt += f"\n===== docker logs {CNAME} (tail) =====\n".encode() + dl.stdout[-200_000:] + dl.stderr[-200_000:]
        s3.put_object(Bucket=B, Key=f"{P}logs/{META['instance_id']}.log", Body=txt)
    except Exception as e:
        log("log upload failed", e)


def heartbeat(state, **extra):
    body = dict(META, state=state, ts=time.time(), **state_files(), **extra)
    s3.put_object(Bucket=B, Key=P + "heartbeat.json", Body=json.dumps(body).encode(),
                  ContentType="application/json")
    upload_log()


def ping():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/ping", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def container_alive():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CNAME],
                       capture_output=True, text=True)
    return r.stdout.strip() == "true"


def shutdown(reason):
    log("shutting down:", reason)
    try:
        heartbeat("stopping", note=reason)
    except Exception as e:
        log("heartbeat failed", e)
    subprocess.run(["docker", "logs", "--tail", "200", CNAME])
    subprocess.run(["shutdown", "-h", "now"])
    sys.exit(0)


def wait_ready():
    t0 = time.time()
    while time.time() - t0 < CFG["ready_timeout_s"]:
        if ping():
            boot = os.path.getmtime(f"{ST}/cap")  # first bootstrap stage marker
            open(f"{ST}/ready_s", "w").write(str(int(time.time() - boot)))
            log("adapter /ping 200 after", int(time.time() - t0), "s of loading")
            return
        if not container_alive():
            shutdown("container exited while loading")
        heartbeat("loading")
        time.sleep(20)
    shutdown("adapter not ready within ready_timeout_s")


current = {"id": None, "body": None}


def spot_watch():
    """Spot interruption notice -> requeue the job in flight and stop."""
    while True:
        if imds("spot/instance-action"):
            jid = current["id"]
            log("spot interruption notice; requeue", jid)
            if jid:
                s3.put_object(Bucket=B, Key=f"{P}in/{jid}.json", Body=current["body"])
                s3.put_object(Bucket=B, Key=f"{P}claimed/{jid}.json",
                              Body=json.dumps({"requeued": True, "ts": time.time()}).encode())
            shutdown("spot interruption")
        time.sleep(5)


def _get(key):
    try:
        o = s3.get_object(Bucket=B, Key=key)
        return o["Body"].read(), o["ETag"]
    except s3.exceptions.NoSuchKey:
        return None, None


def _exists(key):
    try:
        s3.head_object(Bucket=B, Key=key)
        return True
    except Exception:
        return False


def _try_delete(key):
    """Best effort: the test profile (evp-video-builder) has no s3:DeleteObject; the
    controller deletes in/ objects it sees claimed, so the queue works either way."""
    try:
        s3.delete_object(Bucket=B, Key=key)
    except Exception:
        pass


def claim():
    """Claim the oldest unclaimed job with an S3 conditional write (If-None-Match), so two
    workers can never both claim it and the worker needs no DeleteObject permission.
    A claimed/<id>.json marked {"requeued": true} (spot interruption) is claimable again,
    via If-Match on its ETag."""
    r = s3.list_objects_v2(Bucket=B, Prefix=P + "in/", MaxKeys=50).get("Contents", [])
    for o in sorted(r, key=lambda o: o["LastModified"]):
        jid = o["Key"].rsplit("/", 1)[1].removesuffix(".json")
        if _exists(f"{P}out/{jid}.json") or _exists(f"{P}fail/{jid}.json"):
            _try_delete(o["Key"]); continue
        body, _ = _get(o["Key"])
        if body is None:
            continue
        ck = f"{P}claimed/{jid}.json"
        old, etag = _get(ck)
        mark = json.dumps({"instance_id": META["instance_id"], "ts": time.time(),
                           "request": json.loads(body)}).encode()
        try:
            if old is None:
                s3.put_object(Bucket=B, Key=ck, Body=mark, IfNoneMatch="*")
            elif json.loads(old).get("requeued"):
                s3.put_object(Bucket=B, Key=ck, Body=mark, IfMatch=etag)
            else:
                continue  # claimed by someone else
        except Exception as e:  # 412 PreconditionFailed: lost the race
            log("claim lost for", jid, type(e).__name__)
            continue
        _try_delete(o["Key"])
        return jid, body
    return None, None


CTYPES = {"mp4": "video/mp4", "png": "image/png", "flac": "audio/flac", "mp3": "audio/mpeg",
          "wav": "audio/wav", "jpg": "image/jpeg"}


def store_outputs(jid, res):
    """Move binary payloads out of the result JSON into S3 objects.

    Any top-level string key ending in "_b64", and the list under "images", is written
    as out/<id>.<n>.<ext> (n counts files across all keys; ext from config "outputs",
    e.g. {"video_b64": "mp4", "audio_b64": "flac", "images": "png"}; an audio result
    whose "format" says mp3 is stored as .mp3). The result lists the keys under
    "files" = {key: [s3 key, ...]}. Returns the total bytes written."""
    outputs = CFG.get("outputs", {})
    files, n, total = {}, 0, 0
    for k in list(res):
        v = res[k]
        if k.endswith("_b64") and isinstance(v, str):
            blobs = [v]
        elif k == "images" and isinstance(v, list):
            blobs = v
        else:
            continue
        ext = outputs.get(k, "bin")
        if k == "audio_b64" and res.get("format") in ("flac", "mp3"):
            ext = res["format"]
        res.pop(k)
        for b in blobs:
            data = base64.b64decode(b)
            key = f"{P}out/{jid}.{n}.{ext}"
            s3.put_object(Bucket=B, Key=key, Body=data,
                          ContentType=CTYPES.get(ext, "application/octet-stream"))
            files.setdefault(k, []).append(key)
            n += 1; total += len(data)
    res["files"] = files
    if "video_b64" in files:  # older controllers read video_key
        res["video_key"] = files["video_b64"][0]
    return total


def run(jid, body):
    req = json.loads(body)
    t0 = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{PORT}/invocations", data=body,
            headers={"Content-Type": "application/json"}), timeout=CFG["invoke_timeout_s"])
        res = json.loads(r.read())
        nbytes = store_outputs(jid, res)
        res.update(bytes=nbytes, worker_s=round(time.time() - t0, 1),
                   submitted_ts=req.get("_submitted_ts"), done_ts=time.time(),
                   instance=dict(META, **state_files()))
        s3.put_object(Bucket=B, Key=f"{P}out/{jid}.json", Body=json.dumps(res).encode())
        log("job", jid, "done in", res["worker_s"], "s (generation", res.get("seconds"), "s)")
    except Exception as e:
        detail = e.read().decode()[:2000] if hasattr(e, "read") else repr(e)
        s3.put_object(Bucket=B, Key=f"{P}fail/{jid}.json", Body=json.dumps(
            {"error": detail, "instance": META, "ts": time.time()}).encode())
        log("job", jid, "failed:", detail[:300])
    _try_delete(f"{P}claimed/{jid}.json")


def main():
    wait_ready()
    threading.Thread(target=spot_watch, daemon=True).start()
    idle_from = time.time()
    last_hb = 0
    while True:
        if not container_alive():
            shutdown("container exited")
        jid, body = claim()
        if jid:
            current.update(id=jid, body=body)
            heartbeat("busy", job=jid)
            run(jid, body)
            current.update(id=None, body=None)
            idle_from = time.time()
            heartbeat("ready", idle_since=idle_from)
            last_hb = time.time()
            continue
        idle = time.time() - idle_from
        if idle >= CFG["idle_shutdown_s"]:
            shutdown(f"idle {int(idle)} s")
        if time.time() - last_hb > 30:
            heartbeat("ready", idle_since=idle_from)
            last_hb = time.time()
        time.sleep(5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--heartbeat")
    ap.add_argument("--note")
    a = ap.parse_args()
    if a.heartbeat:
        heartbeat(a.heartbeat, **({"note": a.note} if a.note else {}))
    else:
        main()
