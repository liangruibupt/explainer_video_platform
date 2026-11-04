"""Asset store: uploaded or imported media under data/assets/<id>/.

Each asset folder holds file.<ext>, meta.json and (images, videos) thumb.jpg. The file type
is decided from the file's magic bytes; the extension only has to agree with it. ffprobe
confirms audio/video files really contain the expected stream and gives duration / size.

asset_path(id) -> Path of the stored file (used by the input.asset workflow node and other
parts). Ids are always checked against ASSET_ID before any path is built.
"""
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .. import pipeline

ROOT = pipeline.DATA / "assets"
ASSET_ID = re.compile(r"asset_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
PROJECT_ID = re.compile(r"proj_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
MAX_BYTES = 200 * 1024 * 1024
KINDS = ("image", "audio", "video")
# format -> (kind, allowed extensions, mime)
FORMATS = {
    "png": ("image", ("png",), "image/png"),
    "jpg": ("image", ("jpg", "jpeg"), "image/jpeg"),
    "webp": ("image", ("webp",), "image/webp"),
    "wav": ("audio", ("wav",), "audio/wav"),
    "mp3": ("audio", ("mp3",), "audio/mpeg"),
    "flac": ("audio", ("flac",), "audio/flac"),
    "m4a": ("audio", ("m4a",), "audio/mp4"),
    "mp4": ("video", ("mp4", "m4v"), "video/mp4"),
    "mov": ("video", ("mov",), "video/quicktime"),
    "webm": ("video", ("webm",), "video/webm"),
}
EXT_TO_FMT = {e: f for f, (_, exts, _m) in FORMATS.items() for e in exts}
_lock = threading.Lock()


class AssetError(ValueError):
    """Rejected upload / import; status is the HTTP code the API should answer with."""

    def __init__(self, msg, status=415):
        super().__init__(msg)
        self.status = status


def new_id(prefix="asset") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def valid_id(asset_id) -> bool:
    return isinstance(asset_id, str) and ASSET_ID.fullmatch(asset_id) is not None


def _dir(asset_id: str) -> Path:
    if not valid_id(asset_id):
        raise KeyError(asset_id)
    return ROOT / asset_id


# ---------------------------------------------------------------- type detection

def sniff(head: bytes, ext: str) -> str:
    """Format name from the first bytes; ext (lowercase, no dot) picks between the
    ISO-BMFF family (mp4 / mov / m4a) and must agree with the detected format."""
    ext = ext.lower().lstrip(".")
    fmt = None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        fmt = "png"
    elif head.startswith(b"\xff\xd8\xff"):
        fmt = "jpg"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fmt = "webp"
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        fmt = "wav"
    elif head.startswith(b"fLaC"):
        fmt = "flac"
    elif head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF
                                     and head[1] & 0xE0 == 0xE0 and head[1] & 0x06):
        fmt = "mp3"
    elif head.startswith(b"\x1a\x45\xdf\xa3"):
        fmt = "webm"
    elif head[4:8] == b"ftyp":
        brand = head[8:12]
        if ext in ("mp4", "m4v", "mov", "m4a"):
            fmt = {"m4v": "mp4"}.get(ext, ext)
        else:
            fmt = "m4a" if brand in (b"M4A ", b"M4B ") else "mov" if brand == b"qt  " \
                else "mp4"
    elif head[4:8] in (b"moov", b"mdat", b"wide", b"free") and ext == "mov":
        fmt = "mov"
    if fmt is None:
        raise AssetError("不支持的文件类型（只接受 png/jpg/webp、wav/mp3/flac/m4a、mp4/mov/webm）")
    if ext and ext not in FORMATS[fmt][1]:
        raise AssetError(f"文件内容是 {fmt}，和扩展名 .{ext} 不符")
    return fmt


def probe(path: Path) -> dict:
    try:
        out = subprocess.run([pipeline.FFPROBE, "-v", "error", "-show_entries",
                              "format=duration:stream=codec_type,width,height", "-of", "json",
                              str(path)], capture_output=True, text=True, timeout=60)
        j = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    if out.returncode != 0:
        return {}
    streams = j.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = any(s.get("codec_type") == "audio" for s in streams)
    try:
        dur = round(float(j.get("format", {}).get("duration") or 0), 3)
    except ValueError:
        dur = 0.0
    return {"duration": dur, "video": v, "audio": a}


def _thumb(kind: str, src: Path, dst: Path, duration: float) -> bool:
    if kind == "image":
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            bg.thumbnail((320, 320 * 4))
            if bg.width > 320:
                bg = bg.resize((320, max(1, round(bg.height * 320 / bg.width))))
            bg.save(dst, "JPEG", quality=85)
        return True
    if kind == "video":
        at = "1" if duration > 1.05 else "0"
        r = subprocess.run([pipeline.FFMPEG, "-v", "error", "-y", "-ss", at, "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=320:-2", str(dst)],
                           capture_output=True, timeout=120)
        return r.returncode == 0 and dst.exists()
    return False


# ---------------------------------------------------------------- meta

def _write_meta(d: Path, meta: dict) -> None:
    tmp = d / f".meta.{uuid.uuid4().hex[:6]}.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    tmp.replace(d / "meta.json")


def get(asset_id: str) -> dict:
    try:
        p = _dir(asset_id) / "meta.json"
    except KeyError:
        return {}
    return json.loads(p.read_text()) if p.exists() else {}


def asset_path(asset_id: str) -> Path:
    """Path of the stored file; KeyError when the id is malformed or unknown."""
    m = get(asset_id)
    if not m:
        raise KeyError(asset_id)
    p = _dir(asset_id) / m["file"]
    if not p.is_file():
        raise KeyError(asset_id)
    return p


def thumb_path(asset_id: str):
    m = get(asset_id)
    p = _dir(asset_id) / "thumb.jpg" if m and m.get("thumb") else None
    return p if p and p.is_file() else None


def list_all(kind: str = "", project_id: str = "") -> list:
    if not ROOT.exists():
        return []
    out = []
    for p in ROOT.glob("asset_*/meta.json"):
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if kind and m.get("kind") != kind:
            continue
        if project_id and m.get("project_id") != project_id:
            continue
        out.append(m)
    return sorted(out, key=lambda m: m.get("created", 0), reverse=True)


def clean_name(name: str, fallback: str = "未命名") -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()
    return name[:200] or fallback


def ingest(src: Path, filename: str, name: str = "", project_id=None, source=None) -> dict:
    """Validate src (a temp file the caller owns, or a read-only file to copy) and store it
    as a new asset. Raises AssetError on a rejected file."""
    size = src.stat().st_size
    if size == 0:
        raise AssetError("文件是空的", 422)
    if size > MAX_BYTES:
        raise AssetError("文件超过 200 MB", 413)
    if project_id is not None and (not isinstance(project_id, str)
                                   or not PROJECT_ID.fullmatch(project_id)):
        raise AssetError("project_id 格式不对", 422)
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext and not re.fullmatch(r"[a-z0-9]{1,5}", ext):
        ext = ""
    with open(src, "rb") as f:
        head = f.read(64)
    fmt = sniff(head, ext)
    kind, exts, mime = FORMATS[fmt]
    meta = {"kind": kind, "format": fmt, "mime": mime, "size": size}
    if kind == "image":
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(src) as im:
                im.verify()
            with Image.open(src) as im:
                meta.update(width=im.width, height=im.height)
        except (UnidentifiedImageError, OSError, SyntaxError) as e:
            raise AssetError(f"图片无法解析：{e}") from None
    else:
        pr = probe(src)
        if not pr:
            raise AssetError("ffprobe 无法解析这个文件")
        if kind == "video":
            if not pr["video"]:
                raise AssetError("文件里没有视频轨")
            meta.update(width=pr["video"].get("width"), height=pr["video"].get("height"),
                        has_audio=pr["audio"])
        elif not pr["audio"]:
            raise AssetError("文件里没有音轨")
        meta["duration"] = pr["duration"]
    asset_id = new_id()
    d = ROOT / asset_id
    tmpd = ROOT / f".{asset_id}.tmp"
    tmpd.mkdir(parents=True)
    try:
        dst = tmpd / f"file.{exts[0]}"
        shutil.copyfile(src, dst)
        h = hashlib.sha256()
        with open(dst, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        try:
            has_thumb = _thumb(kind, dst, tmpd / "thumb.jpg", meta.get("duration") or 0)
        except Exception:  # noqa: BLE001 - a missing thumbnail is not fatal
            has_thumb = False
        stem = Path(filename or "").stem
        meta.update(id=asset_id, name=clean_name(name or stem), created=time.time(),
                    project_id=project_id or None, sha256=h.hexdigest(), file=dst.name,
                    thumb=has_thumb, original_name=clean_name(Path(filename or "").name, ""),
                    source=source)
        _write_meta(tmpd, meta)
        tmpd.rename(d)
    except BaseException:
        shutil.rmtree(tmpd, ignore_errors=True)
        raise
    return meta


def update(asset_id: str, **changes) -> dict:
    with _lock:
        m = get(asset_id)
        if not m:
            raise KeyError(asset_id)
        if "name" in changes:
            m["name"] = clean_name(changes["name"])
        if "project_id" in changes:
            m["project_id"] = changes["project_id"] or None
        m["updated"] = time.time()
        _write_meta(_dir(asset_id), m)
        return m


def delete(asset_id: str) -> bool:
    if not get(asset_id):
        return False
    d = _dir(asset_id)
    trash = ROOT / f".{asset_id}.deleted"
    d.rename(trash)
    shutil.rmtree(trash, ignore_errors=True)
    return True


def clear_project(project_id: str) -> int:
    n = 0
    for m in list_all(project_id=project_id):
        update(m["id"], project_id=None)
        n += 1
    return n
