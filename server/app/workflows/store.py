"""Saved workflows (data/workflows/<id>.json) and templates (templates/*.json, in git)."""
import json
import re
import time
from pathlib import Path

from ..pipeline import DATA

TEMPLATES = Path(__file__).resolve().parent / "templates"
SAVED = DATA / "workflows"
ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def templates() -> list:
    out = []
    for p in sorted(TEMPLATES.glob("*.json")):
        wf = json.loads(p.read_text())
        wf["template"] = True
        out.append(wf)
    return out


def template(wf_id: str):
    return next((t for t in templates() if t["id"] == wf_id), None)


def safe_id(wf_id: str) -> str:
    wf_id = re.sub(r"[^a-z0-9_-]", "_", str(wf_id or "").lower())[:64].strip("_")
    return wf_id or "wf_" + time.strftime("%Y%m%d%H%M%S")


def save(wf: dict) -> dict:
    wf = dict(wf)
    wf.pop("template", None)
    wf["id"] = safe_id(wf.get("id"))
    wf["saved"] = time.time()
    SAVED.mkdir(parents=True, exist_ok=True)
    (SAVED / f"{wf['id']}.json").write_text(json.dumps(wf, ensure_ascii=False, indent=1))
    return wf


def get(wf_id: str):
    if not ID_RE.match(wf_id or ""):
        return None
    p = SAVED / f"{wf_id}.json"
    return json.loads(p.read_text()) if p.exists() else template(wf_id)


def list_saved() -> list:
    if not SAVED.exists():
        return []
    out = [json.loads(p.read_text()) for p in SAVED.glob("*.json")]
    return sorted(out, key=lambda w: w.get("saved", 0), reverse=True)


def delete(wf_id: str) -> bool:
    p = SAVED / f"{wf_id}.json"
    if ID_RE.match(wf_id or "") and p.exists():
        p.unlink()
        return True
    return False
