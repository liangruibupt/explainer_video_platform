"""HTTP routes for 3D model assets (docs/PREVIS_CONTRACT.md section 11, part A).

POST /api/assets/model/build    {spec, project_id?} -> library record; 422 + issues on a bad spec
POST /api/assets/model/upload   multipart GLB + target_axis + target_metres -> normalized record
GET  /api/assets/{id}/turntable turntable.mp4 of a model asset
(/api/assets/{id}/file and /thumb in api.py already serve file.glb and thumb.jpg.)

main.py includes `router` at integration.
"""
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..previs import assets_build
from . import projects, store

router = APIRouter()
PROJECT_FORM = r"^(|proj_[0-9]{8}-[0-9]{6}-[0-9a-f]{6})$"
MAX_SPEC_BYTES = 1 << 20


def public(m: dict) -> dict:
    """The api.py record plus turntable_url for models."""
    return m | {"url": f"/api/assets/{m['id']}/file",
                "thumb_url": f"/api/assets/{m['id']}/thumb" if m.get("thumb") else None,
                "turntable_url": f"/api/assets/{m['id']}/turntable" if m.get("turntable") else None}


def _check_project(project_id):
    if project_id and not projects.get(project_id):
        raise HTTPException(422, "no such project")


def _issues(e: assets_build.BuildError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(e), "issues": e.issues})


class BuildIn(BaseModel):
    spec: dict
    project_id: str = Field(default="", pattern=PROJECT_FORM)


@router.post("/api/assets/model/build", status_code=201)
def model_build(body: BuildIn):
    _check_project(body.project_id)
    if len(json.dumps(body.spec)) > MAX_SPEC_BYTES:
        raise HTTPException(413, "spec 超过 1 MB")
    try:
        meta = assets_build.build(body.spec, project_id=body.project_id or None)
    except assets_build.BuildError as e:
        return _issues(e)
    except store.AssetError as e:
        raise HTTPException(e.status, str(e)) from None
    return public(meta)


@router.post("/api/assets/model/upload", status_code=201)
def model_upload(file: UploadFile = File(...),
                 target_axis: Literal["w", "h", "d", "longest"] = Form(...),
                 target_metres: float = Form(..., gt=0, le=1000),
                 name: str = Form("", max_length=200),
                 max_tris: int = Form(60000, ge=100, le=200000),
                 project_id: str = Form("", pattern=PROJECT_FORM)):
    _check_project(project_id)
    store.ROOT.mkdir(parents=True, exist_ok=True)
    tmp = store.ROOT / f".upload-{uuid.uuid4().hex}.glb"
    filename = Path(file.filename or "upload.glb").name
    try:
        n = 0
        with open(tmp, "wb") as out:
            while chunk := file.file.read(1 << 20):
                n += len(chunk)
                if n > store.MAX_BYTES:
                    raise HTTPException(413, "文件超过 200 MB")
                out.write(chunk)
        with open(tmp, "rb") as f:
            if f.read(4) != b"glTF":
                raise HTTPException(415, "只接受 GLB（glTF 2.0 二进制）文件")
        target = {"axis": target_axis, "metres": target_metres}
        source = {"type": "upload", "filename": store.clean_name(filename, "upload.glb"),
                  "target": target}
        try:
            meta = assets_build.normalize(tmp, target, source,
                                          name or Path(filename).stem or "上传模型",
                                          project_id=project_id or None, max_tris=max_tris)
        except assets_build.BuildError as e:
            return _issues(e)
        except store.AssetError as e:
            raise HTTPException(e.status, str(e)) from None
    finally:
        tmp.unlink(missing_ok=True)
    return public(meta)


@router.get("/api/assets/{asset_id}/turntable")
def model_turntable(asset_id: str):
    if not store.valid_id(asset_id):
        raise HTTPException(404, "no such asset")
    p = store.turntable_path(asset_id)
    if not p:
        raise HTTPException(404, "no turntable")
    return FileResponse(p, media_type="video/mp4")
