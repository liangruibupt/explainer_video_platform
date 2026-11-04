"""Background watcher: notifies once when a task becomes done / failed / waiting_review.

The last status seen per task is kept in data/tasks/seen.json, so a restart does not
re-notify. On the very first scan (no seen.json yet) everything already finished is
recorded silently: only changes after the studio starts watching produce notifications.
"""
import json
import logging
import os
import threading
import time

from . import collect
from .notify import ROOT, notify

SEEN = ROOT / "seen.json"
INTERVAL = 10
NOTIFY_ON = ("done", "failed", "waiting_review")
KIND_LABEL = {"job": "讲解视频", "image": "图像", "video": "AI 视频", "music": "音乐",
              "workflow": "工作流"}
STATUS_LABEL = {"done": "已完成", "failed": "失败", "waiting_review": "待审核"}
LEVEL = {"done": "success", "failed": "error", "waiting_review": "warning"}
log = logging.getLogger(__name__)
_lock = threading.Lock()
_thread = None
_stop = threading.Event()


def _load():
    try:
        return json.loads(SEEN.read_text())
    except (OSError, ValueError):
        return None


def _save(seen: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = SEEN.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, ensure_ascii=False))
    tmp.replace(SEEN)


def _key(item) -> str:
    return f"{item['kind']}:{item['id']}"


def _sig(item) -> str:
    """Status plus the waiting node ids, so a second review node in the same run notifies."""
    if item["status"] == "waiting_review":
        return "waiting_review:" + ",".join(sorted(r["node_id"] for r in item["reviews"]))
    return item["status"]


def _message(item):
    kind = KIND_LABEL.get(item["kind"], item["kind"])
    st = item["status"]
    title = f"{kind}{STATUS_LABEL[st]}：{item['title']}"
    if st == "failed":
        body = item.get("error") or ""
    elif st == "waiting_review":
        body = "节点 " + "、".join(r["node_id"] for r in item["reviews"]) + " 等待审核"
    else:
        body = ""
    url = "/tasks?tab=review" if st == "waiting_review" else item["url"]
    return title, body, url


def scan() -> list:
    """One pass: returns the notifications sent."""
    with _lock:
        seen = _load()
        first = seen is None
        seen = seen or {}
        sent = []
        for item in collect.collect():
            k, sig = _key(item), _sig(item)
            if seen.get(k) == sig:
                continue
            seen[k] = sig
            if first or item["status"] not in NOTIFY_ON:
                continue
            if item["status"] == "waiting_review" and not item["reviews"]:
                continue
            sent.append(notify(*_message(item), level=LEVEL[item["status"]]))
        _save(seen)
        return sent


def _loop():
    while not _stop.is_set():
        try:
            scan()
        except Exception:  # noqa: BLE001 - keep watching
            log.exception("task watcher scan failed")
        _stop.wait(INTERVAL)


def start() -> bool:
    """Start the watcher thread once per process; returns True if this call started it."""
    global _thread
    if os.environ.get("EVP_TASK_WATCHER", "1") == "0":
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="evp-task-watcher", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    """Stop the watcher thread (tests)."""
    global _thread
    _stop.set()
    t = _thread
    if t is not None:
        t.join(timeout=INTERVAL + 5)
    _thread = None
