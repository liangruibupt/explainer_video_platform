"""Re-voicing HTTP API (docs/REVOICE_CONTRACT.md §2, part A); logic in revoice.py.

  POST /api/jobs/{id}/revoice                        {narrations, all?} -> 202 record; 409 while one runs
  GET  /api/jobs/{id}/revoice                        records, newest first
  GET  /api/jobs/{id}/revoice/{n}                    record
  GET  /api/jobs/{id}/revoice/{n}/audio/{scene}.mp3  a scene's audio in take n
  POST /api/jobs/{id}/revoice/{n}/apply              {project} -> {status:"applied", project, changes}
  POST /api/jobs/{id}/revoice/{n}/undo               {project} -> {status:"undone", project}
"""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import motion_api, pipeline
from . import revoice, versions

router = APIRouter()


@router.on_event("startup")
def _recover_takes() -> None:
    revoice.recover(pipeline.DATA)


async def _body(request: Request, need: str | None = None) -> dict:
    raw = await request.body()
    if len(raw) > versions.MAX_PROJECT_BYTES + 200_000:
        raise HTTPException(413, "请求太大")
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = None
    if not isinstance(body, dict) or (need and need not in body):
        raise HTTPException(400, f"请求体必须是 JSON 对象{f'，并包含 {need}' if need else ''}")
    return body


def _err(e: revoice.RevoiceError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.post("/api/jobs/{job_id}/revoice", status_code=202)
async def revoice_start(job_id: str, request: Request):
    jid, _ = motion_api._remotion_job(job_id)
    body = await _body(request)
    try:
        rec = await asyncio.to_thread(revoice.submit, jid, pipeline.job_dir(jid),
                                      body.get("narrations"), body.get("all", False))
    except revoice.RevoiceError as e:
        raise _err(e)
    return JSONResponse(rec, status_code=202)


@router.get("/api/jobs/{job_id}/revoice")
def revoice_list(job_id: str):
    jid, _ = motion_api._remotion_job(job_id)
    return revoice.list_records(pipeline.job_dir(jid))


@router.get("/api/jobs/{job_id}/revoice/{n}")
def revoice_get(job_id: str, n: int):
    jid, _ = motion_api._remotion_job(job_id)
    rec = revoice.get(pipeline.job_dir(jid), n) if n >= 1 else None
    if not rec:
        raise HTTPException(404, "没有这次配音")
    return rec


@router.get("/api/jobs/{job_id}/revoice/{n}/audio/{scene_id}.mp3")
def revoice_audio(job_id: str, n: int, scene_id: str):
    jid, _ = motion_api._remotion_job(job_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", scene_id):
        raise HTTPException(404, "没有这个段落")
    p = revoice.audio_path(pipeline.job_dir(jid), n, scene_id)
    if not p:
        raise HTTPException(404, "没有这段配音")
    return FileResponse(p, media_type="audio/mpeg")


@router.post("/api/jobs/{job_id}/revoice/{n}/apply")
async def revoice_apply(job_id: str, n: int, request: Request):
    jid, _ = motion_api._remotion_job(job_id)
    body = await _body(request, "project")
    try:
        return await asyncio.to_thread(revoice.apply, jid, pipeline.job_dir(jid), n,
                                       body["project"])
    except revoice.RevoiceError as e:
        raise _err(e)


@router.post("/api/jobs/{job_id}/revoice/{n}/undo")
async def revoice_undo(job_id: str, n: int, request: Request):
    jid, _ = motion_api._remotion_job(job_id)
    body = await _body(request, "project")
    try:
        return await asyncio.to_thread(revoice.undo, jid, pipeline.job_dir(jid), n,
                                       body["project"])
    except revoice.RevoiceError as e:
        raise _err(e)
