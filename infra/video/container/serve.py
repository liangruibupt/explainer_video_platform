"""SageMaker inference adapter for MiniMax-H3 (FL2VA partition) on a single g7e GPU.

The real work is done by `sglang serve`, which this adapter launches as a child on
127.0.0.1:8000 and then bridges to SageMaker's contract:

GET  /ping         200 only once sglang's /health is 200 (i.e. weights loaded + warmed).
POST /invocations  JSON in, JSON out. Translates one request into H3's
                   POST /v1/videos, polls GET /v1/videos/{id}, downloads
                   /v1/videos/{id}/content, and returns the MP4 as base64.

SageMaker starts the container as `docker run IMAGE serve`; the start script ignores
that `serve` argument and runs uvicorn here.

Request:  {"mode": "t2va|fl2va", "prompt": str, "aspect": "16:9|9:16",
           "short_edge": 768, "duration": 5.0, "seed": int, "steps": 8,
           "first_frame": base64 PNG (fl2va), "last_frame": base64 PNG (fl2va, optional)}
Response: {"video_b64": str, "seconds": float, "width": W, "height": H,
           "frames": N, "fps": F}

Weights (ModelDataSource S3Prefix, uncompressed) land at:
  /opt/ml/model/MiniMax-H3        the FL2VA partition (basename MUST stay MiniMax-H3)
  /opt/ml/model/nvfp4/...         the NVFP4 Turbo transformer checkpoint
"""
import base64, io, json, os, subprocess, threading, time, urllib.error, urllib.request

from fastapi import FastAPI, Request, Response

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
H3_MODEL = f"{MODEL_DIR}/MiniMax-H3"          # basename must stay MiniMax-H3 (registry.py)
TURBO = f"{MODEL_DIR}/nvfp4/nvfp4_fl2va_turbo.safetensors"
SGLANG_HOST, SGLANG_PORT = "127.0.0.1", 8000
SGLANG = f"http://{SGLANG_HOST}:{SGLANG_PORT}"

# Only the default-tier shape is warmed, to keep cold start short. 768p 16:9 is the
# released resolution (short edge 768 needs no SGLANG_MINIMAX_H3_EXTRA_SHORT_EDGES).
WARMUP = "1344x768"

app = FastAPI()
state = {"ready": False, "error": None, "proc": None}


def _sglang_cmd():
    # The base image (evp/minimax-h3) already has the sglang H3 patches, SageAttention
    # (sm_120) and the NVFP4 python fixes baked in; the NVFP4/Cache-DiT envs are set on
    # the SageMaker Model Environment and inherited here. Flags mirror the reference
    # repo's fl2va single-GPU serve (serve.sh + g7e_dev_levers.sh), 96 GB card:
    # text-encoder + VAE offload is mandatory, SageAttention for the DiT, the three
    # non-DiT components forced to torch_sdpa, and the Turbo NVFP4 transformer loaded.
    return [
        "sglang", "serve",
        "--model-path", H3_MODEL,
        "--model-variant", "fl2va",
        "--num-gpus", "1",
        "--tp-size", "1",
        "--ulysses-degree", "1",
        "--performance-mode", "speed",
        "--encoder-parallel", "auto",
        "--text-encoder-cpu-offload", "--vae-cpu-offload",
        "--layerwise-offload-components", "text_encoder",
        "--transformer-weights-path", TURBO,
        "--attention-backend", "sage_attn",
        "--component-attention-backends",
        "text_encoder=torch_sdpa,audio_vae=torch_sdpa,video_vae=torch_sdpa",
        "--warmup-resolutions", WARMUP,
        "--master-port", "30100", "--scheduler-port", "5700", "--strict-ports",
        "--host", SGLANG_HOST, "--port", str(SGLANG_PORT),
    ]


def launch():
    """Start sglang, stream its log to stdout (SageMaker CloudWatch), poll for health."""
    try:
        env = dict(os.environ)
        env.setdefault("SGLANG_USE_RUNAI_MODEL_STREAMER", "0")
        log = open("/tmp/sglang.log", "wb")
        proc = subprocess.Popen(_sglang_cmd(), cwd="/sgl-workspace/sglang",
                                stdout=log, stderr=subprocess.STDOUT, env=env)
        state["proc"] = proc
        print("sglang launched pid", proc.pid, flush=True)
        # Mirror the child log to container stdout so it reaches CloudWatch.
        threading.Thread(target=_tail, args=("/tmp/sglang.log",), daemon=True).start()
        # Poll /health. First start loads ~90 GB of weights + warmup; allow a long window.
        deadline = time.time() + 3300
        while time.time() < deadline:
            if proc.poll() is not None:
                state["error"] = f"sglang exited early rc={proc.returncode}"
                print(state["error"], flush=True)
                return
            try:
                with urllib.request.urlopen(f"{SGLANG}/health", timeout=5) as r:
                    if r.status == 200:
                        state["ready"] = True
                        print("sglang healthy; /ping now 200", flush=True)
                        return
            except Exception:
                pass
            time.sleep(10)
        state["error"] = "sglang did not become healthy in time"
        print(state["error"], flush=True)
    except Exception as e:  # keep /ping failing so SageMaker marks the container unhealthy
        state["error"] = repr(e)
        print("launch failed:", repr(e), flush=True)


def _tail(path):
    while not os.path.exists(path):
        time.sleep(1)
    with open(path, "r", errors="replace") as fh:
        while True:
            line = fh.readline()
            if line:
                print("[sglang]", line.rstrip().replace("\r", ""), flush=True)
            else:
                time.sleep(1)


threading.Thread(target=launch, daemon=True).start()


@app.get("/ping")
def ping():
    return Response(status_code=200 if state["ready"] else 503)


def _call(path, data=None, timeout=60):
    url = f"{SGLANG}{path}"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# aspect "W:H" string the H3 server accepts, keyed by the adapter's short names.
ASPECT = {"16:9": "16:9", "9:16": "9:16"}


@app.post("/invocations")
async def invocations(req: Request):
    if not state["ready"]:
        return Response(state["error"] or "loading", status_code=503)
    j = await req.json()
    mode = j.get("mode", "t2va")
    if mode not in ("t2va", "fl2va"):
        return Response(f"mode must be t2va or fl2va, got {mode!r}", status_code=400)
    aspect = ASPECT.get(j.get("aspect", "16:9"))
    if aspect is None:
        return Response("aspect must be 16:9 or 9:16", status_code=400)
    short_edge = int(j.get("short_edge", 768))
    duration = float(j.get("duration", 5.0))
    if not 4.0 <= duration <= 15.0:
        return Response("duration must be within the model's 4..15 s range", status_code=400)
    seed = int(j.get("seed", 1101))
    steps = int(j.get("steps", 8))

    target = {"short_edge": short_edge, "aspect_ratio": aspect,
              "duration_seconds": duration}
    conditions = []
    if mode == "fl2va":
        ff, lf = j.get("first_frame"), j.get("last_frame")
        if not (ff or lf):
            return Response("fl2va needs first_frame and/or last_frame (base64 PNG)",
                            status_code=400)
        # frame_index order must be [0], [-1] or [0, -1].
        if ff:
            conditions.append({"type": "image", "role": "keyframe",
                               "uri": f"data:image/png;base64,{ff}", "frame_index": 0})
        if lf:
            conditions.append({"type": "image", "role": "keyframe",
                               "uri": f"data:image/png;base64,{lf}", "frame_index": -1})

    # Never send a `quality` field: its mere presence disables the env-driven Cache-DiT.
    payload = {
        "model": "MiniMax-H3",
        "prompt": j["prompt"],
        "task": mode,
        "conditions": conditions,
        "target": target,
        "num_outputs_per_prompt": 1,
        "num_inference_steps": steps,
        "flow_shift": 12.0,
        "audio_flow_shift": 3.0,
        "seed": seed,
        "seconds": int(duration),
    }

    t0 = time.time()
    resp = _call("/v1/videos", payload, timeout=120)
    vid = resp["id"]
    while True:
        st = _call(f"/v1/videos/{vid}", timeout=60)
        status = st.get("status")
        if status == "completed":
            break
        if status == "failed":
            return Response("H3 job failed: " + json.dumps(st)[:2000], status_code=500)
        if state["proc"] and state["proc"].poll() is not None:
            return Response("sglang died during generation", status_code=500)
        time.sleep(1.0)
    gen_s = round(time.time() - t0, 2)

    # Download the finished MP4.
    with urllib.request.urlopen(f"{SGLANG}/v1/videos/{vid}/content", timeout=120) as r:
        mp4 = r.read()

    meta = st.get("metadata", {}) or {}
    # Pull geometry/fps/frames from the status when present; fall back to the request.
    width = meta.get("width") or _dig(st, "width")
    height = meta.get("height") or _dig(st, "height")
    fps = meta.get("fps") or _dig(st, "fps") or 24
    frames = meta.get("frames") or _dig(st, "num_frames") or _dig(st, "frames")
    return {
        "video_b64": base64.b64encode(mp4).decode(),
        "seconds": gen_s,
        "width": width,
        "height": height,
        "frames": frames,
        "fps": fps,
    }


def _dig(o, key):
    """Find the first value for `key` anywhere in a nested status structure."""
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _dig(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _dig(v, key)
            if r is not None:
                return r
    return None
