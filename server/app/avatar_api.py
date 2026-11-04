"""HTTP routes for the 数字人 page: /api/avatars/* and /api/avatar/status.

Store: data/avatars/<id>/ with req.json, state.json and the files
  portrait.png   the portrait sent to the worker (from upload, library asset or image page)
  ref.mp4        optional reference video (v2v) instead of a portrait
  audio.wav      16 kHz mono speech sent to the worker (upload / asset / Polly from script)
  video.mp4      the result (carries audio.wav), poster.jpg its first-second frame

POST /api/avatars takes JSON (asset ids, image generation id, script) or multipart
(the same fields as form fields plus files `portrait`, `audio`, `ref_video`). Inputs are
checked and converted before the 201; the speech synthesis and the GPU run happen in a
background thread that records status (queued|running|done|failed), step and notes.
"""
import json
import re
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import avatar, images
from .pipeline import DATA

router = APIRouter()
ROOT = DATA / "avatars"
ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
IMG_ID_RE = ID_RE
FILES = {"video": ("video.mp4", "video/mp4"), "portrait": ("portrait.png", "image/png"),
         "audio": ("audio.wav", "audio/wav"), "poster": ("poster.jpg", "image/jpeg"),
         "ref_video": ("ref.mp4", "video/mp4")}
# scene presets: label -> prompt sent to the worker (English reads best for both models)
SCENES = {
    "live": ("直播间带货", "a friendly host presenting a product to the camera in a bright "
             "livestream studio, ring light, product shelf behind, natural hand gestures, "
             "upper body, medium shot"),
    "shelf": ("超市货架口播", "a presenter standing in a supermarket aisle in front of neat "
              "shelves, talking to the camera about a product, natural head and hand "
              "movement, even store lighting, medium shot"),
    "store": ("门店促销", "a shop assistant at a store entrance announcing a promotion to the "
              "camera, warm lighting, lively but natural gestures, upper body"),
    "closeup": ("产品特写讲解", "a presenter explaining a product held near the face, close "
                "medium shot, soft studio light, clean background, calm natural movement"),
}
LIVE = ("queued", "running")
_lock = threading.Lock()


# ---------------------------------------------------------------- store

def _dir(item_id: str):
    if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
        raise HTTPException(404, "no such avatar")
    return ROOT / item_id


def _state(d, **kw) -> dict:
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


def get(item_id: str) -> dict:
    p = _dir(item_id) / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def list_all(limit: int = 60) -> list:
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
    return s | {"urls": {k: f"/api/avatars/{s['id']}/{k}" for k, (n, _) in FILES.items()
                         if n in have}}


# ---------------------------------------------------------------- inputs

def _asset_bytes(asset_id: str, kind: str) -> bytes:
    try:
        from .assets import store
    except ModuleNotFoundError:
        raise HTTPException(422, "素材库不可用") from None
    if not store.valid_id(asset_id):
        raise HTTPException(422, f"素材 id 格式不对：{asset_id}")
    meta = store.get(asset_id)
    if not meta:
        raise HTTPException(422, f"素材不存在：{asset_id}")
    if meta.get("kind") != kind:
        raise HTTPException(422, f"素材 {asset_id} 是 {meta.get('kind')}，这里需要 {kind}")
    return store.asset_path(asset_id).read_bytes()


def _image_gen_bytes(img_id: str, index) -> bytes:
    if not IMG_ID_RE.fullmatch(img_id or ""):
        raise HTTPException(422, f"图像生成 id 格式不对：{img_id}")
    try:
        index = int(index or 0)
    except (TypeError, ValueError):
        raise HTTPException(422, "image_index 必须是 0-3") from None
    if not 0 <= index <= 3:
        raise HTTPException(422, "image_index 必须是 0-3")
    p = images.ROOT / img_id / f"{index}.png"
    if not p.is_file():
        raise HTTPException(422, f"图像 {img_id} 第 {index + 1} 张不存在或还没生成好")
    return p.read_bytes()


def _flag(v, default=True) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "on")


async def _read_body(request: Request):
    """(fields dict, files dict name -> bytes) from JSON or multipart."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        fields, files = {}, {}
        for k, v in form.multi_items():
            if hasattr(v, "read"):
                data = await v.read()
                if data:
                    files[k] = data
            else:
                fields[k] = v
        return fields, files
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "body must be JSON or multipart/form-data") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "body must be a JSON object")
    return body, {}


def prepare(fields: dict, files: dict) -> tuple:
    """Check every input; returns (req dict, prepared files {name: bytes}).
    Raises HTTPException(422) with a readable message."""
    def s(k, n=200):
        v = fields.get(k)
        return "" if v is None else str(v).strip()[:n * 10]

    model, size = s("model"), s("size") or "480"
    if model and model not in avatar.MODELS:
        raise HTTPException(422, f"不支持的模型：{model}（可选 {', '.join(avatar.MODELS)}）")
    if size not in avatar.SIZES:
        raise HTTPException(422, "size 只能是 480 或 720")
    try:
        seed = int(fields.get("seed", 42) if fields.get("seed") not in (None, "") else 42)
    except (TypeError, ValueError):
        raise HTTPException(422, "seed 必须是整数") from None
    if not 0 <= seed < 2**31:
        raise HTTPException(422, "seed 超出范围")
    scene = s("scene")
    if scene and scene not in SCENES:
        raise HTTPException(422, f"未知的场景：{scene}")
    prompt = s("prompt", 100)
    if len(prompt) > avatar.PROMPT_MAX:
        raise HTTPException(422, f"场景描述超过 {avatar.PROMPT_MAX} 字")
    if not prompt and scene:
        prompt = SCENES[scene][1]
    title = s("title", 20)[:80]

    out = {}
    try:
        # portrait or reference video
        srcs = [k for k in ("portrait", "ref_video") if k in files]
        srcs += [k for k in ("portrait_asset_id", "image_id", "ref_video_asset_id") if s(k)]
        if len(srcs) != 1:
            raise HTTPException(422, "人像需要且只能选一个来源：上传图片、素材库图片、图像页的生成结果，"
                                     "或参考视频")
        src = srcs[0]
        portrait_source = {"type": src}
        if src == "portrait":
            out["portrait.png"] = avatar.portrait_png(files["portrait"])
        elif src == "portrait_asset_id":
            out["portrait.png"] = avatar.portrait_png(_asset_bytes(s(src), "image"))
            portrait_source["id"] = s(src)
        elif src == "image_id":
            out["portrait.png"] = avatar.portrait_png(
                _image_gen_bytes(s(src), fields.get("image_index")))
            portrait_source.update(id=s(src), index=int(fields.get("image_index") or 0))
        else:
            raw = files["ref_video"] if src == "ref_video" else _asset_bytes(s(src), "video")
            mp4, ref_s, first = avatar.ref_video_mp4(raw)
            out["ref.mp4"], out["portrait.png"] = mp4, first
            portrait_source.update(seconds=ref_s)
            if src != "ref_video":
                portrait_source["id"] = s(src)

        # speech: audio upload / asset, or script + voice
        script, voice = s("script", 300), s("voice") or "Zhiyu"
        a_srcs = (["audio"] if "audio" in files else []) + (["audio_asset_id"]
                                                             if s("audio_asset_id") else [])
        if script:
            a_srcs.append("script")
        if len(a_srcs) != 1:
            raise HTTPException(422, "声音需要且只能选一个来源：上传音频、素材库音频，或口播稿 + 声音")
        audio_source = {"type": a_srcs[0]}
        est_s = None
        if a_srcs[0] == "script":
            script = avatar.check_script(script)
            if voice not in avatar.VOICES:
                raise HTTPException(422, f"不支持的声音：{voice}（可选 {', '.join(avatar.VOICES)}）")
            audio_source.update(voice=voice)
            est_s = avatar.speech_seconds(script)
        else:
            raw = files["audio"] if a_srcs[0] == "audio" else _asset_bytes(
                s("audio_asset_id"), "audio")
            out["audio.wav"], est_s = avatar.to_wav16k(raw)
            if a_srcs[0] == "audio_asset_id":
                audio_source["id"] = s("audio_asset_id")
            script = ""
    except avatar.AvatarError as e:
        raise HTTPException(422, str(e)) from None

    req = {"model": model, "model_used": model or avatar.default_model(), "size": size,
           "fast": _flag(fields.get("fast")), "seed": seed, "scene": scene, "prompt": prompt,
           "script": script, "voice": voice if script else "", "title": title
           or (script[:30] if script else ""), "portrait_source": portrait_source,
           "audio_source": audio_source, "audio_seconds": est_s}
    req["estimate"] = avatar.estimate(est_s or 0, req["model_used"], size, req["fast"])
    return req, out


# ---------------------------------------------------------------- background job

def _run(item_id: str) -> None:
    d = ROOT / item_id
    req = json.loads((d / "req.json").read_text())
    t0 = time.time()
    try:
        _state(d, status="running")
        if req.get("script") and not (d / "audio.wav").exists():
            _state(d, step="speech", note=f"Polly 合成语音（{req['voice']}）")
            wav, sec = avatar.to_wav16k(avatar.speech(req["script"], req["voice"]))
            (d / "audio.wav").write_bytes(wav)
            _state(d, audio_seconds=sec, files=_files(d),
                   estimate=avatar.estimate(sec, req["model_used"], req["size"], req["fast"]),
                   note=f"语音 {sec} 秒")
        _state(d, step="render", note="提交数字人 GPU 任务")
        last = {"m": -1}

        def on_wait(sec):
            m = sec // 60
            if m != last["m"]:
                last["m"] = m
                _state(d, waited=sec, note=f"等待 GPU（{m} 分钟）" if m else "已提交，等待 GPU")
            else:
                _state(d, waited=sec)

        ref = d / "ref.mp4"
        meta = {}
        mp4 = avatar.generate((d / "audio.wav").read_bytes(),
                              image=None if ref.exists() else (d / "portrait.png").read_bytes(),
                              video=ref.read_bytes() if ref.exists() else None,
                              prompt=req["prompt"], model=req["model"], size=req["size"],
                              fast=req["fast"], seed=req["seed"], on_wait=on_wait,
                              meta_out=meta)
        (d / "video.mp4").write_bytes(mp4)
        avatar.poster(d / "video.mp4", d / "poster.jpg")
        _state(d, status="done", step="done", files=_files(d), worker_meta=meta,
               video_seconds=avatar.duration(d / "video.mp4"), seconds=round(time.time() - t0),
               note="完成")
    except Exception as e:  # noqa: BLE001 - shown on the page
        _state(d, status="failed", error=str(e)[:500], seconds=round(time.time() - t0),
               note="失败：" + str(e)[:200])


def _files(d) -> list:
    return [n for n, _ in FILES.values() if (d / n).exists()]


def create(req: dict, files: dict) -> dict:
    item_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = ROOT / item_id
    d.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (d / name).write_bytes(data)
    (d / "req.json").write_text(json.dumps(req, ensure_ascii=False))
    s = _state(d, id=item_id, status="queued", step="queued", created=time.time(),
               error=None, files=_files(d), **req, note="已排队")
    threading.Thread(target=_run, args=(item_id,), daemon=True).start()
    return s


# ---------------------------------------------------------------- routes

@router.get("/api/avatar/status")
def avatar_status():
    try:
        return avatar.status()
    except Exception as e:  # noqa: BLE001 - e.g. no AWS credentials
        return {"deployed": False, "state": "unavailable", "error": str(e)[:200]}


@router.get("/api/avatars/options")
def avatar_options():
    return {"models": [{"id": k, "label": v, "default": k == avatar.default_model()}
                       for k, v in avatar.MODELS.items()],
            "default_model": avatar.default_model(), "sizes": list(avatar.SIZES),
            "voices": [{"id": k, "lang": v[0], "label": v[1]} for k, v in avatar.VOICES.items()],
            "scenes": [{"id": k, "label": v[0], "prompt": v[1]} for k, v in SCENES.items()],
            "limits": {"script_chars": avatar.SCRIPT_MAX, "audio_seconds": avatar.AUDIO_MAX_S,
                       "prompt_chars": avatar.PROMPT_MAX, "ref_video_seconds": avatar.VIDEO_MAX_S,
                       "image_min_edge": avatar.IMAGE_MIN_EDGE},
            "configured": avatar.configured()}


@router.get("/api/avatars/estimate")
def avatar_estimate(seconds: float = 30, model: str = "", size: str = "480",
                    fast: bool = True, script: str = ""):
    if model and model not in avatar.MODELS:
        raise HTTPException(422, "unknown model")
    if size not in avatar.SIZES:
        raise HTTPException(422, "size must be 480 or 720")
    if script:
        seconds = avatar.speech_seconds(script[: avatar.SCRIPT_MAX])
    return avatar.estimate(max(0.0, min(seconds, avatar.AUDIO_MAX_S)), model, size, fast)


@router.get("/api/avatars")
def avatar_list():
    return [_public(s) for s in list_all()]


@router.post("/api/avatars", status_code=201)
async def avatar_create(request: Request):
    fields, files = await _read_body(request)
    for k, lim in (("portrait", avatar.IMAGE_MAX_BYTES), ("audio", avatar.AUDIO_MAX_BYTES),
                   ("ref_video", avatar.VIDEO_MAX_BYTES)):
        if k in files and len(files[k]) > lim:
            raise HTTPException(413, f"{k} 超过 {lim // 2**20} MB")
    unknown = set(files) - {"portrait", "audio", "ref_video"}
    if unknown:
        raise HTTPException(422, f"unknown file field: {', '.join(sorted(unknown))}")
    req, prepared = prepare(fields, files)
    return _public(create(req, prepared))


@router.get("/api/avatars/{item_id}")
def avatar_get(item_id: str):
    s = get(item_id)
    if not s:
        raise HTTPException(404, "no such avatar")
    return _public(s)


@router.delete("/api/avatars/{item_id}")
def avatar_delete(item_id: str):
    d = _dir(item_id)
    s = get(item_id)
    if not s:
        raise HTTPException(404, "no such avatar")
    if s.get("status") in LIVE:
        raise HTTPException(409, "还在生成，不能删除")
    trash = ROOT / f".{item_id}.deleted"
    d.rename(trash)
    for p in trash.iterdir():
        p.unlink()
    trash.rmdir()
    return {"deleted": item_id}


@router.get("/api/avatars/{item_id}/{name}")
def avatar_file(item_id: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "no such file")
    fname, mime = FILES[name]
    p = _dir(item_id) / fname
    if not p.is_file():
        raise HTTPException(404, "not ready")
    return FileResponse(p, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})
