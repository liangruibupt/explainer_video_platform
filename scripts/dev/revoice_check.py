#!/usr/bin/env python3
"""Dev check of a take against take 0 (offset model, sfx, captions).
Usage (from server/): EVP_DATA=<copy> python ../scripts/dev/revoice_check.py JOB_ID N
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from app import pipeline  # noqa: E402
from app.motion import revoice  # noqa: E402

jid, n = sys.argv[1], int(sys.argv[2])
d = pipeline.job_dir(jid)
t0, tk = revoice.load_take(d, 0), revoice.load_take(d, n)
nar = revoice.script("narrate")
out = Path(t0["dir"]) / ".." / ".." / "check0.wav"
clips = [str(t0["dir"] / "audio" / f"s{i:02d}.mp3") for i in range(len(t0["scenes"]))]
nar.concat(clips, t0["meta"]["offsets_ms"], t0["meta"]["total_ms"], str(out), pipeline.FFMPEG)
print("take0 offsets reproduce job narration.wav:", out.read_bytes() == (d / "narration.wav").read_bytes())
out.unlink()
print("take0 offsets", t0["meta"]["offsets_ms"])
print("take  offsets", tk["meta"]["offsets_ms"])
print("scene from (frames->ms)", [round(s["from"] * 1000 / 30) for s in tk["timeline"]["scenes"]])
oa, na = t0["audio"]["sfx"], tk["audio"]["sfx"]
print("sfx", [(a["from"], b["from"]) for a, b in zip(oa, na) if a["from"] != b["from"]])
oc = {(c["scene"], c["text"]): c for c in t0["captions"]}
for c in tk["captions"]:
    o = oc.get((c["scene"], c["text"]))
    tag = "same" if o and o["from"] == c["from"] and o["chars"] == c["chars"] else (
        f"shift {c['from'] - o['from']}" if o else "NEW")
    if tag != "same":
        print(c["scene"], c["from"], c["duration"], c["text"], tag)
