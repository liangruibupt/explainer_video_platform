"""Chat sessions on disk: data/chat/<id>.json, one file per session."""
import json
import re
import threading
import time
import uuid
from pathlib import Path

from .. import pipeline

MODES = ("auto", "image", "video", "music", "explainer")
ID_RE = re.compile(r"^chat_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_locks: dict = {}
_guard = threading.Lock()


def root() -> Path:
    # read pipeline.DATA at call time so tests can point it elsewhere
    return Path(pipeline.DATA) / "chat"


def lock(sid: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(sid, threading.Lock())


def path(sid: str) -> Path:
    if not ID_RE.fullmatch(sid or ""):
        raise KeyError(sid)
    return root() / f"{sid}.json"


def create(mode: str = "auto", allow_web: bool = False, project_id: str = "") -> dict:
    now = time.time()
    sid = time.strftime("chat_%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    s = {"id": sid, "title": "", "created": now, "updated": now,
         "mode": mode if mode in MODES else "auto", "allow_web": bool(allow_web),
         "project_id": project_id or None,
         "state": "collecting",      # collecting -> running -> done | failed; idle after errors
         "intent": None, "questions": 0, "assumed": [],
         "workflow": None, "run_id": None, "runs": [], "announced": {},
         "messages": []}
    save(s)
    return s


def save(s: dict) -> None:
    s["updated"] = time.time()
    p = path(s["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    tmp.replace(p)


def get(sid: str) -> dict | None:
    try:
        p = path(sid)
    except KeyError:
        return None
    return json.loads(p.read_text()) if p.exists() else None


def list_sessions(limit: int = 100) -> list:
    if not root().exists():
        return []
    out = []
    for p in root().glob("chat_*.json"):
        try:
            s = json.loads(p.read_text())
        except ValueError:
            continue
        last = s["messages"][-1]["text"][:80] if s.get("messages") else ""
        out.append({k: s.get(k) for k in ("id", "title", "created", "updated", "mode",
                                          "allow_web", "state", "intent", "run_id",
                                          "project_id")} | {"last": last})
    return sorted(out, key=lambda s: s["updated"] or 0, reverse=True)[:limit]


def add_message(s: dict, role: str, text: str, kind: str = "text", **extra) -> dict:
    m = {"role": role, "text": text, "kind": kind, "t": time.time()} | extra
    s["messages"].append(m)
    if role == "user" and not s.get("title"):
        s["title"] = text.strip().replace("\n", " ")[:40]
    return m
