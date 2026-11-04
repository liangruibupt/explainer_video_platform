"""Files under data/settings/: user settings (notifications.json, ui.json) and caches.

Caches are one JSON file each ({key: {"ts": unix, "value": ...}}), so a slow or paid AWS
call (Pricing, Cost Explorer, S3 listing) runs at most once per TTL.
"""
import json
import os
import threading
import time
from pathlib import Path

from .. import pipeline

DEFAULTS = {
    "notifications": {"in_app": True, "browser": False},
    "ui": {"theme": "auto", "density": "comfortable"},
}
_lock = threading.Lock()


def root() -> Path:
    p = pipeline.DATA / "settings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    tmp.replace(path)


def get(name: str) -> dict:
    """A user settings file merged over its defaults (unknown keys dropped)."""
    cur = _read(root() / f"{name}.json") or {}
    return {k: cur.get(k, v) for k, v in DEFAULTS[name].items()}


def put(name: str, value: dict) -> dict:
    data = {k: value.get(k, v) for k, v in get(name).items()}
    with _lock:
        _write(root() / f"{name}.json", data)
    return data


def cached(file: str, key: str, ttl: float, fn, refresh: bool = False):
    """(value, ts) from data/settings/<file>.json if younger than ttl, else fn() stored.

    A failing fn() raises and leaves the old entry in place."""
    path = root() / f"{file}.json"
    entry = (_read(path) or {}).get(key)
    if entry and not refresh and time.time() - entry["ts"] < ttl:
        return entry["value"], entry["ts"]
    value, ts = fn(), time.time()
    with _lock:
        data = _read(path) or {}
        data[key] = {"ts": ts, "value": value}
        _write(path, data)
    return value, ts
