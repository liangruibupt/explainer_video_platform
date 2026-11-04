"""Projects (data/projects/<id>.json) and the read-only scans that list a project's items
and import generated works into the asset library."""
import json
import re
import threading
import time
import uuid
from pathlib import Path

from .. import pipeline
from . import store

ROOT = pipeline.DATA / "projects"
WORK_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
FILE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\.[a-z0-9]{1,5}")
JOB_FILES = ("final.mp4", "poster.jpg")
_lock = threading.Lock()


def valid_id(project_id) -> bool:
    return isinstance(project_id, str) and store.PROJECT_ID.fullmatch(project_id) is not None


def _path(project_id: str) -> Path:
    if not valid_id(project_id):
        raise KeyError(project_id)
    return ROOT / f"{project_id}.json"


def _write(p: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    f = _path(p["id"])
    tmp = f.with_name(f".{f.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1))
    tmp.replace(f)


def get(project_id: str) -> dict:
    try:
        f = _path(project_id)
    except KeyError:
        return {}
    return json.loads(f.read_text()) if f.exists() else {}


def list_all() -> list:
    if not ROOT.exists():
        return []
    out = []
    for f in ROOT.glob("proj_*.json"):
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda p: p.get("created", 0), reverse=True)


def create(name: str, note: str = "") -> dict:
    p = {"id": store.new_id("proj"), "name": store.clean_name(name, "未命名项目"),
         "created": time.time(), "note": str(note or "")[:2000]}
    _write(p)
    return p


def update(project_id: str, **changes) -> dict:
    with _lock:
        p = get(project_id)
        if not p:
            raise KeyError(project_id)
        if "name" in changes:
            p["name"] = store.clean_name(changes["name"], "未命名项目")
        if "note" in changes:
            p["note"] = str(changes["note"] or "")[:2000]
        p["updated"] = time.time()
        _write(p)
        return p


def delete(project_id: str) -> bool:
    """Removes the project; its assets stay in the library, unassigned."""
    if not get(project_id):
        return False
    _path(project_id).unlink()
    store.clear_project(project_id)
    return True


# ---------------------------------------------------------------- other stores (read-only)

def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _scan(root: Path, fname: str):
    if not root.exists():
        return
    for p in root.glob(f"*/{fname}"):
        if WORK_ID.fullmatch(p.parent.name):
            j = _json(p)
            if j:
                yield p.parent.name, j


def _roots():
    from .. import images, media
    from ..workflows import engine
    return {"job": (pipeline.DATA / "jobs", "job.json"),
            "image": (images.ROOT, "state.json"),
            "video": (media.videos.root, "state.json"),
            "music": (media.musics.root, "state.json"),
            "wf_run": (engine.RUNS, "run.json")}


def items(project_id: str) -> dict:
    """Assets of the project plus every job / image / video / music / workflow run whose
    stored record carries this project_id."""
    out = {"assets": store.list_all(project_id=project_id)}
    for kind, (root, fname) in _roots().items():
        found = []
        for wid, j in _scan(root, fname):
            if j.get("project_id") != project_id:
                continue
            found.append({"id": wid, "kind": kind, "status": j.get("status"),
                          "created": j.get("created"),
                          "title": j.get("topic") or j.get("name") or j.get("prompt")
                          or j.get("style") or wid,
                          "files": j.get("files") or [w.get("file") for w in j.get("works", [])
                                                      if w.get("file")]})
        out[kind + "s"] = sorted(found, key=lambda x: x.get("created") or 0, reverse=True)
    return out


def source_file(source: str, work_id: str, file: str = ""):
    """Resolve an existing generated file for import. Raises store.AssetError (404/422)."""
    roots = _roots()
    if source not in roots:
        raise store.AssetError("source 必须是 job / image / video / music / wf_run", 422)
    if not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id):
        raise store.AssetError("id 格式不对", 422)
    if file and (not isinstance(file, str) or not FILE_NAME.fullmatch(file)):
        raise store.AssetError("file 格式不对", 422)
    root, fname = roots[source]
    d = root / work_id
    rec = _json(d / fname)
    if not rec:
        raise store.AssetError("找不到这个作品", 404)
    if source == "job":
        allowed = list(JOB_FILES)
    elif source == "wf_run":
        allowed = [w["file"] for w in rec.get("works", []) if w.get("file")]
        d = d / "outputs"
    else:
        allowed = list(rec.get("files") or [])
    name = file or (allowed[0] if allowed else "")
    if not name or name not in allowed:
        raise store.AssetError(f"可导入的文件：{', '.join(allowed) or '无'}", 404)
    p = (d / name).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():
        raise store.AssetError("文件不存在", 404)
    return p, rec


def import_work(source: str, work_id: str, file: str = "", name: str = "",
                project_id=None) -> dict:
    p, rec = source_file(source, work_id, file)
    title = name or rec.get("topic") or rec.get("prompt") or rec.get("name") \
        or f"{source} {work_id}"
    return store.ingest(p, p.name, name=str(title)[:80], project_id=project_id,
                        source={"type": source, "id": work_id, "file": p.name})
