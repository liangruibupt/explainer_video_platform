"""POST /api/jobs/{id}/assist: Opus chat for parameter edits (docs/MOTION_ASSIST_CONTRACT.md §3).

Body {message, project, scene_id?, history?} -> {reply, changes, project|null, ops, rejected,
scene_edit|null}. 404 unknown job, 409 not a done Remotion job (same checks as the editor
endpoint), 400 bad body, 413 too large, 502 when the Opus call fails.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from .. import motion_api, pipeline
from . import assist, versions

router = APIRouter()


@router.post("/api/jobs/{job_id}/assist")
async def assist_chat(job_id: str, request: Request):
    jid, _ = motion_api._remotion_job(job_id)
    raw = await request.body()
    if len(raw) > versions.MAX_PROJECT_BYTES + 200_000:
        raise HTTPException(413, "请求太大")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "请求体必须是 JSON：{message, project, scene_id?, history?}")
    if not isinstance(body, dict) or "project" not in body:
        raise HTTPException(400, "请求体必须是 JSON：{message, project, scene_id?, history?}")
    try:
        return await asyncio.to_thread(
            assist.run, pipeline.job_dir(jid), body.get("message"), body["project"],
            body.get("scene_id"), body.get("history"))
    except assist.AssistError as e:
        raise HTTPException(400, str(e))
    except assist.AssistLLMError as e:
        raise HTTPException(502, str(e))
