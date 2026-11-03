"""SageMaker inference server for Qwen-Image 2.1 (async endpoint).

GET  /ping         200 once the pipeline is loaded on the GPU, 503 before.
POST /invocations  JSON in, JSON out (PNG bytes as base64, so the container needs no S3 access).

Request:  {"mode": "t2i|edit|rgba", "prompt": str, "ratio": "16:9|9:16|1:1",
           "steps": 40, "seed": 42, "n": 1, "refs": [base64 PNG, ...]}
Response: {"images": [base64 PNG, ...], "width": W, "height": H, "seed": s,
           "seconds": t, "mem_mode": "full|lowvram", "gpu": name}

EVP_MEM_MODE picks how the model fits on the GPU:
  full     everything BF16 on the GPU (needs about 40 GB: g6e 48 GB, g7e 96 GB)
  lowvram  text encoder loaded 8-bit, the rest BF16 (fits 32 GB: g7)
  auto     (default) full if the GPU has >= 44 GB, else lowvram
"""
import base64, io, os, threading, time

import torch
from fastapi import FastAPI, Request, Response
from PIL import Image

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
# Output sizes for 1080p video. The model needs sides divisible by 16, so render
# one step larger and centre-crop to the exact frame.
SIZES = {"16:9": ((1920, 1088), (1920, 1080)), "9:16": ((1088, 1920), (1080, 1920)),
         "1:1": ((1440, 1440), (1440, 1440))}
RGBA_HINT = ("This is an RGBA image with transparency. {} "
             "The image has alpha channel and the background is transparent.")

app = FastAPI()
state = {"pipe": None, "error": None, "mode": None, "gpu": None}
lock = threading.Lock()


def load():
    try:
        from diffusers import QwenImage21Pipeline
        gpu = torch.cuda.get_device_properties(0)
        mode = os.environ.get("EVP_MEM_MODE", "auto")
        if mode == "auto":
            mode = "full" if gpu.total_memory >= 44 * 1024**3 else "lowvram"
        kw = {"torch_dtype": torch.bfloat16}
        if mode == "lowvram":
            from transformers import BitsAndBytesConfig, Qwen3VLForConditionalGeneration
            kw["text_encoder"] = Qwen3VLForConditionalGeneration.from_pretrained(
                MODEL_DIR, subfolder="text_encoder", torch_dtype=torch.bfloat16,
                quantization_config=BitsAndBytesConfig(load_in_8bit=True), device_map={"": 0})
        pipe = QwenImage21Pipeline.from_pretrained(MODEL_DIR, **kw)
        # The VAE decode of a 1920x1088 frame allocates several GB in one block and OOMs
        # the 31 GB card; tiling + slicing cap the peak so decode fits. Harmless to output.
        pipe.vae.enable_tiling()
        pipe.vae.enable_slicing()
        pipe.to("cuda")
        state.update(pipe=pipe, mode=mode, gpu=gpu.name)
        print(f"loaded on {gpu.name} ({gpu.total_memory / 1024**3:.0f} GB), mode={mode}", flush=True)
    except Exception as e:  # keep /ping failing so SageMaker reports the container unhealthy
        state["error"] = repr(e)
        print("load failed:", repr(e), flush=True)


threading.Thread(target=load, daemon=True).start()


@app.get("/ping")
def ping():
    return Response(status_code=200 if state["pipe"] else 503)


def b64png(img):
    buf = io.BytesIO(); img.save(buf, "PNG"); return base64.b64encode(buf.getvalue()).decode()


def crop(img, w, h):
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


@app.post("/invocations")
async def invocations(req: Request):
    if not state["pipe"]:
        return Response(state["error"] or "loading", status_code=503)
    j = await req.json()
    mode, ratio = j.get("mode", "t2i"), j.get("ratio", "16:9")
    (gw, gh), (w, h) = SIZES[ratio]
    seed = int(j.get("seed", 42)); n = max(1, min(int(j.get("n", 1)), 4))
    prompt = j["prompt"] if mode != "rgba" else RGBA_HINT.format(j["prompt"])
    kw = dict(prompt=prompt, num_inference_steps=int(j.get("steps", 40)),
              num_images_per_prompt=n, generator=torch.Generator("cuda").manual_seed(seed))
    if mode == "edit":
        kw["image"] = [Image.open(io.BytesIO(base64.b64decode(r))).convert("RGB")
                       for r in j.get("refs", [])[:10]]
    else:
        kw.update(width=gw, height=gh)
    t = time.time()
    with lock, torch.inference_mode():
        imgs = state["pipe"](**kw).images
    if mode != "edit":
        imgs = [crop(im, w, h) for im in imgs]
    return {"images": [b64png(im) for im in imgs], "width": imgs[0].width,
            "height": imgs[0].height, "seed": seed, "seconds": round(time.time() - t, 1),
            "mem_mode": state["mode"], "gpu": state["gpu"]}
