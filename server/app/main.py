"""HTTP API for the basic explainer-video studio (single user, local)."""
import base64
import binascii
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pipeline

app = FastAPI(title="explainer_video_platform")
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
FILES = {"final.mp4", "poster.jpg", "scenes.json", "index.html", "captions.ass",
         "pipeline.log", "words.json", "durations.json"}


class JobIn(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    language: str = Field(default="中文（普通话）", max_length=40)
    seconds: int = Field(default=60, ge=20, le=180)
    ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16)$")
    captions: bool = True
    music: str = Field(default="synth", pattern=r"^[a-z0-9_-]{1,32}$")
    voice: str = Field(default="Zhiyu", pattern=r"^[A-Za-z]{2,20}$")
    brief: str = Field(default="", max_length=8000)
    scene_context: str = Field(default="", max_length=8000)
    background_png_b64: str = Field(default="", max_length=12_000_000)


@app.get("/api/health")
def health():
    return {"ok": True, "model": "claude-opus-5.5", "time": time.time()}


@app.get("/api/music")
def music():
    return pipeline.music_choices()


@app.get("/api/jobs")
def jobs():
    return pipeline.list_jobs()


@app.post("/api/jobs", status_code=201)
def create(body: JobIn):
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    bg = b""
    if body.background_png_b64:
        try:
            bg = base64.b64decode(body.background_png_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "background_png_b64 is not valid base64")
        if not bg.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(422, "background must be a PNG")
    if body.music not in pipeline.music_choices():
        raise HTTPException(422, f"unknown or not downloaded music: {body.music}")
    return pipeline.create_job(job_id, body.topic.strip(), body.language, body.seconds,
                               body.ratio, body.captions, body.music, body.voice,
                               brief=body.brief.strip(), scene_context=body.scene_context.strip(),
                               background_png=bg)


def _id(job_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id):
        raise HTTPException(404, "no such job")
    return job_id


@app.get("/api/jobs/{job_id}")
def get(job_id: str):
    job = pipeline.read_job(_id(job_id))
    if not job:
        raise HTTPException(404, "no such job")
    return job


@app.delete("/api/jobs/{job_id}")
def delete(job_id: str):
    job = pipeline.read_job(_id(job_id))
    if not job:
        raise HTTPException(404, "no such job")
    if job.get("status") in ("queued", "running"):
        raise HTTPException(409, "job is still running")
    pipeline.delete_job(job_id)
    return {"deleted": job_id}


@app.get("/api/jobs/{job_id}/files/{name}")
def file(job_id: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "no such file")
    p = pipeline.job_dir(_id(job_id)) / name
    if not p.exists():
        raise HTTPException(404, "not ready")
    if name in ("pipeline.log", "captions.ass", "index.html"):
        return PlainTextResponse(p.read_text(errors="replace"))
    return FileResponse(p)


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(WEB / "index.html")
