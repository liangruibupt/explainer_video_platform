"""Feedback records under data/feedback/ (docs/PHASE5_CONTRACTS.md, Data).

items/<fb_id>.json, analyses/<an_id>.json, proposals/<pr_id>.json. Every write goes to a
temp file in the same folder and is renamed over the target, so a reader never sees a
half-written file. update() / add_history() hold a per-item lock around read-modify-write.
"""
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from ..pipeline import DATA

ROOT = DATA / "feedback"
ITEMS = ROOT / "items"
ANALYSES = ROOT / "analyses"
PROPOSALS = ROOT / "proposals"
KINDS = ("job", "workflow", "image", "video", "music", "avatar")
REASONS = ("画面", "声音", "字幕", "内容", "节奏", "其他")
STATUSES = ("new", "analyzing", "rerun", "clustered", "proposed", "merged", "resolved",
            "dismissed")
MAX_TEXT = 500
_ID_RE = re.compile(r"^(fb|an|pr)_\d{8}-\d{6}-[0-9a-f]{6}$")
_locks = {}
_guard = threading.Lock()


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def valid_id(x: str, prefix: str = "") -> bool:
    return isinstance(x, str) and bool(_ID_RE.match(x)) and (not prefix or
                                                            x.startswith(prefix + "_"))


def lock(obj_id: str) -> threading.RLock:
    with _guard:
        return _locks.setdefault(obj_id, threading.RLock())


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(path)


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _path(folder: Path, obj_id: str, prefix: str) -> Path:
    if not valid_id(obj_id, prefix):
        raise ValueError(f"bad id: {obj_id!r}")
    return folder / f"{obj_id}.json"


# ---------------------------------------------------------------- items

def clean_target(target) -> dict:
    if not isinstance(target, dict) or target.get("kind") not in KINDS:
        raise ValueError(f"target.kind must be one of {KINDS}")
    tid = str(target.get("id") or "").strip()
    if not tid or not re.match(r"^[A-Za-z0-9._-]{1,100}$", tid):
        raise ValueError("target.id is required (letters, digits, . _ -)")
    out = {"kind": target["kind"], "id": tid}
    if target.get("node_id"):
        nid = str(target["node_id"]).strip()
        if not re.match(r"^[A-Za-z0-9._-]{1,100}$", nid):
            raise ValueError("bad target.node_id")
        out["node_id"] = nid
    return out


def clean_reasons(reasons) -> list:
    if isinstance(reasons, str):
        reasons = [reasons]
    out = [r for r in (reasons or []) if r in REASONS]
    out = list(dict.fromkeys(out))
    if not out:
        raise ValueError(f"reasons must include at least one of {REASONS}")
    return out


def create(target, reasons, text="", snapshot=None) -> dict:
    """New feedback item. snapshot defaults to snapshot.take(target) (record copy, QC,
    key file paths, skill version); a failing snapshot is recorded, never raised."""
    target = clean_target(target)
    reasons = clean_reasons(reasons)
    text = str(text or "")[:MAX_TEXT]
    if snapshot is None:
        from .qc import snapshot as take
        try:
            snapshot = take(target)
        except Exception as e:  # noqa: BLE001 - feedback must still be recorded
            from . import skill_info
            snapshot = {**skill_info(), "record": None, "qc": {}, "files": {},
                        "error": str(e)[:300]}
    now = time.time()
    item = {"id": new_id("fb"), "created": now, "target": target, "reasons": reasons,
            "text": text, "snapshot": snapshot, "status": "new", "analysis_id": None,
            "proposal_id": None, "resolution": None,
            "history": [{"ts": now, "event": "created", "detail": "、".join(reasons)}]}
    _write(_path(ITEMS, item["id"], "fb"), item)
    return item


def get(fb_id: str):
    try:
        return _read(_path(ITEMS, fb_id, "fb"))
    except ValueError:
        return None


def list_items(status: str = "", limit: int = 200) -> list:
    if not ITEMS.exists():
        return []
    out = []
    for p in ITEMS.glob("fb_*.json"):
        it = _read(p)
        if it and (not status or it.get("status") == status):
            out.append(it)
    out.sort(key=lambda x: x.get("created", 0), reverse=True)
    return out[:max(0, int(limit))]


def update(fb_id: str, fn) -> dict:
    """Read, fn(item) mutates it in place (return value ignored), write. Raises KeyError
    when the item does not exist."""
    p = _path(ITEMS, fb_id, "fb")
    with lock(fb_id):
        item = _read(p)
        if item is None:
            raise KeyError(fb_id)
        fn(item)
        item["id"] = fb_id
        _write(p, item)
        return item


def add_history(fb_id: str, event: str, detail="") -> dict:
    def add(item):
        item.setdefault("history", []).append({"ts": time.time(), "event": str(event),
                                               "detail": detail})
    return update(fb_id, add)


def set_status(fb_id: str, status: str, detail="") -> dict:
    if status not in STATUSES:
        raise ValueError(f"bad status {status!r}")

    def f(item):
        item["status"] = status
        item.setdefault("history", []).append({"ts": time.time(), "event": status,
                                               "detail": detail})
    return update(fb_id, f)


# ---------------------------------------------------------------- analyses / proposals

def _save(folder, prefix, obj) -> dict:
    obj = dict(obj)
    if not obj.get("id"):
        obj["id"] = new_id(prefix)
    obj.setdefault("created", time.time())
    with lock(obj["id"]):
        _write(_path(folder, obj["id"], prefix), obj)
    return obj


def save_analysis(analysis: dict) -> dict:
    return _save(ANALYSES, "an", analysis)


def get_analysis(an_id: str):
    try:
        return _read(_path(ANALYSES, an_id, "an"))
    except ValueError:
        return None


def list_analyses(limit: int = 500) -> list:
    if not ANALYSES.exists():
        return []
    out = [a for a in (_read(p) for p in ANALYSES.glob("an_*.json")) if a]
    out.sort(key=lambda x: x.get("created", 0), reverse=True)
    return out[:limit]


def save_proposal(proposal: dict) -> dict:
    return _save(PROPOSALS, "pr", proposal)


def get_proposal(pr_id: str):
    try:
        return _read(_path(PROPOSALS, pr_id, "pr"))
    except ValueError:
        return None


def list_proposals(state: str = "", limit: int = 200) -> list:
    if not PROPOSALS.exists():
        return []
    out = [p for p in (_read(f) for f in PROPOSALS.glob("pr_*.json"))
           if p and (not state or p.get("state") == state)]
    out.sort(key=lambda x: x.get("created", 0), reverse=True)
    return out[:limit]
