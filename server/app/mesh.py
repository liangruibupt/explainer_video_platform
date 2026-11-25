"""Client for the mesh module (图生 3D: Hunyuan3D-2) on the self-managed EC2 GPU backend.

Contract: docs/PREVIS_CONTRACT.md section 4.6; deployment and measurements: docs/MESH.md.
Worker config: infra/gpu_ec2/modules/mesh.json. generate() queues a job in S3, starts the
worker instance if none is running and waits for the GLB, so it works the same warm or
from 0. The GLB is returned raw (Hunyuan's own unit cube, no real size): the caller runs
assets_build.normalize() on it.

  status()                     same shape as image.status(), plus models / default / estimate
  generate(image, model, texture, seed, **kw) -> GLB bytes
  prepare_image(raw) -> PNG    validate + re-encode one input image (trust boundary)
"""
import base64
import io
import json
from pathlib import Path

from . import gpu_client
from .gpu_ec2 import MODULES

MODULE = "mesh"
MODELS = {"hunyuan3d-2mini": "Hunyuan3D-2mini（0.6B，快）", "hunyuan3d-2": "Hunyuan3D-2.0（1.1B）"}
IMAGE_MAX_BYTES = 20 * 2**20
IMAGE_MIN_EDGE, IMAGE_MAX_EDGE = 128, 8192
SEND_EDGE = 1024            # the shape model works at 512 px; 1024 keeps detail for Paint
TIMEOUT_S = 3600
# extra worker parameters generate(**kw) accepts: name -> (cast, lo, hi)
PARAMS = {"remove_bg": (bool, None, None), "octree_resolution": (int, 64, 512),
          "steps": (int, 1, 100), "guidance": (float, 0.0, 20.0), "max_faces": (int, 500, 500000)}
COLD_MIN, USD_PER_HOUR = 8.0, 2.24


class MeshError(ValueError):
    """Bad input (shown to the user as is)."""


def config_path() -> Path:
    return MODULES / f"{MODULE}.json"


def configured() -> bool:
    return config_path().is_file()


def config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def default_model() -> str:
    m = config().get("default_model")
    return m if m in MODELS else "hunyuan3d-2mini"


def estimate(model: str = "", texture: bool = False) -> dict:
    """Rough seconds on a warm worker and the cold start, from mesh.json's measurements."""
    cfg = config()
    rs = cfg.get("render_s") or {}
    m = model or default_model()
    sec = float(rs.get(m, 60)) + (float(rs.get("texture_extra", 180)) if texture else 0.0)
    return {"model": m, "texture": texture, "render_s": sec,
            "cold_min": float(cfg.get("cold_min") or COLD_MIN),
            "usd_per_hour": float(cfg.get("usd_per_hour") or USD_PER_HOUR)}


def status() -> dict:
    """{'deployed', 'state': ready|starting|idle|failed|unavailable, 'instances', 'desired',
    'instance_type', 'models', 'default_model', 'estimate'}."""
    base = {"models": MODELS, "default_model": default_model(), "estimate": estimate()}
    if not configured():
        return dict(base, deployed=False, state="unavailable", instances=0, desired=0,
                    instance_type="", backend="ec2", label="未部署",
                    error="未部署：缺少 infra/gpu_ec2/modules/mesh.json")
    return dict(gpu_client.status(MODULE), **base)


def prepare_image(raw: bytes) -> bytes:
    """Check an input image (png / jpg / webp) and return it as PNG, alpha kept (a cut-out
    skips background removal on the worker), long edge at most SEND_EDGE."""
    from PIL import Image, UnidentifiedImageError
    if not raw:
        raise MeshError("图片是空的")
    if len(raw) > IMAGE_MAX_BYTES:
        raise MeshError(f"图片超过 {IMAGE_MAX_BYTES // 2**20} MB")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise MeshError("图片只支持 png / jpg / webp")
            w, h = im.size
            if min(w, h) < IMAGE_MIN_EDGE:
                raise MeshError(f"图片太小（短边至少 {IMAGE_MIN_EDGE}px，现在 {min(w, h)}px）")
            if max(w, h) > IMAGE_MAX_EDGE:
                raise MeshError(f"图片太大（长边最多 {IMAGE_MAX_EDGE}px）")
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if alpha else "RGB")
            if max(w, h) > SEND_EDGE:
                im.thumbnail((SEND_EDGE, SEND_EDGE), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise MeshError(f"图片无法解析：{e}") from None


def _params(kw: dict) -> dict:
    out = {}
    for k, v in kw.items():
        if k not in PARAMS:
            raise MeshError(f"未知参数：{k}")
        cast, lo, hi = PARAMS[k]
        try:
            v = cast(v)
        except (TypeError, ValueError):
            raise MeshError(f"{k} 格式不对") from None
        if lo is not None and not lo <= v <= hi:
            raise MeshError(f"{k} 超出范围 [{lo}, {hi}]")
        out[k] = v
    return out


def check_options(model: str = "", seed=42, **kw) -> dict:
    """Validate everything but the image; returns {model, seed, **kw} normalised."""
    if model and model not in MODELS:
        raise MeshError(f"不支持的模型：{model}（可选 {', '.join(MODELS)}）")
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise MeshError("seed 必须是整数") from None
    if not 0 <= seed < 2**31:
        raise MeshError("seed 超出范围")
    return dict(_params(kw), model=model or default_model(), seed=seed)


def request(image: bytes, model: str = "", texture: bool = False, seed: int = 42, **kw) -> dict:
    """The worker request (contract 4.6) for one image; raises MeshError on bad input."""
    opts = check_options(model, seed, **kw)
    req = {"image_b64": base64.b64encode(prepare_image(image)).decode(),
           "remove_bg": True, "texture": bool(texture), "octree_resolution": 256, "steps": 30,
           "guidance": 5.0, "max_faces": 60000}
    req.update(opts)
    return req


def generate(image: bytes, model: str = "", texture: bool = False, seed: int = 42,
             timeout: int = TIMEOUT_S, on_wait=None, meta_out: dict | None = None, **kw) -> bytes:
    """Blocking: image bytes -> raw GLB bytes (not normalised). on_wait(seconds) is called
    while waiting; meta_out (a dict) receives the worker's meta (faces, timings, VRAM...)."""
    if not configured():
        raise RuntimeError("图生 3D 未部署：缺少 infra/gpu_ec2/modules/mesh.json")
    req = request(image, model, texture, seed, **kw)
    res = gpu_client.run(MODULE, req, timeout, on_wait)
    blobs = gpu_client.gpu(MODULE).fetch(res, "glb_b64")
    if not blobs or not blobs[0] or blobs[0][:4] != b"glTF":
        raise RuntimeError("mesh worker 没有返回 GLB")
    if meta_out is not None:
        meta_out.update(res.get("meta") or {})
        if isinstance(res.get("instance"), dict):
            meta_out["instance_type"] = res["instance"].get("instance_type")
        meta_out["worker_s"] = res.get("worker_s")
    return blobs[0]
