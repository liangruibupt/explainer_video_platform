#!/usr/bin/env python3
"""CLI for the self-managed EC2 GPU backend (server/app/gpu_ec2.py).

  ctl.py [--module video] plan               capacity order with prices
  ctl.py [--module video] status             none|launching|booting|loading|ready|busy
  ctl.py [--module video] submit-smoke [--seed N] [--out PATH] [--no-wait]
                                             768p 16:9 5 s t2va West Lake clip
  ctl.py [--module video] wait ID [--out PATH]
  ctl.py [--module video] stop               terminate the module's instance

Credentials come from the machine's AWS profile or role. Nothing secret is stored here.
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from app.gpu_ec2 import GpuEC2, NoCapacity  # noqa: E402

PROMPT = "西湖清晨，薄雾笼罩湖面，远处苏堤的柳树和石拱桥，一只小船缓缓划过，电影感，平稳镜头"
LOG = ROOT / "data" / "video_smoke" / "ctl.log"


def note(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def plan(g):
    pr = g.prices()
    print(f"module {g.module} region {g.region} (tried in this order):")
    for i, o in enumerate(g.cfg["capacity"], 1):
        p = pr.get((o["type"], o["market"], o["az"]), float("nan"))
        print(f"  {i}. {o['market']:9} {o['type']:12} {o['az']}  ${p:.2f}/h")


def wait(g, jid, out=None, timeout=3600):
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        r = g.poll(jid)
        if r is not None:
            if not r["ok"]:
                note(f"job {jid} FAILED: {json.dumps(r, ensure_ascii=False)[:1500]}")
                return r
            if out:
                g.fetch_video(r, out)
            r["wall_s"] = round(time.time() - r.get("submitted_ts", t0), 1) if r.get("submitted_ts") else None
            note(f"job {jid} done: {json.dumps(r, ensure_ascii=False)}")
            return r
        st = g.status()
        if st["state"] != last:
            note(f"job {jid} waiting; backend state={st['state']} "
                 f"{st.get('instance_type', '')} {st.get('az', '')} {st.get('lifecycle', '')} "
                 f"{(st.get('heartbeat') or {}).get('note', '') if isinstance(st.get('heartbeat'), dict) else ''}")
            last = st["state"]
        time.sleep(15)
    note(f"job {jid} still pending after {timeout} s")


def submit_smoke(g, seed, out, no_wait):
    req = {"mode": "t2va", "aspect": "16:9", "short_edge": 768, "duration": 5.0, "steps": 8,
           "seed": seed, "prompt": PROMPT}
    try:
        jid = g.submit(req)
    except NoCapacity as e:
        note(f"NO CAPACITY: {e}")
        sys.exit(2)
    note(f"submitted {jid} seed={seed}; launch={json.dumps(g.last_launch)}")
    if no_wait:
        print(jid); return
    wait(g, jid, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "status", "submit-smoke", "wait", "stop"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--module", default="video")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    g = GpuEC2(a.module)
    if a.cmd == "plan":
        plan(g)
    elif a.cmd == "status":
        print(json.dumps(g.status(), default=str, ensure_ascii=False, indent=1))
    elif a.cmd == "submit-smoke":
        submit_smoke(g, a.seed, a.out or str(ROOT / "data" / "video_smoke" / "smoke_0.mp4"), a.no_wait)
    elif a.cmd == "wait":
        wait(g, a.id or sys.exit("wait needs a job id"), a.out)
    elif a.cmd == "stop":
        note(f"stop: terminated {g.stop()}")
