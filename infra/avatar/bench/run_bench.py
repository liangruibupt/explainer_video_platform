#!/usr/bin/env python3
"""Run the avatar comparison matrix through the EC2 worker queue (GpuEC2, module avatar).

  run_bench.py submit OUTDIR [--only NAME,...]   queue the runs (one GPU, served in order)
  run_bench.py collect OUTDIR                    poll, save mp4 + result json per run

Inputs: OUTDIR/img/p169.png, p916.png (image module) and OUTDIR/audio/*.mp3 (make_audio.py).
Runs per model: 15 s clip at 480 fast / 720 fast / 480 full, 9:16 once, 45 s and 90 s at
480 fast. Results go to OUTDIR/runs/<name>.{mp4,json}; runs.json maps names to job ids.
"""
import argparse, base64, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "server"))
from app.gpu_ec2 import GpuEC2  # noqa: E402

PROMPT = ("A young woman shop presenter in a bright supermarket aisle talks to the camera, "
          "smiling, natural hand gestures, steady camera")
MATRIX = []
# Order matters (one GPU, FIFO): each model's runs are grouped so the model loads once,
# and the most important runs (15 s fast, 90 s long-video) come first.
for m in ("infinitetalk", "longcat"):
    MATRIX += [
        (f"{m}_15s_480_fast", m, "p169", "pitch15", "480", True),
        (f"{m}_90s_480_fast", m, "p169", "live90", "480", True),
        (f"{m}_15s_480_fast_916", m, "p916", "pitch15", "480", True),
        (f"{m}_45s_480_fast", m, "p169", "promo45", "480", True),
        (f"{m}_15s_720_fast", m, "p169", "pitch15", "720", True),
        (f"{m}_15s_480_full", m, "p169", "pitch15", "480", False),
    ]


def b64(p):
    return base64.b64encode(Path(p).read_bytes()).decode()


def submit(out, only):
    g = GpuEC2("avatar")
    jobs = json.loads((out / "runs.json").read_text()) if (out / "runs.json").exists() else {}
    for name, model, img, aud, size, fast in MATRIX:
        if (only and name not in only) or name in jobs:
            continue
        req = {"model": model, "image_b64": b64(out / "img" / f"{img}.png"),
               "audio_b64": b64(out / "audio" / f"{aud}.mp3"), "prompt": PROMPT, "size": size,
               "fast": fast, "seed": 42, "_bench": name}
        jobs[name] = g.submit(req)
        print("submitted", name, jobs[name], flush=True)
        (out / "runs.json").write_text(json.dumps(jobs, indent=1))
        time.sleep(1.1)  # job ids sort by time: keeps the queue in matrix order


def collect(out):
    g = GpuEC2("avatar")
    jobs = json.loads((out / "runs.json").read_text())
    (out / "runs").mkdir(exist_ok=True)
    for name, jid in jobs.items():
        if (out / "runs" / f"{name}.json").exists():
            continue
        r = g.poll(jid)
        if r is None:
            print("pending", name); continue
        if r["ok"]:
            (out / "runs" / f"{name}.mp4").write_bytes(g.fetch(r, "video_b64")[0])
        (out / "runs" / f"{name}.json").write_text(json.dumps(r, indent=1, default=str))
        print("done" if r["ok"] else "FAILED", name, r.get("render_s"), r.get("seconds"),
              r.get("peak_vram_gb"), str(r.get("error", ""))[:300])
    print("state", g.status().get("state"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "collect"])
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    o = Path(a.out)
    submit(o, set(filter(None, a.only.split(",")))) if a.cmd == "submit" else collect(o)
