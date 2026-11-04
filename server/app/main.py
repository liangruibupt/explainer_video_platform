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

from . import image, images, media, pipeline

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
    illustrations: int = Field(default=0, ge=0, le=4)
    clips: int = Field(default=0, ge=0, le=2)


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
                               background_png=bg, illustrations=body.illustrations,
                               clips=body.clips)


class ImageIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=1500)
    ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16|1:1)$")
    mode: str = Field(default="t2i", pattern=r"^(t2i|rgba)$")
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    n: int = Field(default=1, ge=1, le=4)


def _img_id(img_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", img_id):
        raise HTTPException(404, "no such image")
    return img_id


@app.get("/api/image/status")
def image_status():
    try:
        return image.status()
    except Exception as e:  # noqa: BLE001 - e.g. no AWS credentials
        return {"deployed": False, "state": "unavailable", "error": str(e)[:200]}


@app.get("/api/images")
def image_list():
    return images.list_all()


@app.post("/api/images", status_code=201)
def image_create(body: ImageIn):
    img_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    return images.create(img_id, body.model_dump())


@app.get("/api/images/{img_id}")
def image_get(img_id: str):
    s = images.get(_img_id(img_id))
    if not s:
        raise HTTPException(404, "no such image")
    return s


@app.get("/api/images/{img_id}/{name}")
def image_file(img_id: str, name: str):
    if not re.fullmatch(r"[0-3]\.png", name):
        raise HTTPException(404, "no such file")
    p = images.ROOT / _img_id(img_id) / name
    if not p.exists():
        raise HTTPException(404, "not ready")
    return FileResponse(p, media_type="image/png")


def _id(job_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id):
        raise HTTPException(404, "no such job")
    return job_id


class VideoIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=1500)
    aspect: str = Field(default="16:9", pattern=r"^(16:9|9:16)$")
    seed: int = Field(default=7, ge=0, le=2**31 - 1)


class MusicIn(BaseModel):
    style: str = Field(min_length=2, max_length=600)
    lyrics: str = Field(default="[Intro]\n\n[Instrumental]\n\n[Outro]", min_length=2,
                        max_length=4000)
    cot: str = Field(default="full", pattern=r"^(full|off)$")
    seed: int = Field(default=831001, ge=0, le=2**31 - 1)


def _gpu_status(mod):
    try:
        return mod.status()
    except Exception as e:  # noqa: BLE001 - e.g. no AWS credentials
        return {"deployed": False, "state": "unavailable", "error": str(e)[:200]}


@app.get("/api/video/status")
def video_status():
    from . import video
    return _gpu_status(video)


@app.get("/api/music/status")
def music_status():
    from . import music as music_mod
    return _gpu_status(music_mod)


def _media_routes(path: str, store, model, files: dict):
    def lst():
        return store.list_all()

    def create_item(body: model):
        item_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        return store.create(item_id, body.model_dump())

    def get_item(item_id: str):
        s = store.get(_img_id(item_id))
        if not s:
            raise HTTPException(404, "no such item")
        return s

    def get_file(item_id: str, name: str):
        if name not in files:
            raise HTTPException(404, "no such file")
        p = store.root / _img_id(item_id) / name
        if not p.exists():
            raise HTTPException(404, "not ready")
        return FileResponse(p, media_type=files[name])

    app.get(f"/api/{path}")(lst)
    app.post(f"/api/{path}", status_code=201)(create_item)
    app.get(f"/api/{path}/{{item_id}}")(get_item)
    app.get(f"/api/{path}/{{item_id}}/{{name}}")(get_file)


_media_routes("videos", media.videos, VideoIn, {"0.mp4": "video/mp4"})
_media_routes("musics", media.musics, MusicIn, {"0.flac": "audio/flac", "0.mp3": "audio/mpeg",
                                                "score.abc": "text/plain"})


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
