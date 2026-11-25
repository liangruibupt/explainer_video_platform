"""EVP mesh adapter (图生 3D): Hunyuan3D-2 shape (2mini / 2.0) + optional Paint texture.

  GET  /ping          200 once the preloaded shape model is on the GPU (503 while loading)
  POST /invocations   docs/PREVIS_CONTRACT.md 4.6 request ->
                      {"glb_b64", "seconds", "meta": {"model", "render_s", "faces", "textured",
                       "peak_vram_gb", ...timings}}

Weights come from /opt/ml/model (HY3DGEN_MODELS; layout in infra/mesh/stage_weights.py),
copied there by the worker bootstrap. Shape models load on first use and stay resident (both
fit next to Paint on a 32 GB card); MESH_PRELOAD (default: the default model) loads at
startup so /ping means "ready to generate". One request at a time.
"""
import base64, io, os, threading, time, traceback

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

MODELS = {"hunyuan3d-2mini": ("tencent/Hunyuan3D-2mini", "hunyuan3d-dit-v2-mini"),
          "hunyuan3d-2": ("tencent/Hunyuan3D-2", "hunyuan3d-dit-v2-0")}
PAINT = ("tencent/Hunyuan3D-2", "hunyuan3d-paint-v2-0-turbo")
DEFAULT = os.environ.get("MESH_DEFAULT_MODEL", "hunyuan3d-2mini")
PRELOAD = [m for m in os.environ.get("MESH_PRELOAD", DEFAULT).split(",") if m]
PAINT_MAX_FACES = 40000   # Paint's xatlas unwrap + bake is CPU-bound; Hunyuan paints ~40k faces
MAX_IMAGE_BYTES = 20 * 2**20
LIMITS = {"octree_resolution": (64, 512), "steps": (1, 100), "guidance": (0.0, 20.0),
          "max_faces": (500, 500000), "seed": (0, 2**31 - 1)}

app = FastAPI()
_lock = threading.Lock()
_pipes, _load_s = {}, {}
_ready = threading.Event()


def log(*a):
    print(time.strftime("%H:%M:%S"), "[mesh]", *a, flush=True)


class VramPeak:
    """Peak device memory in use (nvml: includes the CUDA context and allocator cache)."""

    def __init__(self, every=0.25):
        import pynvml
        pynvml.nvmlInit()
        self.nv, self.h = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)
        self.every, self.peak, self._stop = every, 0, threading.Event()

    def total_gb(self):
        return self.nv.nvmlDeviceGetMemoryInfo(self.h).total / 2**30

    def __enter__(self):
        self.peak = 0
        self._stop.clear()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self.nv.nvmlDeviceGetMemoryInfo(self.h).used)
            time.sleep(self.every)

    def __exit__(self, *a):
        self._stop.set()
        self.t.join()

    @property
    def peak_gb(self):
        return round(self.peak / 2**30, 1)


def _load(key):
    """Load one pipeline once: a shape model name, 'paint' or 'rembg'."""
    if key in _pipes:
        return _pipes[key], 0.0
    t0 = time.time()
    if key == "rembg":
        from hy3dgen.rembg import BackgroundRemover
        p = BackgroundRemover()
    elif key == "paint":
        from hy3dgen.texgen import Hunyuan3DPaintPipeline
        p = Hunyuan3DPaintPipeline.from_pretrained(PAINT[0], subfolder=PAINT[1])
    else:
        import torch
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
        repo, sub = MODELS[key]
        p = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            repo, subfolder=sub, variant="fp16", use_safetensors=True, device="cuda",
            dtype=torch.float16)
    dt = round(time.time() - t0, 1)
    _pipes[key], _load_s[key] = p, dt
    log("loaded", key, f"{dt} s")
    return p, dt


@app.on_event("startup")
def _startup():
    """Fail fast if an import breaks on this GPU, then preload in the background; a failed
    preload exits the process so the worker sees the container die and powers off."""
    import check_imports  # noqa: F401 - raises SystemExit on any failure: the container exits
    import torch
    log("gpu", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0),
        f"{torch.cuda.get_device_properties(0).total_memory / 2**30:.1f} GB", "preload", PRELOAD)

    def go():
        try:
            for m in PRELOAD:
                _load(m)
            _load("rembg")
            _ready.set()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            os._exit(1)
    threading.Thread(target=go, daemon=True).start()


@app.get("/ping")
def ping():
    if not _ready.is_set():
        raise HTTPException(503, "loading")
    return {"ok": True, "loaded": sorted(_pipes), "default": DEFAULT, "load_s": _load_s}


def _num(req, k, default, cast):
    v = req.get(k, default)
    try:
        v = cast(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{k} must be a number") from None
    lo, hi = LIMITS[k]
    if not lo <= v <= hi:
        raise HTTPException(400, f"{k} must be in [{lo}, {hi}]")
    return v


def parse(req: dict) -> dict:
    """Validate a request (contract 4.6); returns the normalised parameters."""
    if not isinstance(req, dict):
        raise HTTPException(400, "body must be a JSON object")
    model = req.get("model") or DEFAULT
    if model not in MODELS:
        raise HTTPException(400, f"unknown model {model!r} (one of {', '.join(MODELS)})")
    b64 = req.get("image_b64")
    if not isinstance(b64, str) or not b64:
        raise HTTPException(400, "image_b64 is required")
    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError:
        raise HTTPException(400, "image_b64 is not valid base64") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "image larger than 20 MB")
    return {"model": model, "image": raw, "remove_bg": bool(req.get("remove_bg", True)),
            "texture": bool(req.get("texture", False)),
            "octree_resolution": _num(req, "octree_resolution", 256, int),
            "steps": _num(req, "steps", 30, int), "guidance": _num(req, "guidance", 5.0, float),
            "seed": _num(req, "seed", 42, int), "max_faces": _num(req, "max_faces", 60000, int)}


def has_cutout_alpha(im) -> bool:
    """A real cut-out: at least 1 % of the pixels (nearly) fully transparent. Qwen-Image PNGs
    are RGBA with a few semi-transparent pixels (alpha >= 181) on a white background; treating
    those as cut-outs skipped rembg and the white floor was reconstructed as a slab."""
    if im.mode not in ("RGBA", "LA"):
        return False
    hist = im.getchannel("A").histogram()
    return sum(hist[:17]) >= 0.01 * im.size[0] * im.size[1]


def _image(raw: bytes, remove_bg: bool):
    """RGBA PIL image of the single object; background removed unless it already has alpha."""
    from PIL import Image, UnidentifiedImageError
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError) as e:
        raise HTTPException(400, f"cannot decode image: {e}") from None
    if max(im.size) > 4096:
        raise HTTPException(400, "image longer than 4096 px")
    if has_cutout_alpha(im) or not remove_bg:
        return im.convert("RGBA"), 0.0
    t0 = time.time()
    rembg, _ = _load("rembg")
    return rembg(im.convert("RGB")), round(time.time() - t0, 1)


def generate(p: dict) -> dict:
    import torch
    from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover
    t_all = time.time()
    img, rembg_s = _image(p["image"], p["remove_bg"])
    mon = VramPeak()
    torch.cuda.reset_peak_memory_stats()
    with mon:
        pipe, load_s = _load(p["model"])
        t0 = time.time()
        mesh = pipe(image=img, num_inference_steps=p["steps"], guidance_scale=p["guidance"],
                    octree_resolution=p["octree_resolution"], num_chunks=20000,
                    generator=torch.manual_seed(p["seed"]), output_type="trimesh")[0]
        shape_s = round(time.time() - t0, 1)
        raw_faces = len(mesh.faces)
        t0 = time.time()
        target = min(p["max_faces"], PAINT_MAX_FACES) if p["texture"] else p["max_faces"]
        mesh = FloaterRemover()(mesh)
        mesh = DegenerateFaceRemover()(mesh)
        if len(mesh.faces) > target:
            mesh = FaceReducer()(mesh, max_facenum=target)
        post_s = round(time.time() - t0, 1)
        paint_s = paint_load_s = 0.0
        if p["texture"]:
            paint, paint_load_s = _load("paint")
            t0 = time.time()
            mesh = paint(mesh, image=img)
            paint_s = round(time.time() - t0, 1)
        glb = mesh.export(file_type="glb")
    render_s = round(time.time() - t_all, 1)
    meta = {"model": p["model"], "render_s": render_s, "faces": int(len(mesh.faces)),
            "vertices": int(len(mesh.vertices)), "raw_faces": int(raw_faces),
            "textured": p["texture"], "peak_vram_gb": mon.peak_gb,
            "torch_peak_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1),
            "gpu": torch.cuda.get_device_name(0), "gpu_total_gb": round(mon.total_gb(), 1),
            "shape_s": shape_s, "post_s": post_s, "paint_s": paint_s, "rembg_s": rembg_s,
            "load_s": load_s, "paint_load_s": paint_load_s, "seed": p["seed"],
            "steps": p["steps"], "octree_resolution": p["octree_resolution"],
            "guidance": p["guidance"], "glb_bytes": len(glb)}
    log("done", meta)
    return {"glb_b64": base64.b64encode(glb).decode(), "seconds": render_s, "meta": meta}


@app.post("/invocations")
def invocations(req: dict = Body(...)):
    # plain def: FastAPI runs it in its thread pool, so /ping still answers during a job
    p = parse(req)
    with _lock:
        try:
            return JSONResponse(generate(p))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 - goes back to the queue as a failure
            traceback.print_exc()
            raise HTTPException(500, f"{type(e).__name__}: {e}"[:1800])
