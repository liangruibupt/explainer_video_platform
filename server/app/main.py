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
    arrangement: str = Field(default="", max_length=500)


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


# ---------------------------------------------------------------- workflows (phase 3)

from .workflows import agent as wf_agent  # noqa: E402
from .workflows import engine as wf_engine  # noqa: E402
from .workflows import nodes as wf_nodes  # noqa: E402
from .workflows import store as wf_store  # noqa: E402
from .workflows.validate import levels as wf_levels  # noqa: E402
from .workflows.validate import validate as wf_validate  # noqa: E402

wf_engine.recover()


class WorkflowIn(BaseModel):
    workflow: dict


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=1000)


class EditIn(BaseModel):
    workflow: dict
    instruction: str = Field(min_length=2, max_length=1000)


class ReviewIn(BaseModel):
    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str = Field(default="", max_length=500)


class RetryIn(BaseModel):
    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def _run_id(run_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", run_id):
        raise HTTPException(404, "no such run")
    return run_id


def _with_layout(wf: dict) -> dict:
    errs = wf_validate(wf)
    lv = wf_levels(wf.get("nodes") or [], wf.get("edges") or []) if not any(
        e["code"] == "format" for e in errs) else {}
    return {"workflow": wf, "errors": errs, "valid": not errs, "levels": lv}


@app.get("/api/workflows/nodes")
def wf_node_list():
    return wf_nodes.declarations()


@app.get("/api/workflows/templates")
def wf_templates():
    return wf_store.templates()


@app.get("/api/workflows")
def wf_list():
    return wf_store.list_saved()


@app.post("/api/workflows", status_code=201)
def wf_save(body: WorkflowIn):
    return wf_store.save(body.workflow)


@app.post("/api/workflows/validate")
def wf_check(body: WorkflowIn):
    return _with_layout(body.workflow)


@app.post("/api/workflows/generate")
def wf_generate(body: GenerateIn):
    res = wf_agent.generate(body.prompt)
    if res["workflow"]:
        res["levels"] = _with_layout(res["workflow"])["levels"]
    return res


@app.post("/api/workflows/edit")
def wf_edit(body: EditIn):
    res = wf_agent.edit(body.workflow, body.instruction)
    if res["workflow"]:
        res["levels"] = _with_layout(res["workflow"])["levels"]
    return res


@app.get("/api/workflows/{wf_id}")
def wf_get(wf_id: str):
    wf = wf_store.get(wf_id)
    if not wf:
        raise HTTPException(404, "no such workflow")
    return wf


@app.delete("/api/workflows/{wf_id}")
def wf_delete(wf_id: str):
    if not wf_store.delete(wf_id):
        raise HTTPException(404, "no such saved workflow")
    return {"deleted": wf_id}


@app.post("/api/wf_runs", status_code=201)
def wf_run(body: WorkflowIn):
    try:
        return wf_engine.start(body.workflow)
    except ValueError as e:
        raise HTTPException(422, e.args[0])


@app.get("/api/wf_runs")
def wf_runs():
    return wf_engine.list_runs()


@app.get("/api/wf_runs/{run_id}")
def wf_run_get(run_id: str):
    r = wf_engine.read_run(_run_id(run_id))
    if not r:
        raise HTTPException(404, "no such run")
    for n in r["nodes"].values():
        n.pop("trace", None)
    r["levels"] = wf_levels(r["workflow"]["nodes"], r["workflow"]["edges"])
    return r


@app.post("/api/wf_runs/{run_id}/review")
def wf_review(run_id: str, body: ReviewIn):
    if not wf_engine.read_run(_run_id(run_id)):
        raise HTTPException(404, "no such run")
    try:
        return wf_engine.review(run_id, body.node_id, body.decision, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/wf_runs/{run_id}/retry")
def wf_retry(run_id: str, body: RetryIn):
    if not wf_engine.read_run(_run_id(run_id)):
        raise HTTPException(404, "no such run")
    try:
        return wf_engine.retry(run_id, body.node_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


WF_MEDIA = {".png": "image/png", ".mp4": "video/mp4", ".flac": "audio/flac",
            ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


@app.get("/api/wf_runs/{run_id}/file")
def wf_file(run_id: str, node: str, port: str = "", work: str = ""):
    """A node's output file (node + port) or a saved work (work=<file name>)."""
    r = wf_engine.read_run(_run_id(run_id))
    if not r or node not in r["nodes"]:
        raise HTTPException(404, "no such node")
    if work:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}\.[a-z0-9]{2,5}", work):
            raise HTTPException(404, "no such file")
        p = wf_engine.run_dir(run_id) / "outputs" / work
    else:
        n = r["nodes"][node]
        v = (n.get("outputs") or {}).get(port) or (n.get("review_input")
                                                   if port == "input" else None)
        if not isinstance(v, str):
            raise HTTPException(404, "no such file")
        p = Path(v).resolve()
        if not (p.is_relative_to(wf_engine.CACHE.resolve())
                or p.is_relative_to(wf_engine.RUNS.resolve())):
            raise HTTPException(404, "no such file")
    if not p.is_file() or p.suffix not in WF_MEDIA:
        raise HTTPException(404, "not ready")
    return FileResponse(p, media_type=WF_MEDIA[p.suffix])


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


# Phase 4 parts (docs/PHASE4_CONTRACTS.md): each is a package with api.router; a part that is
# not built yet is skipped, so the parts can be developed and tested independently.
import importlib as _importlib
import logging as _logging

for _part in ("chat", "assets", "tasks", "settings"):
    try:
        app.include_router(_importlib.import_module(f"app.{_part}.api").router)
    except ModuleNotFoundError as _e:
        if _e.name not in (f"app.{_part}", f"app.{_part}.api"):
            raise
        _logging.getLogger(__name__).info("phase 4 part %s not present", _part)


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(WEB / "index.html")
