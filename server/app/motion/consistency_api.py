"""POST /api/jobs/{id}/consistency: whole-film content check (docs/REVOICE_CONTRACT.md §3).

Body {project, instruction?} -> {summary, issues, narrations, props, changes, project|null,
rejected}. 404 unknown job, 409 not a done Remotion job (same checks as the editor endpoint),
400 bad body, 413 too large, 502 when the Opus call fails or returns no usable JSON.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from .. import motion_api, pipeline
from . import consistency, versions

router = APIRouter()

BODY_HINT = "请求体必须是 JSON：{project, instruction?}"


@router.post("/api/jobs/{job_id}/consistency")
async def consistency_check(job_id: str, request: Request):
    jid, _ = motion_api._remotion_job(job_id)
    raw = await request.body()
    if len(raw) > versions.MAX_PROJECT_BYTES + 50_000:
        raise HTTPException(413, "请求太大")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, BODY_HINT)
    if not isinstance(body, dict) or "project" not in body:
        raise HTTPException(400, BODY_HINT)
    try:
        return await asyncio.to_thread(
            consistency.run, pipeline.job_dir(jid), body["project"], body.get("instruction"))
    except consistency.ConsistencyError as e:
        raise HTTPException(400, str(e))
    except consistency.ConsistencyLLMError as e:
        raise HTTPException(502, str(e))
