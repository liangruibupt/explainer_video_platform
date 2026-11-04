"""SageMaker inference server for YuE2-3B music generation (async endpoint).

GET  /ping         200 once the pipeline is loaded on the GPU, 503 before (or if load failed).
POST /invocations  JSON in, JSON out (audio as base64, so the container needs no S3 access).

Request:  {"style": str, "lyrics": str, "cot": "full|melody|off" (default full),
           "seed": int, "cfg_scale"?: float}
           Instrumental: lyrics with section tags only ("[Intro]\n\n[Instrumental]\n\n[Outro]")
           and a style ending with "instrumental, no vocals".
Response: {"audio_b64": base64, "format": "flac|mp3", "seconds": audio length,
           "sample_rate": 48000, "channels": 2, "abc": score text or null,
           "truncated": {...}, "timing": {...}, "gpu": name}

Audio is 16-bit FLAC (48 kHz stereo). If the FLAC is over 40 MB (very long songs) it is
re-encoded to 320 kbit/s MP3 so the async response stays small; "format" says which.
"""
import base64, io, os, subprocess, threading, time

import soundfile as sf
import torch
from fastapi import Body, FastAPI, Response

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
FLAC_LIMIT = 40 * 1024 * 1024

app = FastAPI()
state = {"pipe": None, "error": None, "gpu": None}
lock = threading.Lock()


def load():
    try:
        from yue2 import YuE2Pipeline
        t = time.time()
        pipe = YuE2Pipeline.from_pretrained(model=f"{MODEL_DIR}/model", vae=f"{MODEL_DIR}/vae",
                                            local_files_only=True, device="cuda", progress=False)
        # Load the weights onto the GPU now so /ping means "ready to generate".
        pipe._load_model()
        if hasattr(pipe, "_load_vae"):
            pipe._load_vae()
        gpu = torch.cuda.get_device_properties(0)
        state.update(pipe=pipe, gpu=gpu.name)
        print(f"loaded on {gpu.name} ({gpu.total_memory / 1024**3:.0f} GB) in {time.time() - t:.0f}s",
              flush=True)
    except Exception as e:  # keep /ping failing so SageMaker reports the container unhealthy
        state["error"] = repr(e)
        print("load failed:", repr(e), flush=True)


threading.Thread(target=load, daemon=True).start()


@app.get("/ping")
def ping():
    return Response(status_code=200 if state["pipe"] else 503)


def encode(audio, sr):
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="FLAC", subtype="PCM_16")
    data = buf.getvalue()
    if len(data) <= FLAC_LIMIT:
        return data, "flac"
    mp3 = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "flac", "-i", "pipe:0",
                          "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3", "pipe:1"],
                         input=data, capture_output=True, check=True).stdout
    return mp3, "mp3"


# Plain def: FastAPI runs it in a worker thread, so /ping keeps answering during generation.
@app.post("/invocations")
def invocations(j: dict = Body(...)):
    if not state["pipe"]:
        return Response(state["error"] or "loading", status_code=503)
    kw = {"style": j["style"], "lyrics": j["lyrics"], "cot": j.get("cot", "full"),
          "seed": int(j.get("seed", 831001))}
    if j.get("cfg_scale") is not None:
        kw["cfg_scale"] = float(j["cfg_scale"])
    with lock, torch.inference_mode():
        song = state["pipe"](**kw)
    data, fmt = encode(song.audio, song.sample_rate)
    timing = {k: v for k, v in song.timing.items() if k != "load"}
    return {"audio_b64": base64.b64encode(data).decode(), "format": fmt,
            "seconds": round(len(song.audio) / song.sample_rate, 2),
            "sample_rate": song.sample_rate,
            "channels": 1 if song.audio.ndim == 1 else song.audio.shape[1],
            "abc": song.abc, "truncated": song.truncated, "timing": timing, "gpu": state["gpu"]}
