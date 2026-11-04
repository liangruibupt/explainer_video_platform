"""/api/chat/* — conversational creation sessions (docs/PHASE4_CONTRACTS.md, part A)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import assistant, store

router = APIRouter(prefix="/api/chat", tags=["chat"])


class SessionIn(BaseModel):
    mode: str = Field(default="auto", pattern=r"^(auto|image|video|music|explainer)$")
    allow_web: bool = False
    project_id: str = Field(default="", max_length=64)
    message: str = Field(default="", max_length=4000)  # optional first message (guide mode)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _load(sid: str) -> dict:
    s = store.get(sid)
    if not s:
        raise HTTPException(404, "no such session")
    return s


def _view(s: dict) -> dict:
    """The session plus live progress of its current run; posts status changes once."""
    lk = store.lock(s["id"])
    if not lk.acquire(blocking=False):  # a message is being answered: read-only view
        s = store.get(s["id"]) or s
        prog = assistant.progress(s["run_id"]) if s.get("run_id") else None
        return s | {"progress": prog, "busy": True}
    try:
        s = store.get(s["id"]) or s
        prog = assistant.progress(s["run_id"]) if s.get("run_id") else None
        if assistant.sync(s, prog):
            store.save(s)
    finally:
        lk.release()
    return s | {"progress": prog, "busy": False}


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn):
    s = store.create(body.mode, body.allow_web, body.project_id)
    if body.message.strip():
        with store.lock(s["id"]):
            assistant.turn(s, body.message.strip())
            store.save(s)
    return {"id": s["id"]}


@router.get("/sessions")
def list_sessions():
    return store.list_sessions()


@router.get("/sessions/{sid}")
def get_session(sid: str):
    return _view(_load(sid))


@router.post("/sessions/{sid}/messages")
def post_message(sid: str, body: MessageIn):
    _load(sid)
    lk = store.lock(sid)
    if not lk.acquire(blocking=False):
        raise HTTPException(409, "上一条消息还在处理")
    try:
        s = store.get(sid)
        prog = assistant.progress(s["run_id"]) if s.get("run_id") else None
        assistant.sync(s, prog)
        assistant.turn(s, body.text.strip())
        store.save(s)
    finally:
        lk.release()
    return _view(s)


class SettingsIn(BaseModel):
    mode: str | None = Field(default=None, pattern=r"^(auto|image|video|music|explainer)$")
    allow_web: bool | None = None


@router.patch("/sessions/{sid}")
def patch_session(sid: str, body: SettingsIn):
    with store.lock(sid):
        s = _load(sid)
        if body.mode is not None:
            s["mode"] = body.mode
        if body.allow_web is not None:
            s["allow_web"] = body.allow_web
        store.save(s)
    return _view(s)
