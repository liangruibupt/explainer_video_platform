"""EVP avatar adapter: one HTTP server for InfiniteTalk and LongCat-Video-Avatar-1.5.

  GET  /ping          200 once the process is up (models load lazily on first request)
  POST /invocations   docs/AVATAR_CONTRACT.md request -> {"video_b64", "model", "seconds",
                      "render_s", "frames", "size", "fast", "peak_vram_gb", ...}

The requested model's weights are copied from the pinned S3 prefix in
/opt/ml/model/index.json to /weights (NVMe) on first use, then the model stays resident.
Asking for the other model frees the first (one GPU, both are 14B+ class).
"""
import base64, os, shutil, tempfile, threading, time, traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import common
from common import log

DEFAULT = os.environ.get("AVATAR_DEFAULT_MODEL", "infinitetalk")
app = FastAPI()
_lock = threading.Lock()
_runners = {}
_active = {"name": None}
MAX_AUDIO_S = 600


def runner(name):
    if name not in ("infinitetalk", "longcat"):
        raise HTTPException(400, f"unknown model {name!r}")
    fetch_s = 0.0
    if _active["name"] != name:
        if _active["name"]:
            log("switching model", _active["name"], "->", name)
            _runners.pop(_active["name"]).unload()
        wdir, fetch_s = common.fetch(name)
        common.init_dist()
        if name == "infinitetalk":
            from it_runner import InfiniteTalk
            _runners[name] = InfiniteTalk(wdir)
        else:
            from lc_runner import LongCat
            _runners[name] = LongCat(wdir)
        _active["name"] = name
    return _runners[name], fetch_s


@app.on_event("startup")
def _prefetch():
    """Fail fast if the model code cannot import on this GPU (both codebases touch CUDA at
    import, so the CPU build check cannot cover it), then copy the default model's weights
    while the first request is on its way."""
    import check_imports  # noqa: F401 - raises SystemExit on any failure: the container exits

    def go():
        try:
            common.fetch(DEFAULT)
        except Exception as e:  # noqa: BLE001 - the request path retries and reports it
            log("prefetch failed", e)
    threading.Thread(target=go, daemon=True).start()


@app.get("/ping")
def ping():
    return {"ok": True, "active": _active["name"], "default": DEFAULT}


def _ext(b: bytes, kind):
    if kind == "image":
        return ".png" if b[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    if kind == "video":
        return ".mp4"
    return ".wav" if b[:4] == b"RIFF" else ".mp3"


@app.post("/invocations")
async def invocations(request: Request):
    req = await request.json()
    model = req.get("model") or DEFAULT
    if not req.get("audio_b64"):
        raise HTTPException(400, "audio_b64 is required")
    if not (req.get("image_b64") or req.get("video_b64")):
        raise HTTPException(400, "image_b64 or video_b64 is required")
    size = str(req.get("size") or "480")
    if size not in ("480", "720"):
        raise HTTPException(400, "size must be '480' or '720'")
    fast = bool(req.get("fast", True))
    seed = int(req.get("seed", 42))
    prompt = req.get("prompt") or "A person talking to the camera, natural expression and gestures."
    with _lock:
        tmp = Path(tempfile.mkdtemp(prefix="avatar-"))
        try:
            img = vid = None
            if req.get("image_b64"):
                b = base64.b64decode(req["image_b64"]); img = tmp / ("ref" + _ext(b, "image")); img.write_bytes(b)
            if req.get("video_b64"):
                b = base64.b64decode(req["video_b64"]); vid = tmp / "ref.mp4"; vid.write_bytes(b)
            b = base64.b64decode(req["audio_b64"]); src = tmp / ("in" + _ext(b, "audio")); src.write_bytes(b)
            wav = tmp / "speech16k.wav"
            seconds = common.to_wav16k(src, wav)
            if seconds > MAX_AUDIO_S:
                raise HTTPException(400, f"audio {seconds:.0f} s > {MAX_AUDIO_S} s")
            t0 = time.time()
            r, fetch_s = runner(model)
            mon = common.VramPeak()
            with mon:
                out = r.generate(str(img) if img else None, str(vid) if vid else None, str(wav), prompt,
                                 size, fast, seed, req.get("audio_cfg"), req.get("text_cfg"), tmp,
                                 int8=req.get("int8"), gpu_gb=mon.total_gb())
                mp4 = tmp / "out.mp4"
                n, (w, h) = common.encode(out.pop("frames"), out.pop("fps"), src, mp4, seconds=seconds)
            render_s = time.time() - t0
            meta = {k: v for k, v in out.items() if k != "audio"}
            res = {"video_b64": base64.b64encode(mp4.read_bytes()).decode(), "model": model,
                   "seconds": round(seconds, 2), "render_s": round(render_s, 1),
                   "fetch_s": round(fetch_s, 1), "frames": n, "width": w, "height": h, "size": size,
                   "fast": fast, "seed": seed, "peak_vram_gb": mon.peak_gb,
                   "gpu_total_gb": round(mon.total_gb(), 1), **meta}
            log("done", model, size, "fast" if fast else "full", f"{seconds:.1f}s audio",
                f"{render_s:.0f}s render", f"{mon.peak_gb} GB")
            return JSONResponse(res)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 - goes back to the queue as a failure
            traceback.print_exc()
            raise HTTPException(500, f"{type(e).__name__}: {e}"[:1800])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
