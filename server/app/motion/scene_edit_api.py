"""Scene rewrites HTTP API (docs/MOTION_ASSIST_CONTRACT.md §4, part B).

  POST /api/jobs/{id}/scene-edits                    {scene_id, instruction} -> 202 record
  GET  /api/jobs/{id}/scene-edits                    records, newest first
  GET  /api/jobs/{id}/scene-edits/{n}                record
  GET  /api/jobs/{id}/scene-edits/{n}/stills/{k}.png preview still k
  POST /api/jobs/{id}/scene-edits/{n}/apply|discard|undo
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import motion_api, pipeline
from . import scene_edit

router = APIRouter()


@router.on_event("startup")
def _recover_scene_edits() -> None:
    scene_edit.recover(pipeline.DATA)


def _job(job_id: str):
    jid, _ = motion_api._remotion_job(job_id)
    return jid, pipeline.job_dir(jid)


def _call(fn, *args):
    try:
        return fn(*args)
    except scene_edit.EditError as e:
        raise HTTPException(e.status, str(e))


@router.post("/api/jobs/{job_id}/scene-edits", status_code=202)
async def create(job_id: str, request: Request):
    jid, d = _job(job_id)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("scene_id"), str) \
            or not isinstance(body.get("instruction"), str):
        raise HTTPException(422, "请求体必须是 JSON：{scene_id, instruction}")
    rec = await asyncio.to_thread(_call, scene_edit.submit, jid, d, body["scene_id"],
                                  body["instruction"])
    return JSONResponse(rec, status_code=202)


@router.get("/api/jobs/{job_id}/scene-edits")
def list_(job_id: str):
    jid, d = _job(job_id)
    return scene_edit.list_edits(d)


@router.get("/api/jobs/{job_id}/scene-edits/{n}")
def one(job_id: str, n: int):
    jid, d = _job(job_id)
    rec = scene_edit.get_edit(d, n)
    if not rec:
        raise HTTPException(404, "没有这个画面修改")
    return scene_edit.public(rec)


@router.get("/api/jobs/{job_id}/scene-edits/{n}/stills/{k}.png")
def still(job_id: str, n: int, k: int):
    jid, d = _job(job_id)
    p = scene_edit.still_path(d, n, k)
    if not p:
        raise HTTPException(404, "没有这张预览图")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post("/api/jobs/{job_id}/scene-edits/{n}/apply")
async def apply(job_id: str, n: int):
    jid, d = _job(job_id)
    return await asyncio.to_thread(_call, scene_edit.apply, jid, d, n)


@router.post("/api/jobs/{job_id}/scene-edits/{n}/discard")
async def discard(job_id: str, n: int):
    jid, d = _job(job_id)
    return await asyncio.to_thread(_call, scene_edit.discard, jid, d, n)


@router.post("/api/jobs/{job_id}/scene-edits/{n}/undo")
async def undo(job_id: str, n: int):
    jid, d = _job(job_id)
    return await asyncio.to_thread(_call, scene_edit.undo, jid, d, n)
