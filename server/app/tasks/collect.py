"""Read every store into one task list (GET /api/tasks) and the review inbox.

Sources (read only, never changed here):
  explainer jobs     pipeline.list_jobs()                    data/jobs/<id>/job.json
  image generations  images.list_all()                       data/images/<id>/state.json
  video / music      media.videos / media.musics .list_all() data/videos|music/<id>/state.json
  workflow runs      workflows.engine.list_runs / read_run   data/wf_runs/<id>/run.json

Item: {kind, id, title, status: queued|running|waiting_review|done|failed, created, updated,
       progress: {done, total}, url, gpu_wait (minutes, when known), error, reviews: [...]}
"""
import re
from pathlib import Path

KINDS = ("job", "image", "video", "music", "workflow")
STATUSES = ("queued", "running", "waiting_review", "done", "failed")
MEDIA = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
         ".mp4": "video", ".flac": "audio", ".mp3": "audio", ".wav": "audio", ".m4a": "audio"}
_WAIT_RE = re.compile(r"等待 GPU（(\d+) 分钟）")


def _status(s: str) -> str:
    if s in STATUSES:
        return s
    return {"pending": "queued", "cached": "done", "skipped": "done"}.get(s, "running")


def _wait_from_notes(notes) -> int | None:
    best = None
    for note in notes:
        m = _WAIT_RE.search(note or "")
        if m:
            best = max(best or 0, int(m.group(1)))
    return best


def _short(text, n=80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- per source

def _jobs() -> list:
    from .. import pipeline
    out = []
    for j in pipeline.list_jobs():
        steps = j.get("steps") or []
        st = _status(j.get("status", "queued"))
        live = st in ("queued", "running")
        out.append({
            "kind": "job", "id": j["id"], "title": _short(j.get("topic") or j["id"]),
            "status": st, "created": j.get("created"), "updated": j.get("updated"),
            "progress": {"done": sum(s.get("status") in ("done", "skipped") for s in steps),
                         "total": len(steps)},
            "url": f"/jobs/{j['id']}",
            "gpu_wait": _wait_from_notes(s.get("note") for s in steps
                                         if s.get("status") == "running") if live else None,
            "step": next((s.get("label") for s in steps if s.get("status") == "running"), None),
            "error": (j.get("error") or "").split("\n")[0][:200] or None,
            "reviews": []})
    return out


def _media_item(kind, s, url, title_key):
    st = _status(s.get("status", "queued"))
    waited = s.get("waited")
    return {"kind": kind, "id": s.get("id"), "title": _short(s.get(title_key) or s.get("id")),
            "status": st, "created": s.get("created"), "updated": s.get("updated"),
            "progress": {"done": 1 if st == "done" else 0, "total": 1},
            "url": url,
            "gpu_wait": (int(waited) // 60) if st == "running" and isinstance(waited, (int, float))
            and waited >= 60 else None,
            "error": s.get("error") or None,
            "reviews": []}


def _images() -> list:
    from .. import images
    return [_media_item("image", s, "/image", "prompt") for s in images.list_all(limit=200)]


def _videos() -> list:
    from .. import media
    return [_media_item("video", s, "/video", "prompt") for s in media.videos.list_all(limit=200)]


def _musics() -> list:
    from .. import media
    return [_media_item("music", s, "/music", "style") for s in media.musics.list_all(limit=200)]


def _file_kind(v) -> str | None:
    if isinstance(v, str) and v.startswith("/"):
        return MEDIA.get(Path(v).suffix.lower())
    return None


def node_reviews(run: dict) -> list:
    """Nodes of one run that wait for a person, with preview links."""
    out = []
    types = {n["id"]: n.get("type") for n in (run.get("workflow") or {}).get("nodes", [])}
    for nid, n in (run.get("nodes") or {}).items():
        if n.get("status") != "waiting_review":
            continue
        v = n.get("review_input")
        rv = {"run_id": run["id"], "run_name": run.get("name"), "node_id": nid,
              "node_type": types.get(nid), "note": n.get("note"), "since": n.get("started"),
              "preview_kind": _file_kind(v), "preview_url": None, "text": None,
              "candidates": []}
        if rv["preview_kind"]:
            rv["preview_url"] = f"/api/wf_runs/{run['id']}/file?node={nid}&port=input"
        elif v is not None:
            rv["text"] = v if isinstance(v, str) else None
            rv["value"] = None if isinstance(v, str) else v
        cands = n.get("candidates")
        if isinstance(cands, list):
            for i, c in enumerate(cands):
                path = c if isinstance(c, str) else (c or {}).get("path") or (c or {}).get("value")
                k = _file_kind(path)
                rv["candidates"].append({
                    "index": i, "kind": k,
                    "url": f"/api/tasks/reviews/{run['id']}/{nid}/candidates/{i}" if k else None,
                    "text": path if isinstance(path, str) and not k else None,
                    "label": (c.get("label") if isinstance(c, dict) else None) or f"候选 {i + 1}"})
        out.append(rv)
    return out


def candidate_path(run: dict, nid: str, index: int):
    """Absolute path of one candidate file, or None (only files under the run / cache dirs)."""
    from ..workflows import engine
    n = (run.get("nodes") or {}).get(nid) or {}
    cands = n.get("candidates")
    if not isinstance(cands, list) or not 0 <= index < len(cands):
        return None
    c = cands[index]
    v = c if isinstance(c, str) else (c or {}).get("path") or (c or {}).get("value")
    if not _file_kind(v):
        return None
    p = Path(v).resolve()
    if not (p.is_relative_to(engine.CACHE.resolve()) or p.is_relative_to(engine.RUNS.resolve())):
        return None
    return p if p.is_file() else None


def _runs() -> list:
    from ..workflows import engine
    out = []
    for summary in engine.list_runs(limit=200):
        run = engine.read_run(summary["id"])
        if not run:
            continue
        nodes = run.get("nodes") or {}
        st = _status(run.get("status", "running"))
        out.append({
            "kind": "workflow", "id": run["id"], "title": _short(run.get("name") or run["id"]),
            "status": st, "created": run.get("created"), "updated": run.get("updated"),
            "progress": {"done": sum(n.get("status") in ("done", "cached") for n in nodes.values()),
                         "total": len(nodes)},
            "url": f"/workflow?run={run['id']}",
            "gpu_wait": _wait_from_notes(n.get("note") for n in nodes.values()
                                         if n.get("status") == "running") if st == "running" else None,
            "error": next((n.get("error") for n in nodes.values() if n.get("status") == "failed"),
                          None),
            "reviews": [{k: r[k] for k in ("run_id", "node_id", "node_type", "preview_url")}
                        for r in node_reviews(run)]})
    return out


SOURCES = {"job": _jobs, "image": _images, "video": _videos, "music": _musics,
           "workflow": _runs}


def collect(kinds=None) -> list:
    items = []
    for kind in kinds or KINDS:
        try:
            items += SOURCES[kind]()
        except Exception:  # noqa: BLE001 - one broken store must not hide the others
            continue
    return sorted(items, key=lambda i: i.get("created") or 0, reverse=True)


def reviews() -> list:
    from ..workflows import engine
    out = []
    for summary in engine.list_runs(limit=200):
        if summary.get("status") not in ("waiting_review", "running"):
            continue
        run = engine.read_run(summary["id"])
        if run:
            out += node_reviews(run)
    return sorted(out, key=lambda r: r.get("since") or 0, reverse=True)
