"""Client for the digital-human module (数字人) on the self-managed EC2 GPU backend.

Contract: docs/AVATAR_CONTRACT.md. Worker config: infra/gpu_ec2/modules/avatar.json (written
by the infra part). One worker image serves both models, picked by the request's `model`:
  infinitetalk  MeiGen-AI InfiniteTalk on Wan2.1-I2V-14B-480P
  longcat       meituan-longcat LongCat-Video-Avatar-1.5
model "" means the module default (config `default_model`, else infinitetalk).

A portrait (image) or a short reference video plus speech audio -> lip-synced MP4 that
carries the input audio. Nothing is added to the output (no watermark, no "AI 生成" mark).

Helpers used by the API and the workflow nodes:
  status()               same shape as video.status(); deployed=False when the config is missing
  generate(...)          blocking submit -> poll -> fetch, returns MP4 bytes
  to_wav16k(audio)       any audio -> 16 kHz mono PCM wav (what the worker gets)
  speech(script, voice)  Polly neural speech through the narration script (narrate.py)
  estimate(...)          rough render time / cost for the page
"""
import base64
import io
import json
import subprocess
import tempfile
from pathlib import Path

from . import gpu_client, pipeline
from .gpu_ec2 import MODULES

MODULE = "avatar"
MODELS = {"infinitetalk": "InfiniteTalk（Wan2.1 14B）", "longcat": "LongCat-Video-Avatar 1.5"}
SIZES = ("480", "720")
VOICES = {"Zhiyu": ("cmn-CN", "知语 · 普通话女声"), "Hiujin": ("yue-CN", "晓晴 · 粤语女声")}
SCRIPT_MAX = 2000           # characters of 口播稿
AUDIO_MAX_S = 600           # contract: speech up to 600 s
AUDIO_MIN_S = 1.0
AUDIO_MAX_BYTES = 60 * 1024 * 1024
IMAGE_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MIN_EDGE, IMAGE_MAX_EDGE = 256, 8192
VIDEO_MAX_BYTES = 200 * 1024 * 1024
VIDEO_MAX_S = 120           # reference video (v2v), trimmed to the audio by the worker
PROMPT_MAX = 600
TIMEOUT_S = 4 * 3600        # = worker hard cap


class AvatarError(ValueError):
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
    return m if m in MODELS else "infinitetalk"


def status() -> dict:
    """{'deployed', 'state': ready|starting|idle|failed|unavailable, 'instances', 'desired',
    'instance_type', ...}; without avatar.json: deployed False, label 未部署."""
    base = {"models": MODELS, "default_model": default_model()}
    if not configured():
        return dict(base, deployed=False, state="unavailable", instances=0, desired=0,
                    instance_type="", backend="ec2", label="未部署",
                    error="未部署：缺少 infra/gpu_ec2/modules/avatar.json")
    return dict(gpu_client.status(MODULE), **base)


# ---------------------------------------------------------------- media helpers

def _ff(args, timeout=300):
    r = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise AvatarError(f"ffmpeg 处理失败：{r.stderr.decode(errors='replace')[-300:]}")
    return r


def duration(path) -> float:
    try:
        out = subprocess.run([pipeline.FFPROBE, "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=60)
        return round(float(out.stdout.strip() or 0), 2)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def to_wav16k(audio: bytes) -> tuple:
    """(16 kHz mono s16 wav bytes, seconds). Raises AvatarError on bad / too long audio."""
    if not audio:
        raise AvatarError("音频是空的")
    if len(audio) > AUDIO_MAX_BYTES:
        raise AvatarError(f"音频超过 {AUDIO_MAX_BYTES // 2**20} MB")
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in", Path(td) / "out.wav"
        src.write_bytes(audio)
        r = subprocess.run([pipeline.FFMPEG, "-v", "error", "-y", "-i", str(src), "-vn",
                            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                            "-t", str(AUDIO_MAX_S + 1), str(dst)],
                           capture_output=True, timeout=300)
        if r.returncode != 0 or not dst.is_file():
            raise AvatarError("音频无法解析（支持 wav / mp3 / m4a / flac 等）")
        sec = duration(dst)
        if sec > AUDIO_MAX_S:
            raise AvatarError(f"音频超过 {AUDIO_MAX_S} 秒（{AUDIO_MAX_S // 60} 分钟）")
        if sec < AUDIO_MIN_S:
            raise AvatarError("音频太短（至少 1 秒）")
        return dst.read_bytes(), sec


def portrait_png(image: bytes) -> bytes:
    """Validate a portrait (png/jpg/webp) and return it as an RGB PNG."""
    from PIL import Image, UnidentifiedImageError
    if not image:
        raise AvatarError("人像图片是空的")
    if len(image) > IMAGE_MAX_BYTES:
        raise AvatarError(f"人像图片超过 {IMAGE_MAX_BYTES // 2**20} MB")
    try:
        with Image.open(io.BytesIO(image)) as im:
            im.verify()
        with Image.open(io.BytesIO(image)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise AvatarError("人像只支持 png / jpg / webp")
            w, h = im.size
            if min(w, h) < IMAGE_MIN_EDGE:
                raise AvatarError(f"人像太小（短边至少 {IMAGE_MIN_EDGE}px，现在 {min(w, h)}px）")
            if max(w, h) > IMAGE_MAX_EDGE:
                raise AvatarError(f"人像太大（长边最多 {IMAGE_MAX_EDGE}px）")
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            out = io.BytesIO()
            bg.save(out, "PNG")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise AvatarError(f"人像图片无法解析：{e}") from None


def ref_video_mp4(video: bytes) -> tuple:
    """(H.264 mp4 bytes without audio, short edge <= 720, seconds, first frame PNG)."""
    if not video:
        raise AvatarError("参考视频是空的")
    if len(video) > VIDEO_MAX_BYTES:
        raise AvatarError(f"参考视频超过 {VIDEO_MAX_BYTES // 2**20} MB")
    with tempfile.TemporaryDirectory() as td:
        src, dst, png = Path(td) / "in", Path(td) / "ref.mp4", Path(td) / "f.png"
        src.write_bytes(video)
        sec = duration(src)
        if sec <= 0:
            raise AvatarError("参考视频无法解析（支持 mp4 / mov / webm）")
        if sec > VIDEO_MAX_S:
            raise AvatarError(f"参考视频超过 {VIDEO_MAX_S} 秒")
        r = subprocess.run([pipeline.FFMPEG, "-v", "error", "-y", "-i", str(src), "-an",
                            "-vf", "scale='if(gt(iw,ih),-2,min(720,iw))':'if(gt(iw,ih),min(720,ih),-2)'",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)],
                           capture_output=True, timeout=600)
        if r.returncode != 0 or not dst.is_file():
            raise AvatarError("参考视频里没有可用的画面")
        _ff([pipeline.FFMPEG, "-v", "error", "-y", "-i", dst, "-frames:v", "1", png])
        return dst.read_bytes(), sec, png.read_bytes()


def poster(mp4_path: Path, out: Path) -> bool:
    at = "1" if duration(mp4_path) > 1.5 else "0"
    r = subprocess.run([pipeline.FFMPEG, "-v", "error", "-y", "-ss", at, "-i", str(mp4_path),
                        "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True,
                       timeout=120)
    return r.returncode == 0 and out.is_file()


# ---------------------------------------------------------------- speech (Polly)

def check_script(script: str) -> str:
    s = (script or "").strip()
    if len(s) < 2:
        raise AvatarError("口播稿太短")
    if len(s) > SCRIPT_MAX:
        raise AvatarError(f"口播稿超过 {SCRIPT_MAX} 字（现在 {len(s)} 字）")
    return s


def speech(script: str, voice: str = "Zhiyu") -> bytes:
    """Polly neural speech (wav bytes) for a 口播稿, through the narration script narrate.py
    (same SSML + pronunciation lexicon as the explainer jobs)."""
    script = check_script(script)
    if voice not in VOICES:
        raise AvatarError(f"不支持的声音：{voice}（可选 {', '.join(VOICES)}）")
    lang = VOICES[voice][0]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "scenes.json").write_text(json.dumps([{"id": "pitch", "narration": script}],
                                                  ensure_ascii=False))
        r = subprocess.run([pipeline.PY, str(pipeline.SCRIPTS / "narrate.py"), "scenes.json",
                            ".", "--tts", "polly", "--voice", voice, "--lang", lang,
                            "--lead", "200", "--tail", "500"],
                           cwd=d, env=pipeline._env(), capture_output=True, text=True,
                           timeout=240)
        out = d / "narration.wav"
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError("Polly 合成失败：" + (r.stderr or r.stdout)[-500:])
        return out.read_bytes()


def speech_seconds(script: str) -> float:
    """Rough spoken length of a Chinese script (Zhiyu reads about 4.5 characters a second)."""
    return round(len((script or "").strip()) / 4.5 + 0.7, 1)


# ---------------------------------------------------------------- estimate

# render seconds per second of output video, (model, size, fast) -> factor. Rough figures
# until docs/AVATAR.md measures them; avatar.json may override with "render_s_per_s":
# {"infinitetalk": {"480_fast": 4, ...}}, "cold_min" and "usd_per_hour".
RENDER = {("infinitetalk", "480", True): 4, ("infinitetalk", "720", True): 10,
          ("infinitetalk", "480", False): 20, ("infinitetalk", "720", False): 50,
          ("longcat", "480", True): 5, ("longcat", "720", True): 12,
          ("longcat", "480", False): 5, ("longcat", "720", False): 12}  # longcat: distill only
COLD_MIN = 15
USD_PER_HOUR = 3.0


def estimate(seconds: float, model: str = "", size: str = "480", fast: bool = True) -> dict:
    cfg = config()
    model = model or default_model()
    over = (cfg.get("render_s_per_s") or {}).get(model) or {}
    factor = over.get(f"{size}_{'fast' if fast else 'full'}") or RENDER.get(
        (model, size, bool(fast)), 10)
    render_min = max(1.0, seconds * float(factor) / 60 + 1)
    cold = float(cfg.get("cold_min") or COLD_MIN)
    rate = float(cfg.get("usd_per_hour") or USD_PER_HOUR)
    return {"seconds": round(seconds, 1), "model": model, "render_min": round(render_min, 1),
            "cold_min": cold, "usd_per_hour": rate,
            "usd_warm": round(render_min / 60 * rate, 2),
            "usd_cold": round((render_min + cold) / 60 * rate, 2), "rough": True}


# ---------------------------------------------------------------- generate

def request(audio_wav: bytes, image: bytes = None, video: bytes = None, prompt: str = "",
            model: str = "", size: str = "480", fast: bool = True, seed: int = 42) -> dict:
    """The worker request (contract), from already prepared media."""
    if model and model not in MODELS:
        raise AvatarError(f"不支持的模型：{model}（可选 {', '.join(MODELS)}）")
    if str(size) not in SIZES:
        raise AvatarError(f"size 只能是 {' / '.join(SIZES)}")
    if bool(image) == bool(video):
        raise AvatarError("需要一张人像图片或一段参考视频（二选一）")
    if len(prompt or "") > PROMPT_MAX:
        raise AvatarError(f"场景描述超过 {PROMPT_MAX} 字")
    req = {"model": model or default_model(), "audio_b64": base64.b64encode(audio_wav).decode(),
           "prompt": (prompt or "").strip(), "size": str(size), "fast": bool(fast),
           "seed": int(seed)}
    if image:
        req["image_b64"] = base64.b64encode(image).decode()
    else:
        req["video_b64"] = base64.b64encode(video).decode()
    return req


def generate(audio: bytes, image: bytes = None, video: bytes = None, prompt: str = "",
             model: str = "", size: str = "480", fast: bool = True, seed: int = 42,
             timeout: int = TIMEOUT_S, on_wait=None, meta_out: dict = None) -> bytes:
    """Blocking: returns the MP4 bytes (lip-synced video carrying the input audio).
    audio is any audio format (converted to 16 kHz mono wav); image a png/jpg/webp
    portrait, or video a reference clip. meta_out, when given, receives the worker meta."""
    if not configured():
        raise RuntimeError("数字人 GPU 模块未部署：缺少 infra/gpu_ec2/modules/avatar.json")
    wav, _ = to_wav16k(audio)
    img = portrait_png(image) if image else None
    vid = ref_video_mp4(video)[0] if video else None
    req = request(wav, img, vid, prompt, model, size, fast, seed)
    res = gpu_client.run(MODULE, req, timeout, on_wait)
    files = gpu_client.gpu(MODULE).fetch(res, "video_b64")
    if not files or not files[0]:
        raise RuntimeError("avatar worker returned no video")
    if meta_out is not None:
        meta_out.update(res.get("meta") or {})
    return files[0]
