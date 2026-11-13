#!/usr/bin/env python3
"""Live check of the re-voice API on a local studio (127.0.0.1 only).
Usage: python revoice_live.py PORT JOB_ID start|apply|undo [N]
  start  POST /revoice with scripts/dev/revoice_d48e69.json, poll until done, print the record
  apply  POST /revoice/N/apply with versions/v2.project.json, save the result next to the take
  undo   POST /revoice/N/undo with the applied project
"""
import json
import sys
import time
from pathlib import Path

import httpx

port, jid, op = sys.argv[1], sys.argv[2], sys.argv[3]
base = f"http://127.0.0.1:{port}/api/jobs/{jid}/revoice"
here = Path(__file__).resolve().parent
data = Path(__file__).resolve().parents[2] / "data" / "jobs" / jid
c = httpx.Client(timeout=600)
if op == "start":
    narr = json.loads((here / "revoice_d48e69.json").read_text())
    t0 = time.time()
    r = c.post(base, json={"narrations": narr})
    print(r.status_code, r.json().get("status"))
    n = r.json()["n"]
    while (rec := c.get(f"{base}/{n}").json())["status"] in ("queued", "running"):
        time.sleep(1)
    print(f"wall {time.time() - t0:.1f}s")
    print(json.dumps(rec, ensure_ascii=False, indent=1))
elif op == "apply":
    n = sys.argv[4]
    proj = json.loads((data / "versions" / "v2.project.json").read_text())
    r = c.post(f"{base}/{n}/apply", json={"project": proj})
    print(r.status_code)
    out = r.json()
    (data / "remotion" / ".voice" / f"applied-{n}.json").write_text(
        json.dumps(out, ensure_ascii=False))
    print(json.dumps({k: v for k, v in out.items() if k != "project"}, ensure_ascii=False, indent=1))
elif op == "undo":
    n = sys.argv[4]
    out = json.loads((data / "remotion" / ".voice" / f"applied-{n}.json").read_text())
    r = c.post(f"{base}/{n}/undo", json={"project": out["project"]})
    print(r.status_code, json.dumps({k: v for k, v in r.json().items() if k != "project"},
                                    ensure_ascii=False))
    (data / "remotion" / ".voice" / f"undone-{n}.json").write_text(
        json.dumps(r.json(), ensure_ascii=False))
