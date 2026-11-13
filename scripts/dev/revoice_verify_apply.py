#!/usr/bin/env python3
"""Check an applied take's project against v2 (run from server/ with EVP_DATA set).
Usage: python ../scripts/dev/revoice_verify_apply.py JOB_ID N
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from app import pipeline  # noqa: E402
from app.motion import revoice, versions  # noqa: E402

jid, n = sys.argv[1], int(sys.argv[2])
d = pipeline.job_dir(jid)
out = json.loads((d / "remotion" / ".voice" / f"applied-{n}.json").read_text())
p = out["project"]
v2 = json.loads((d / "versions" / "v2.project.json").read_text())
tl, audio = versions._load_job_data(d / "remotion")
versions.validate(p, tl)
print("validates against the live (new) timeline: yes")
print("source", p["source"], "| key", revoice.manifest_key(tl), "| match", p["source"] == revoice.manifest_key(tl))
clips = lambda q: {c["id"]: c for t in q["tracks"] for c in t["clips"]}
a, b = clips(v2), clips(p)
s2 = next(c for c in b.values() if c["cardId"].endswith("scene-growth_math"))
print("S2 segments", repr(s2["props"]["segments"]))
props_same = all(b[k]["props"] == a[k]["props"] for k in a if k in b and not a[k]["cardId"].endswith("caption"))
print("non-caption clip props unchanged:", props_same)
for s in tl["scenes"]:
    c = next(c for c in b.values() if c["cardId"] == f"proj:shot:scene-{s['id']}")
    o = next(c for c in a.values() if c["cardId"] == f"proj:shot:scene-{s['id']}")
    print(f"  {s['id']:17s} tl {s['from']:5d}+{s['duration']:4d}  clip {c['start']:5d}+{c['duration']:4d}  (v2 {o['start']}+{o['duration']})")
sfx_a = sorted((c["start"], c["id"]) for c in a.values() if c["cardId"] == "audio-clip" and "sfx" in c["props"]["file"])
sfx_b = {c["id"]: c["start"] for c in b.values() if c["cardId"] == "audio-clip" and "sfx" in c["props"]["file"]}
print("sfx moved:", [(st, sfx_b[i]) for st, i in sfx_a if sfx_b[i] != st])
voice = [c for c in b.values() if versions._is_voice(c, audio["voice"])]
print("voice clip", [(c["start"], c["duration"]) for c in voice])
caps = sorted((c for c in b.values() if c["cardId"] == revoice.CAPTION_CARD), key=lambda c: c["start"])
for sid in ("token_engine", "migration_engine", "recap"):
    s = next(x for x in tl["scenes"] if x["id"] == sid)
    mine = [c for c in caps if s["from"] <= c["start"] < s["from"] + s["duration"]]
    inside = all(c["start"] + c["duration"] <= s["from"] + s["duration"] for c in mine)
    print(f"  {sid}: inside={inside}", " / ".join(f"{c['start']}+{c['duration']} {c['props']['text']}" for c in mine))
print("caption style", {k: v for k, v in caps[0]["props"].items() if k not in ("text", "chars")})
locked, warns = versions.apply_voice_lock(p, tl, audio)
print("voice lock warnings", warns, "drift warnings", versions.drift_warnings(locked, tl))
