"""HTTP API of the task center: /api/tasks/* (docs/PHASE4_CONTRACTS.md, part D)."""
import inspect
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import collect, notify, watcher

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
RUN_RE = r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}"
NODE_RE = r"[A-Za-z][A-Za-z0-9_]{0,31}"
CONTENT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".webp": "image/webp", ".mp4": "video/mp4", ".flac": "audio/flac",
           ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}

watcher.start()  # one per process; EVP_TASK_WATCHER=0 turns it off


@router.get("")
def tasks(status: str = "", kind: str = ""):
    kinds = [k for k in kind.split(",") if k] if kind else None
    if kinds and any(k not in collect.KINDS for k in kinds):
        raise HTTPException(422, f"kind must be one of {', '.join(collect.KINDS)}")
    statuses = [s for s in status.split(",") if s] if status else None
    if statuses and any(s not in collect.STATUSES for s in statuses):
        raise HTTPException(422, f"status must be one of {', '.join(collect.STATUSES)}")
    items = collect.collect(kinds)
    if statuses:
        items = [i for i in items if i["status"] in statuses]
    return items


@router.get("/reviews")
def reviews():
    return collect.reviews()


class ReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str = Field(default="", max_length=500)
    choice: int | None = Field(default=None, ge=0, le=63)


def _check(run_id: str, node_id: str):
    if not re.fullmatch(RUN_RE, run_id) or not re.fullmatch(NODE_RE, node_id):
        raise HTTPException(404, "no such review")
    from ..workflows import engine
    run = engine.read_run(run_id)
    if not run or node_id not in (run.get("nodes") or {}):
        raise HTTPException(404, "no such review")
    return engine, run


def _accepts_choice(fn) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "choice" in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


@router.post("/reviews/{run_id}/{node_id}")
def review(run_id: str, node_id: str, body: ReviewIn):
    engine, _ = _check(run_id, node_id)
    kw = {}
    if body.choice is not None:
        if not _accepts_choice(engine.review):
            raise HTTPException(422, "candidate choice is not supported by this engine")
        kw["choice"] = body.choice
    try:
        run = engine.review(run_id, node_id, body.decision, body.note, **kw)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"id": run.get("id", run_id), "status": run.get("status"),
            "node": {k: v for k, v in (run.get("nodes") or {}).get(node_id, {}).items()
                     if k != "trace"}}


@router.get("/reviews/{run_id}/{node_id}/candidates/{index}")
def candidate_file(run_id: str, node_id: str, index: int):
    _, run = _check(run_id, node_id)
    p = collect.candidate_path(run, node_id, index)
    if p is None or p.suffix.lower() not in CONTENT:
        raise HTTPException(404, "no such candidate")
    return FileResponse(p, media_type=CONTENT[p.suffix.lower()])


@router.get("/notifications")
def notifications(unread: int = 0, limit: int = Query(default=100, ge=1, le=500)):
    items = notify.list_all(unread=bool(unread), limit=limit)
    return {"items": items, "unread": len(notify.list_all(unread=True, limit=500)),
            "settings": notify.settings()}


class ReadIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    all: bool = False


@router.post("/notifications/read")
def notifications_read(body: ReadIn):
    return {"marked": notify.mark_read(body.ids, all_=body.all)}
