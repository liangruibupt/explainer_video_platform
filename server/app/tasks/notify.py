"""Notifications: an append-only list in data/tasks/notifications.jsonl.

notify(title, body, url) is the one call other parts use (docs/PHASE4_CONTRACTS.md). The
dashboard reads the list (in-app bell) and, when data/settings/notifications.json says
browser: true, also shows a browser Notification for new unread items.
"""
import json
import threading
import time
import uuid

from ..pipeline import DATA

ROOT = DATA / "tasks"
FILE = ROOT / "notifications.jsonl"
SETTINGS = DATA / "settings" / "notifications.json"
DEFAULTS = {"in_app": True, "browser": False}
KEEP = 500  # older notifications are dropped when the list is rewritten
_lock = threading.Lock()


def settings() -> dict:
    s = dict(DEFAULTS)
    try:
        s.update({k: bool(v) for k, v in json.loads(SETTINGS.read_text()).items()
                  if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        pass
    return s


def _read() -> list:
    if not FILE.exists():
        return []
    out = []
    for line in FILE.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _rewrite(items: list) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in items[-KEEP:]))
    tmp.replace(FILE)


def notify(title: str, body: str = "", url: str = "", level: str = "info") -> dict:
    n = {"id": uuid.uuid4().hex[:12], "t": time.time(), "title": str(title)[:200],
         "body": str(body)[:1000], "url": str(url)[:300],
         "level": level if level in ("info", "success", "warning", "error") else "info",
         "read": False}
    with _lock:
        ROOT.mkdir(parents=True, exist_ok=True)
        with FILE.open("a") as f:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    return n


def list_all(unread: bool = False, limit: int = 100) -> list:
    with _lock:
        items = _read()
    if unread:
        items = [n for n in items if not n.get("read")]
    return sorted(items, key=lambda n: n.get("t", 0), reverse=True)[:limit]


def mark_read(ids=None, all_: bool = False) -> int:
    ids = set(ids or [])
    with _lock:
        items = _read()
        changed = 0
        for n in items:
            if not n.get("read") and (all_ or n.get("id") in ids):
                n["read"] = True
                changed += 1
        if changed:
            _rewrite(items)
    return changed
