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
    cfg = config()
    base = {"models": MODELS, "default_model": default_model(),
            "render_s_per_s": cfg.get("render_s_per_s") or {},
            "cold_min": float(cfg.get("cold_min") or COLD_MIN),
            "usd_per_hour": float(cfg.get("usd_per_hour") or USD_PER_HOUR)}
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
COLD_MIN = 13  # measured cold start, docs/AVATAR.md
USD_PER_HOUR = 3.0


def estimate(seconds: float, model: str = "", size: str = "480", fast: bool = True,
             segment_s: int = 0, script: str = "") -> dict:
    cfg = config()
    model = model or default_model()
    over = (cfg.get("render_s_per_s") or {}).get(model) or {}
    factor = over.get(f"{size}_{'fast' if fast else 'full'}") or RENDER.get(
        (model, size, bool(fast)), 10)
    segments = segment_count(seconds, segment_s, script)
    # each extra segment pays the per-job fixed overhead again (~1 min)
    render_min = max(1.0, seconds * float(factor) / 60 + segments)
    cold = float(cfg.get("cold_min") or COLD_MIN)
    rate = float(cfg.get("usd_per_hour") or USD_PER_HOUR)
    return {"seconds": round(seconds, 1), "model": model, "render_min": round(render_min, 1),
            "cold_min": cold, "usd_per_hour": rate, "segments": segments,
            "segment_s": int(segment_s or 0),
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


# ---------------------------------------------------------------- long clips: segments
#
# Identity holds about 60 s and then drifts slowly (docs/AVATAR.md section 5). Speech longer
# than segment_s is split into segments that are each generated from the ORIGINAL portrait
# and joined into one mp4 with the full, continuous audio:
#   split_text()   script -> chunks at sentence ends (TTS path; each chunk synthesized alone)
#   split_audio()  16 kHz wav -> pieces cut at silences (ffmpeg silencedetect), else hard cut
#   generate_long  pieces -> one generate() per piece (cached by hash) -> join()
#   join()         per-segment video trimmed/padded to its audio, 0.3 s video crossfade at
#                  each seam, full audio muxed; total length = audio length.

SEGMENT_S = 55              # default limit per segment, seconds
SEGMENT_MAX_S = 60          # identity is measured stable up to ~60 s
SEGMENT_MIN_S = 15          # smallest accepted limit (0 = off)
XFADE_S = 0.3
SILENCE_DB, SILENCE_MIN_S = -35, 0.25
_SENT_END = "。！？；!?;"
_SOFT_END = "，、,："


def check_segment_s(v) -> int:
    """0 (off) or SEGMENT_MIN_S..SEGMENT_MAX_S; None/'' means the default."""
    if v is None or v == "":
        return SEGMENT_S
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        raise AvatarError("segment_s 必须是整数秒（0 = 不分段）") from None
    if v != 0 and not SEGMENT_MIN_S <= v <= SEGMENT_MAX_S:
        raise AvatarError(f"segment_s 只能是 0（不分段）或 {SEGMENT_MIN_S}–{SEGMENT_MAX_S} 秒")
    return v


def _pieces(text: str, ends: str) -> list:
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in ends:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return [p for p in out if p.strip()]


def _sentences(text: str, limit: float) -> list:
    """Sentences, each of estimated speech <= limit (long ones split at commas, then hard)."""
    out = []
    for s in _pieces(text, _SENT_END):
        if speech_seconds(s) <= limit:
            out.append(s)
            continue
        for c in _pieces(s, _SOFT_END):
            n = max(1, int((limit - 0.7) * 4.5))
            out += [c[i:i + n] for i in range(0, len(c), n)] if speech_seconds(c) > limit else [c]
    return out


def _pack(sents: list, limit: float) -> list:
    chunks, cur = [], ""
    for s in sents:
        if cur and speech_seconds(cur + s) > limit:
            chunks.append(cur)
            cur = ""
        cur += s
    return chunks + ([cur] if cur else [])


def split_text(script: str, segment_s: int = SEGMENT_S) -> list:
    """Script -> chunks split at sentence ends (。！？；) whose estimated speech is <= segment_s,
    balanced in length. One chunk when segment_s is 0 or the script is short enough."""
    s = (script or "").strip()
    if not segment_s or speech_seconds(s) <= segment_s:
        return [s] if s else []
    sents = _sentences(s, segment_s)
    best = _pack(sents, segment_s)
    # balance: the smallest limit that still needs no more chunks than the greedy fill
    lim = speech_seconds(s) / len(best)
    while lim < segment_s:
        c = _pack(sents, lim)
        if len(c) <= len(best):
            return c
        lim += 0.5
    return best


def _wav_parts(wav: bytes) -> tuple:
    import wave
    with wave.open(io.BytesIO(wav)) as w:
        return w.getparams(), w.readframes(w.getnframes())


def _wav_bytes(params, frames: bytes) -> bytes:
    import wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    return b.getvalue()


def wav_seconds(wav: bytes) -> float:
    p, fr = _wav_parts(wav)
    return len(fr) / (p.sampwidth * p.nchannels) / p.framerate


def concat_wavs(wavs: list) -> bytes:
    """Concatenate 16 kHz mono wavs (same params) without re-encoding."""
    params, frames = None, b""
    for w in wavs:
        p, fr = _wav_parts(w)
        params = params or p
        frames += fr
    return _wav_bytes(params, frames)


def silences(wav: bytes, noise_db: int = SILENCE_DB, min_s: float = SILENCE_MIN_S) -> list:
    """[(start, end)] silent stretches of a wav (ffmpeg silencedetect)."""
    import re
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.wav"
        p.write_bytes(wav)
        r = subprocess.run([pipeline.FFMPEG, "-hide_banner", "-nostats", "-i", str(p), "-af",
                            f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=300)
    out, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    return out


def cut_points(total: float, segment_s: float, quiet: list) -> list:
    """Cut times (seconds) so every piece is <= segment_s: each cut is the middle of the silence
    nearest to an even split, never past the limit and not in the first 30 % of the piece;
    a hard cut at the even split when no silence fits."""
    cuts, start = [], 0.0
    mids = [(a + b) / 2 for a, b in quiet]
    while total - start > segment_s:
        n_left = -(-(total - start) // segment_s)          # pieces still needed
        target = start + (total - start) / n_left
        ok = [m for m in mids if start + 0.3 * segment_s <= m <= start + segment_s
              and total - m >= 0.3 * segment_s]      # never leave a short tail piece
        cut = min(ok, key=lambda m: abs(m - target)) if ok else target
        cuts.append(round(cut, 3))
        start = cut
    return cuts


def split_audio(wav: bytes, segment_s: int = SEGMENT_S) -> list:
    """16 kHz wav -> list of wav pieces, each <= segment_s, cut at silences when possible."""
    if not segment_s:
        return [wav]
    total = wav_seconds(wav)
    if total <= segment_s:
        return [wav]
    cuts = cut_points(total, segment_s, silences(wav))
    p, fr = _wav_parts(wav)
    bpf = p.sampwidth * p.nchannels
    edges = [0] + [int(c * p.framerate) * bpf for c in cuts] + [len(fr)]
    return [_wav_bytes(p, fr[a:b]) for a, b in zip(edges, edges[1:])]


def segment_count(seconds: float, segment_s: int = SEGMENT_S, script: str = "") -> int:
    if not segment_s:
        return 1
    if script:
        return max(1, len(split_text(script, segment_s)))
    return max(1, int(-(-seconds // segment_s)))


def segment_key(wav: bytes, image: bytes, params: dict) -> str:
    import hashlib
    h = hashlib.sha256()
    for part in (wav, image or b"", json.dumps(params, sort_keys=True).encode()):
        h.update(hashlib.sha256(part).digest())
    return h.hexdigest()[:20]


def join(videos: list, durations: list, audio_wav: Path, out: Path, xfade: float = XFADE_S):
    """Join segment videos into one mp4 carrying audio_wav. Segment k is trimmed or padded
    (last frame held) to exactly durations[k] (its audio length), so audio sync is exact;
    each seam gets a video-only crossfade of `xfade` s that starts where segment k's audio
    ends (segment k is held `xfade` s longer underneath the next one)."""
    from fractions import Fraction
    if len(videos) != len(durations) or not videos:
        raise ValueError("join: one duration per video")
    probe = subprocess.run([pipeline.FFPROBE, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,r_frame_rate", "-of", "json",
                            str(videos[0])], capture_output=True, text=True, timeout=60)
    st = json.loads(probe.stdout)["streams"][0]
    w, h = st["width"] // 2 * 2, st["height"] // 2 * 2
    fps = float(Fraction(st["r_frame_rate"])) or 25.0
    n, chains = len(videos), []
    for k in range(n):
        keep = durations[k] + (xfade if k < n - 1 and xfade > 0 else 0)
        chains.append(f"[{k}:v]fps={fps:g},scale={w}:{h},setsar=1,format=yuv420p,"
                      f"tpad=stop_mode=clone:stop_duration={keep + 1:.3f},"
                      f"trim=duration={keep:.3f},setpts=PTS-STARTPTS,fps={fps:g}[v{k}]")
    last, t = "v0", 0.0
    for k in range(1, n):
        t += durations[k - 1]
        tag = f"x{k}"
        if xfade > 0:
            chains.append(f"[{last}][v{k}]xfade=transition=fade:duration={xfade:.3f}:"
                          f"offset={t:.3f}[{tag}]")
        else:
            chains.append(f"[{last}][v{k}]concat=n=2:v=1:a=0[{tag}]")
        last = tag
    args = [pipeline.FFMPEG, "-v", "error", "-y"]
    for v in videos:
        args += ["-i", str(v)]
    args += ["-i", str(audio_wav), "-filter_complex", ";".join(chains), "-map", f"[{last}]",
             "-map", f"{n}:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-t", f"{sum(durations):.3f}",
             "-movflags", "+faststart", str(out)]
    _ff(args, timeout=1800)
    return out


def generate_long(segments: list, work_dir: Path, image: bytes = None, video: bytes = None,
                  prompt: str = "", model: str = "", size: str = "480", fast: bool = True,
                  seed: int = 42, timeout: int = TIMEOUT_S, on_wait=None, on_segment=None,
                  meta_out: dict = None, xfade: float = XFADE_S) -> bytes:
    """Segments (16 kHz wav bytes each) -> one MP4 with their concatenated audio.

    Every segment is a generate() call from the same portrait with the same seed, prompt,
    size and model, one after another. Done segments are kept in work_dir/segments/ as
    <k>-<hash>.mp4 (hash of segment audio + portrait + params) and reused on a retry.
    on_segment(k, n) is called before segment k (1-based); a failure raises RuntimeError
    naming the segment. A single segment is just generate() (still cached)."""
    if video and len(segments) > 1:
        raise AvatarError("参考视频（v2v）不分段：请把 segment_s 设为 0")
    img = portrait_png(image) if image else None
    params = {"model": model or default_model(), "prompt": (prompt or "").strip(),
              "size": str(size), "fast": bool(fast), "seed": int(seed)}
    seg_dir = Path(work_dir) / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    n, paths, durs, metas = len(segments), [], [], []
    for k, wav in enumerate(segments, 1):
        key = segment_key(wav, img if img else video, params)
        p = seg_dir / f"{k:02d}-{key}.mp4"
        durs.append(round(wav_seconds(wav), 3))
        if on_segment:
            on_segment(k, n, p.is_file())
        if not p.is_file():
            meta = {}
            try:
                mp4 = generate(wav, image=img, video=video, timeout=timeout, on_wait=on_wait,
                               meta_out=meta, **params)
            except AvatarError:
                raise
            except Exception as e:  # noqa: BLE001 - name the segment
                raise RuntimeError(f"第 {k}/{n} 段失败：{e}") from e
            tmp = p.with_suffix(".part")
            tmp.write_bytes(mp4)
            tmp.replace(p)
            (seg_dir / f"{k:02d}-{key}.json").write_text(json.dumps(meta, default=str))
        else:
            mf = seg_dir / f"{k:02d}-{key}.json"
            meta = json.loads(mf.read_text()) if mf.is_file() else {}
            meta["reused"] = True
        metas.append(meta)
        paths.append(p)
    if meta_out is not None:
        meta_out.update(metas[0] if n == 1 else {
            "segments": n, "segment_seconds": durs, "seams": [round(sum(durs[:i]), 3)
                                                              for i in range(1, n)],
            "render_s": sum(float(m.get("render_s") or 0) for m in metas),
            "reused": sum(1 for m in metas if m.get("reused")), "per_segment": metas})
    if n == 1:
        return paths[0].read_bytes()
    audio = seg_dir / "joined_audio.wav"
    audio.write_bytes(concat_wavs(segments))
    out = seg_dir / "joined.mp4"
    join(paths, durs, audio, out, xfade)
    return out.read_bytes()
