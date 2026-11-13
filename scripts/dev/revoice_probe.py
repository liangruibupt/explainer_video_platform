#!/usr/bin/env python3
"""Dev probe for server/app/motion/revoice.py: run one re-voice synchronously on a job copy.
Usage (from server/): EVP_DATA=<copy> python ../scripts/dev/revoice_probe.py JOB_ID narrations.json
Prints the take record. Calls Polly + faster-whisper for real.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from app import pipeline  # noqa: E402
from app.motion import revoice  # noqa: E402

jid, narr = sys.argv[1], json.loads(Path(sys.argv[2]).read_text())
d = pipeline.job_dir(jid)
rec = revoice.submit(jid, d, narr, start=False)
revoice.run(jid, d, rec["n"])
print(json.dumps(revoice.get(d, rec["n"]), ensure_ascii=False, indent=1))
