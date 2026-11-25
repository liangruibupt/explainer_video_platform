"""图生 3D: POST /api/assets/model/image3d and the task routes /api/mesh/*.

Flow (docs/PREVIS_CONTRACT.md 4.6), one background thread per task:
  1. concept image: image.generate() with CONCEPT_PROMPT + the prompt (skipped when an image
     asset is given)
  2. mesh.generate() on the mesh GPU module -> raw GLB (Hunyuan's unit cube)
  3. normalize(): scale to the target size, ground at y = 0, into the library (part A's
     assets_build.normalize; injectable through set_normalize() for tests and integration)

Store: data/mesh3d/<id>/ with req.json, state.json, concept.png (or input.png), raw.glb.
state.status is queued|running|done|failed (the task-center shape of the other modules);
list_all() is the task-center source (integration adds it to tasks/collect.py).

Routes: POST /api/assets/model/image3d -> 202 {task_id}; GET /api/mesh/status;
GET /api/mesh/tasks; GET /api/mesh/tasks/{id}; GET /api/mesh/tasks/{id}/{concept|input|raw};
POST /api/mesh/tasks/{id}/retry (reuses the concept image and the raw GLB it already has).
"""
import json
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import image, mesh
from .pipeline import DATA

router = APIRouter()
ROOT = DATA / "mesh3d"
ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
CONCEPT_PROMPT = "单个物体，纯白背景，三分之二侧视角，完整不裁切，无投影，画面中没有任何文字"
AXES = ("w", "h", "d", "longest")
PROMPT_MAX = 500
FILES = {"concept": ("concept.png", "image/png"), "input": ("input.png", "image/png"),
         "raw": ("raw.glb", "model/gltf-binary")}
LIVE = ("queued", "running")
_lock = threading.Lock()
_normalize = None


def set_normalize(fn) -> None:
    """Inject normalize(raw_glb: Path, target, source, name, project_id) -> library record.
    None = part A's assets_build.normalize (imported on first use)."""
    global _normalize
    _normalize = fn


def normalize(raw_glb: Path, target: dict, source: dict, name: str, project_id=None) -> dict:
    fn = _normalize
    if fn is None:
        from .previs import assets_build
        fn = assets_build.normalize
    return fn(raw_glb, target, source, name, project_id)


# ---------------------------------------------------------------- inputs

def check_target(target) -> dict:
    """{"axis": w|h|d|longest, "metres": >0} (TargetSize in core/types.ts)."""
    if not isinstance(target, dict):
        raise mesh.MeshError("target 必须是 {axis, metres}")
    axis = target.get("axis", "longest")
    if axis not in AXES:
        raise mesh.MeshError(f"target.axis 只能是 {' / '.join(AXES)}")
    try:
        m = float(target.get("metres"))
    except (TypeError, ValueError):
        raise mesh.MeshError("target.metres 必须是数字（米）") from None
    if not 0.001 <= m <= 1000:
        raise mesh.MeshError("target.metres 超出范围（0.001–1000 米）")
    return {"axis": axis, "metres": m}


def concept_prompt(prompt: str) -> str:
    return f"{prompt.strip()}。{CONCEPT_PROMPT}"


def _asset_image(asset_id: str) -> bytes:
    from .assets import store
    if not store.valid_id(asset_id):
        raise mesh.MeshError(f"素材 id 格式不对：{asset_id}")
    meta = store.get(asset_id)
    if not meta:
        raise mesh.MeshError(f"素材不存在：{asset_id}")
    if meta.get("kind") != "image":
        raise mesh.MeshError(f"素材 {asset_id} 是 {meta.get('kind')}，这里需要图片")
    return store.asset_path(asset_id).read_bytes()


def prepare(body: dict) -> tuple:
    """Check a POST body; returns (req dict, input image PNG or None). Raises MeshError."""
    if not isinstance(body, dict):
        raise mesh.MeshError("body 必须是 JSON 对象")
    prompt = str(body.get("prompt") or "").strip()
    asset = str(body.get("image_asset") or "").strip()
    if bool(prompt) == bool(asset):
        raise mesh.MeshError("提示词（prompt）和图片素材（image_asset）需要且只能给一个")
    if len(prompt) > PROMPT_MAX:
        raise mesh.MeshError(f"提示词超过 {PROMPT_MAX} 字")
    target = check_target(body.get("target"))
    model = str(body.get("model") or "")
    texture = bool(body.get("texture", False))
    seed = body.get("seed", 42)
    opts = mesh.check_options(model, seed)
    png = mesh.prepare_image(_asset_image(asset)) if asset else None
    name = str(body.get("name") or prompt or asset)[:60]
    project_id = body.get("project_id") or None
    req = {"prompt": prompt, "image_asset": asset or None, "target": target,
           "model": opts["model"], "texture": texture, "seed": opts["seed"],
           "name": name, "project_id": project_id}
    return req, png


# ---------------------------------------------------------------- the work

def run_image3d(req: dict, work: Path, png: bytes | None = None, note=None, on_wait=None) -> dict:
    """concept image (when no input) -> mesh -> normalize. Reuses concept.png / input.png
    and raw.glb already in `work` (retry). Returns {"asset", "mesh_meta"}."""
    note = note or (lambda msg: None)
    work.mkdir(parents=True, exist_ok=True)
    given = png is not None or bool(req.get("image_asset")) or (work / "input.png").exists()
    src = work / ("input.png" if given else "concept.png")
    if png is not None and not src.exists():
        src.write_bytes(png)
    if not src.exists():
        note("生成概念图（图像模块）")
        imgs = image.generate(concept_prompt(req["prompt"]), ratio="1:1", seed=req["seed"],
                              on_wait=on_wait)
        if not imgs:
            raise RuntimeError("图像模块没有返回图片")
        src.write_bytes(imgs[0])
    raw = work / "raw.glb"
    meta_p = work / "mesh_meta.json"
    if not raw.exists():
        note(f"生成 3D 网格（{req['model']}" + ("，带贴图" if req["texture"] else "") + "）")
        meta = {}
        glb = mesh.generate(src.read_bytes(), model=req["model"], texture=req["texture"],
                            seed=req["seed"], on_wait=on_wait, meta_out=meta)
        raw.write_bytes(glb)
        meta_p.write_text(json.dumps(meta, ensure_ascii=False))
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    note("规范化尺寸并入库")
    source = {"type": "image3d", "model": req["model"], "seed": req["seed"],
              "target": req["target"]}
    if req.get("image_asset"):
        source["image_asset"] = req["image_asset"]
    if req.get("prompt"):
        source["prompt"] = req["prompt"]
    rec = normalize(raw, req["target"], source, req.get("name") or "图生 3D",
                    req.get("project_id"))
    return {"asset": rec, "mesh_meta": meta}


def _dir(task_id: str) -> Path:
    if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id):
        raise HTTPException(404, "no such task")
    return ROOT / task_id


def _state(d: Path, **kw) -> dict:
    with _lock:
        p = d / "state.json"
        s = json.loads(p.read_text()) if p.exists() else {}
        if "note" in kw:
            s["notes"] = (s.get("notes") or []) + [{"t": time.time(), "text": kw["note"]}]
        s.update(kw, updated=time.time())
        tmp = d / f".state.{uuid.uuid4().hex[:6]}.tmp"
        tmp.write_text(json.dumps(s, ensure_ascii=False))
        tmp.replace(p)
        return s


def _run(task_id: str) -> None:
    d = ROOT / task_id
    req = json.loads((d / "req.json").read_text())
    t0 = time.time()
    last = {"m": -1}

    def on_wait(sec):
        m = sec // 60
        if m != last["m"]:
            last["m"] = m
            _state(d, waited=sec, note=f"等待 GPU（{m} 分钟）" if m else "已提交，等待 GPU")
        else:
            _state(d, waited=sec)

    try:
        _state(d, status="running", error=None)
        out = run_image3d(req, d, note=lambda msg: _state(d, step=msg, note=msg), on_wait=on_wait)
        rec = out["asset"] or {}
        _state(d, status="done", step="done", asset_id=rec.get("id"), asset=rec,
               mesh_meta=out["mesh_meta"], files=_files(d), seconds=round(time.time() - t0),
               note="完成，已入库" + (f"：{rec.get('id')}" if rec.get("id") else ""))
    except Exception as e:  # noqa: BLE001 - shown in the task center
        _state(d, status="failed", error=str(e)[:500], files=_files(d),
               seconds=round(time.time() - t0), note="失败：" + str(e)[:200])


def _files(d: Path) -> list:
    return [n for n, _ in FILES.values() if (d / n).exists()]


def create(req: dict, png: bytes | None) -> dict:
    task_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = ROOT / task_id
    d.mkdir(parents=True, exist_ok=False)
    if png is not None:
        (d / "input.png").write_bytes(png)
    (d / "req.json").write_text(json.dumps(req, ensure_ascii=False))
    s = _state(d, id=task_id, status="queued", step="queued", created=time.time(), error=None,
               title=req["name"], files=_files(d), **req, note="已排队")
    threading.Thread(target=_run, args=(task_id,), daemon=True).start()
    return s


def get(task_id: str) -> dict:
    p = _dir(task_id) / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def list_all(limit: int = 60) -> list:
    """Task-center source: newest first."""
    if not ROOT.exists():
        return []
    out = []
    for p in ROOT.glob("*/state.json"):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda s: s.get("created", 0), reverse=True)[:limit]


def _public(s: dict) -> dict:
    if not s:
        return s
    have = set(s.get("files") or [])
    return s | {"task_id": s["id"],
                "urls": {k: f"/api/mesh/tasks/{s['id']}/{k}" for k, (n, _) in FILES.items()
                         if n in have}}


# ---------------------------------------------------------------- routes

@router.post("/api/assets/model/image3d", status_code=202)
def image3d_create(body: dict):
    try:
        req, png = prepare(body)
    except mesh.MeshError as e:
        raise HTTPException(422, str(e)) from None
    if not mesh.configured():
        raise HTTPException(503, "图生 3D 未部署：缺少 infra/gpu_ec2/modules/mesh.json")
    return _public(create(req, png))


@router.get("/api/mesh/status")
def mesh_status():
    try:
        return mesh.status()
    except Exception as e:  # noqa: BLE001 - e.g. no AWS credentials
        return {"deployed": False, "state": "unavailable", "error": str(e)[:200]}


@router.get("/api/mesh/tasks")
def mesh_tasks():
    return [_public(s) for s in list_all()]


@router.get("/api/mesh/tasks/{task_id}")
def mesh_task(task_id: str):
    s = get(task_id)
    if not s:
        raise HTTPException(404, "no such task")
    return _public(s)


@router.post("/api/mesh/tasks/{task_id}/retry")
def mesh_retry(task_id: str):
    d = _dir(task_id)
    s = get(task_id)
    if not s:
        raise HTTPException(404, "no such task")
    if s.get("status") != "failed":
        raise HTTPException(409, "只有失败的任务可以重试")
    s = _state(d, status="queued", step="queued", error=None, note="重试：已排队")
    threading.Thread(target=_run, args=(task_id,), daemon=True).start()
    return _public(s)


@router.get("/api/mesh/tasks/{task_id}/{name}")
def mesh_file(task_id: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "no such file")
    fname, mime = FILES[name]
    p = _dir(task_id) / fname
    if not p.is_file():
        raise HTTPException(404, "not ready")
    return FileResponse(p, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})
