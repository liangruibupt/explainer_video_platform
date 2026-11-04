#!/usr/bin/env python3
"""CLI for the self-managed EC2 GPU backend (server/app/gpu_ec2.py).

  ctl.py [--module M] plan               capacity order with prices
  ctl.py [--module M] status             none|launching|booting|loading|ready|busy
  ctl.py [--module M] submit-smoke [--seed N] [--out PATH] [--no-wait]
                                         video: 768p 16:9 5 s t2va West Lake clip
                                         image: 16:9 t2i Song-dynasty landscape (40 steps)
                                         music: instrumental guqin + xiao, cot full
  ctl.py [--module M] wait ID [--out PATH]
  ctl.py [--module M] stop               terminate the module's instance

M is video (default), image or music. --out is a path prefix for image/music
(files are saved as PREFIX.<n>.<ext>) and the MP4 path for video.
Credentials come from the machine's AWS profile or role. Nothing secret is stored here.
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from app.gpu_ec2 import GpuEC2, NoCapacity  # noqa: E402

SMOKE = {
    "video": {"mode": "t2va", "aspect": "16:9", "short_edge": 768, "duration": 5.0, "steps": 8,
              "prompt": "西湖清晨，薄雾笼罩湖面，远处苏堤的柳树和石拱桥，一只小船缓缓划过，电影感，平稳镜头"},
    "image": {"mode": "t2i", "ratio": "16:9", "steps": 40, "n": 1,
              "prompt": "北宋山水长卷风格，远山层叠，江面开阔，一叶渔舟，淡墨设色，绢本质感，画面中没有任何文字"},
    "music": {"style": "Chinese classical instrumental, guqin and xiao flute, slow, ethereal, ambient, "
                       "no drums, instrumental, no vocals",
              "lyrics": "[Intro]\n\n[Instrumental]\n\n[Outro]", "cot": "full"},
}
EXT = {"video_b64": "mp4", "images": "png", "audio_b64": "flac"}


def log_path(module):
    return ROOT / "data" / f"{module}_smoke" / "ctl.log"


def note(g, msg):
    p = log_path(g.module)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg
    print(line, flush=True)
    with p.open("a") as f:
        f.write(line + "\n")


def plan(g):
    pr = g.prices()
    print(f"module {g.module} region {g.region} (tried in this order):")
    for i, o in enumerate(g.cfg["capacity"], 1):
        p = pr.get((o["type"], o["market"], o["az"]), float("nan"))
        print(f"  {i}. {o['market']:9} {o['type']:12} {o['az']}  ${p:.2f}/h")


def save(g, r, out):
    """Write every output file of a result; returns the local paths."""
    if g.module == "video":
        return [g.fetch_video(r, out)]
    paths = []
    for key, keys in (r.get("files") or {}).items():
        for i, data in enumerate(g.fetch(r, key)):
            p = f"{out}.{i}.{keys[i].rsplit('.', 1)[1]}"
            Path(p).write_bytes(data); paths.append(p)
    return paths


def wait(g, jid, out=None, timeout=3600):
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        r = g.poll(jid)
        if r is not None:
            if not r["ok"]:
                note(g, f"job {jid} FAILED: {json.dumps(r, ensure_ascii=False)[:1500]}")
                return r
            if out:
                r["local"] = save(g, r, out)
            r["wall_s"] = round(time.time() - r["submitted_ts"], 1) if r.get("submitted_ts") else None
            r.pop("abc", None)  # long score text; kept in the S3 result JSON
            note(g, f"job {jid} done: {json.dumps(r, ensure_ascii=False)}")
            return r
        st = g.status()
        if st["state"] != last:
            hb = st.get("heartbeat") if isinstance(st.get("heartbeat"), dict) else {}
            note(g, f"job {jid} waiting; backend state={st['state']} "
                    f"{st.get('instance_type', '')} {st.get('az', '')} {st.get('lifecycle', '')} "
                    f"{hb.get('note', '')}")
            last = st["state"]
        time.sleep(15)
    note(g, f"job {jid} still pending after {timeout} s")


def submit_smoke(g, seed, out, no_wait):
    req = dict(SMOKE[g.module], seed=seed)
    try:
        jid = g.submit(req)
    except NoCapacity as e:
        note(g, f"NO CAPACITY: {e}")
        sys.exit(2)
    note(g, f"submitted {jid} seed={seed}; launch={json.dumps(g.last_launch)}")
    if no_wait:
        print(jid); return
    wait(g, jid, out)


def default_out(module):
    d = ROOT / "data" / f"{module}_smoke"
    return str(d / "smoke_0.mp4") if module == "video" else str(d / "ec2_smoke")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "status", "submit-smoke", "wait", "stop"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--module", default="video", choices=sorted(SMOKE))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    g = GpuEC2(a.module)
    out = a.out or default_out(a.module)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if a.cmd == "plan":
        plan(g)
    elif a.cmd == "status":
        print(json.dumps(g.status(), default=str, ensure_ascii=False, indent=1))
    elif a.cmd == "submit-smoke":
        submit_smoke(g, a.seed, out, a.no_wait)
    elif a.cmd == "wait":
        wait(g, a.id or sys.exit("wait needs a job id"), out)
    elif a.cmd == "stop":
        note(g, f"stop: terminated {g.stop()}")
