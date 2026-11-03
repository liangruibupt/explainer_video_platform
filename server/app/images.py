"""Stand-alone image generations for the 图像 page, stored under data/images/<id>/.

Each request gets a folder with req.json and state.json; a background thread waits for the
endpoint (which may be starting from 0) and writes 0.png, 1.png, ... when it is done.
"""
import json
import threading
import time

from . import image
from .pipeline import DATA

ROOT = DATA / "images"
RATIOS = ("16:9", "9:16", "1:1")


def _state(d, **kw):
    p = d / "state.json"
    s = json.loads(p.read_text()) if p.exists() else {}
    s.update(kw, updated=time.time())
    p.write_text(json.dumps(s, ensure_ascii=False))
    return s


def create(img_id: str, req: dict) -> dict:
    d = ROOT / img_id
    d.mkdir(parents=True, exist_ok=False)
    (d / "req.json").write_text(json.dumps(req, ensure_ascii=False))
    s = _state(d, id=img_id, status="queued", created=time.time(), files=[], error=None, **req)
    threading.Thread(target=_run, args=(img_id,), daemon=True).start()
    return s


def _run(img_id: str) -> None:
    d = ROOT / img_id
    req = json.loads((d / "req.json").read_text())
    _state(d, status="running")
    try:
        t0 = time.time()
        pngs = image.generate(req["prompt"], ratio=req["ratio"], mode=req["mode"],
                              seed=req["seed"], n=req["n"],
                              on_wait=lambda sec: _state(d, waited=sec))
        names = []
        for i, png in enumerate(pngs):
            (d / f"{i}.png").write_bytes(png)
            names.append(f"{i}.png")
        _state(d, status="done", files=names, seconds=round(time.time() - t0))
    except Exception as e:  # noqa: BLE001 - shown on the page
        _state(d, status="failed", error=str(e)[:500])


def get(img_id: str) -> dict:
    p = ROOT / img_id / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def list_all(limit: int = 40) -> list:
    if not ROOT.exists():
        return []
    out = [json.loads(p.read_text()) for p in ROOT.glob("*/state.json")]
    return sorted(out, key=lambda s: s.get("created", 0), reverse=True)[:limit]
