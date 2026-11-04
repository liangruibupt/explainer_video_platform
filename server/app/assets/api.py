"""HTTP routes for the asset library (/api/assets/*) and projects (/api/projects/*)."""
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import projects, store

router = APIRouter()
ID_PATTERN = r"^asset_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$"
PROJECT_PATTERN = r"^proj_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$"


def _aid(asset_id: str) -> str:
    if not store.valid_id(asset_id):
        raise HTTPException(404, "no such asset")
    return asset_id


def _pid(project_id: str) -> str:
    if not projects.valid_id(project_id):
        raise HTTPException(404, "no such project")
    return project_id


def _check_project(project_id):
    if project_id and not projects.get(project_id):
        raise HTTPException(422, "no such project")


def _public(m: dict) -> dict:
    return m | {"url": f"/api/assets/{m['id']}/file",
                "thumb_url": f"/api/assets/{m['id']}/thumb" if m.get("thumb") else None}


def _fail(e: store.AssetError):
    raise HTTPException(e.status, str(e))


# ---------------------------------------------------------------- assets

@router.get("/api/assets")
def asset_list(kind: Optional[Literal["image", "audio", "video"]] = None,
               project_id: Optional[str] = Query(None, pattern=PROJECT_PATTERN)):
    return [_public(m) for m in store.list_all(kind or "", project_id or "")]


@router.post("/api/assets", status_code=201)
def asset_upload(file: UploadFile = File(...), name: str = Form("", max_length=200),
                 project_id: str = Form("", pattern=r"^(|proj_[0-9]{8}-[0-9]{6}-[0-9a-f]{6})$")):
    _check_project(project_id)
    store.ROOT.mkdir(parents=True, exist_ok=True)
    tmp = store.ROOT / f".upload-{uuid.uuid4().hex}"
    try:
        n = 0
        with open(tmp, "wb") as out:
            while chunk := file.file.read(1 << 20):
                n += len(chunk)
                if n > store.MAX_BYTES:
                    raise HTTPException(413, "文件超过 200 MB")
                out.write(chunk)
        try:
            meta = store.ingest(tmp, file.filename or "", name=name,
                                project_id=project_id or None, source={"type": "upload"})
        except store.AssetError as e:
            _fail(e)
    finally:
        tmp.unlink(missing_ok=True)
    return _public(meta)


class ImportIn(BaseModel):
    source: Literal["job", "image", "video", "music", "wf_run"]
    id: str = Field(pattern=r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
    file: str = Field(default="", pattern=r"^(|[A-Za-z0-9_-]{1,64}\.[a-z0-9]{1,5})$")
    name: str = Field(default="", max_length=200)
    project_id: str = Field(default="", pattern=r"^(|proj_[0-9]{8}-[0-9]{6}-[0-9a-f]{6})$")


@router.post("/api/assets/import", status_code=201)
def asset_import(body: ImportIn):
    _check_project(body.project_id)
    try:
        meta = projects.import_work(body.source, body.id, body.file, body.name,
                                    body.project_id or None)
    except store.AssetError as e:
        _fail(e)
    return _public(meta)


@router.get("/api/assets/{asset_id}")
def asset_get(asset_id: str):
    m = store.get(_aid(asset_id))
    if not m:
        raise HTTPException(404, "no such asset")
    return _public(m)


@router.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: str):
    try:
        p = store.asset_path(_aid(asset_id))
    except KeyError:
        raise HTTPException(404, "no such asset") from None
    m = store.get(asset_id)
    return FileResponse(p, media_type=m.get("mime"),
                        headers={"X-Content-Type-Options": "nosniff"})


@router.get("/api/assets/{asset_id}/thumb")
def asset_thumb(asset_id: str):
    p = store.thumb_path(_aid(asset_id))
    if not p:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(p, media_type="image/jpeg")


class AssetPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    project_id: Optional[str] = Field(default=None,
                                      pattern=r"^(|proj_[0-9]{8}-[0-9]{6}-[0-9a-f]{6})$")


@router.patch("/api/assets/{asset_id}")
def asset_patch(asset_id: str, body: AssetPatch):
    _aid(asset_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("project_id"):
        _check_project(changes["project_id"])
    try:
        return _public(store.update(asset_id, **changes))
    except KeyError:
        raise HTTPException(404, "no such asset") from None


@router.delete("/api/assets/{asset_id}")
def asset_delete(asset_id: str):
    if not store.delete(_aid(asset_id)):
        raise HTTPException(404, "no such asset")
    return {"deleted": asset_id}


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.get("/api/projects")
def project_list():
    out = []
    counts = {}
    for m in store.list_all():
        if m.get("project_id"):
            counts[m["project_id"]] = counts.get(m["project_id"], 0) + 1
    for p in projects.list_all():
        out.append(p | {"asset_count": counts.get(p["id"], 0)})
    return out


@router.post("/api/projects", status_code=201)
def project_create(body: ProjectIn):
    return projects.create(body.name, body.note)


@router.get("/api/projects/{project_id}")
def project_get(project_id: str):
    p = projects.get(_pid(project_id))
    if not p:
        raise HTTPException(404, "no such project")
    return p


@router.patch("/api/projects/{project_id}")
def project_patch(project_id: str, body: ProjectPatch):
    try:
        return projects.update(_pid(project_id), **body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(404, "no such project") from None


@router.delete("/api/projects/{project_id}")
def project_delete(project_id: str):
    if not projects.delete(_pid(project_id)):
        raise HTTPException(404, "no such project")
    return {"deleted": project_id}


@router.get("/api/projects/{project_id}/items")
def project_items(project_id: str):
    if not projects.get(_pid(project_id)):
        raise HTTPException(404, "no such project")
    items = projects.items(project_id)
    items["assets"] = [_public(m) for m in items["assets"]]
    return items
