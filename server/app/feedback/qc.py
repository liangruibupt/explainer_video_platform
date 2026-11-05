"""Deterministic QC metrics for a feedback target (docs/PHASE5_CONTRACTS.md).

measure(target) reads files on disk only: ffprobe/ffmpeg (ebur128, silencedetect) and the
job / run records. No LLM, no GPU. A key is left out when it does not apply or its input
file is missing; a broken file never raises.

snapshot(target) is what a feedback item stores: skill version + commit, a copy of the
record (job.json / run.json / state.json), measure(target) and the key file paths
(relative to data/).
"""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from .. import pipeline
from ..pipeline import DATA
from . import skill_info

SILENCE_DB = -50  # silencedetect noise floor
SILENCE_MIN_S = 0.5
DEFAULT_MUSIC_VOLUME = 0.72  # mix_bgm.py --music-volume default
FOLDERS = {"image": "images", "video": "videos", "music": "music", "avatar": "avatars"}
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")
AUDIO_EXT = (".wav", ".flac", ".mp3", ".m4a", ".ogg")


def _tool(path: str, name: str) -> str:
    return path if Path(path).is_file() else (shutil.which(name) or name)


def _ff():
    return _tool(pipeline.FFMPEG, "ffmpeg")


def _fp():
    return _tool(pipeline.FFPROBE, "ffprobe")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _isfile(p) -> bool:
    try:
        return p is not None and Path(p).is_file() and Path(p).stat().st_size > 0
    except OSError:
        return False


# ---------------------------------------------------------------- media probes

def duration(path) -> float | None:
    try:
        r = subprocess.run([_fp(), "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                           timeout=60)
        return round(float(r.stdout.strip()), 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def has_audio(path) -> bool:
    try:
        r = subprocess.run([_fp(), "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=60)
        return bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def lufs(path) -> float | None:
    """Integrated loudness via ffmpeg ebur128; -99.0 for digital silence; None on error."""
    try:
        r = subprocess.run([_ff(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                            "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.findall(r"I:\s+(-?[0-9.]+|-inf) LUFS", r.stderr)
    if not m:
        return None
    return -99.0 if m[-1] == "-inf" else round(max(float(m[-1]), -99.0), 2)


def silence_ratio(path, total: float | None = None) -> float | None:
    total = total or duration(path)
    if not total:
        return None
    try:
        r = subprocess.run([_ff(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                            f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S}",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[0-9.]+)", r.stderr)]
    silent = 0.0
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else total
        silent += max(0.0, min(e, total) - max(s, 0.0))
    return round(min(silent / total, 1.0), 4)


# ---------------------------------------------------------------- captions

_TAG = re.compile(r"\{[^}]*\}")
_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()《》\-—…·]")


def _ass_time(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def caption_lines(d: Path) -> list:
    """[(start, end, text)] from captions.ass, else words.json grouped by scene."""
    ass = d / "captions.ass"
    out = []
    if _isfile(ass):
        try:
            for line in ass.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.startswith("Dialogue:"):
                    continue
                parts = line[len("Dialogue:"):].split(",", 9)
                if len(parts) < 10:
                    continue
                text = _TAG.sub("", parts[9]).replace("\\N", " ").replace("\\n", " ")
                out.append((_ass_time(parts[1]), _ass_time(parts[2]), text))
        except (OSError, ValueError):
            out = []
        if out:
            return out
    words = _read_json(d / "words.json")
    if isinstance(words, list):
        groups = {}
        for w in words:
            if isinstance(w, dict) and "start" in w and "end" in w:
                g = groups.setdefault(w.get("scene", ""), [])
                g.append(w)
        for g in groups.values():
            out.append((min(w["start"] for w in g), max(w["end"] for w in g),
                        "".join(str(w.get("text", "")) for w in g)))
    return out


def caption_metrics(d: Path) -> dict:
    lines = sorted(caption_lines(d))
    if not lines:
        return {}
    cps = []
    for s, e, t in lines:
        n = len(_PUNCT.sub("", t))
        if n:
            cps.append(n / max(e - s, 0.05))
    overlap, last_end = 0.0, None
    for s, e, _ in lines:
        if last_end is not None and s < last_end:
            overlap += min(last_end, e) - s
        last_end = e if last_end is None else max(last_end, e)
    out = {"caption_count": len(lines), "caption_overlap_s": round(overlap, 3)}
    if cps:
        out["caption_max_cps"] = round(max(cps), 2)
    return out


# ---------------------------------------------------------------- voice vs music

def music_volume(d: Path, record: dict) -> float:
    """The --music-volume the mix used: from pipeline.log, else the pipeline's rule."""
    log = d / "pipeline.log"
    try:
        hits = re.findall(r"mix_bgm\.py .*?--music-volume\s+([0-9.]+)",
                          log.read_text(errors="replace"))
        if hits:
            return float(hits[-1])
        if "mix_bgm.py" in log.read_text(errors="replace"):
            return DEFAULT_MUSIC_VOLUME
    except (OSError, ValueError):
        pass
    key = record.get("music") if isinstance(record, dict) else None
    if key == "ai" and record.get("music_file"):
        return pipeline.AI_MUSIC_VOLUME
    v = pipeline.MUSIC_LIB.get(key) if key else None
    if isinstance(v, dict):
        return float(v.get("volume", DEFAULT_MUSIC_VOLUME))
    return DEFAULT_MUSIC_VOLUME


def voice_music(d: Path, record: dict) -> dict:
    out = {}
    voice, bgm = d / "narration.wav", d / "bgm.wav"
    if _isfile(voice):
        v = lufs(voice)
        if v is not None:
            out["voice_lufs"] = v
    if _isfile(bgm):
        m = lufs(bgm)
        if m is not None:
            vol = music_volume(d, record)
            # the bed as it enters the mix (before the sidechain duck): bgm x volume
            m = -99.0 if m <= -99 or vol <= 0 else round(m + 20 * math.log10(vol), 2)
            out["music_lufs"] = m
    if "voice_lufs" in out and "music_lufs" in out and out["music_lufs"] > -99:
        out["voice_minus_music_db"] = round(out["voice_lufs"] - out["music_lufs"], 2)
    return out


# ---------------------------------------------------------------- targets

def _media_metrics(path: Path, target_s=None) -> dict:
    out = {}
    if not _isfile(path):
        return out
    dur = duration(path)
    if dur:
        out["duration_s"] = dur
        if target_s:
            out["target_s"] = target_s
            out["duration_ratio"] = round(dur / float(target_s), 3)
    if path.suffix.lower() in AUDIO_EXT or has_audio(path):
        lv = lufs(path)
        if lv is not None:
            out["lufs"] = lv
        sr = silence_ratio(path, dur)
        if sr is not None:
            out["silence_ratio"] = sr
    return out


def _job(job_id: str):
    d = pipeline.job_dir(job_id)
    rec = _read_json(d / "job.json")
    files = {}
    for name, fn in (("video", (rec or {}).get("video") or "final.mp4"),
                     ("mix", "narration_bgm.wav"), ("narration", "narration.wav"),
                     ("bgm", "bgm.wav"), ("captions", "captions.ass"),
                     ("words", "words.json"), ("scenes", "scenes.json")):
        p = d / fn
        if _isfile(p):
            files[name] = p
    return d, rec, files


def _job_metrics(job_id: str) -> dict:
    d, rec, files = _job(job_id)
    if rec is None and not files:
        return {}
    rec = rec or {}
    target_s = rec.get("seconds")
    main = files.get("video") or files.get("mix") or files.get("narration")
    out = _media_metrics(main, target_s) if main else {}
    if "duration_s" not in out and rec.get("duration") and target_s:
        out.update(duration_s=rec["duration"], target_s=target_s,
                   duration_ratio=round(rec["duration"] / float(target_s), 3))
    out.update(voice_music(d, rec))
    if rec.get("captions", True):
        out.update(caption_metrics(d))
    scenes = rec.get("scenes") or _read_json(d / "scenes.json")
    if isinstance(scenes, list) and scenes:
        out["scene_count"] = len(scenes)
    if isinstance(rec.get("steps"), list):
        out["failed_steps"] = [s.get("key") for s in rec["steps"]
                               if isinstance(s, dict) and s.get("status") == "failed"]
    return out


def _run(run_id: str):
    from ..workflows import engine
    d = engine.run_dir(run_id)
    return d, _read_json(d / "run.json")


def _run_files(d: Path, rec: dict, node_id: str = "") -> dict:
    """Output files of the run: the named node's outputs, else the saved works."""
    files = {}
    nodes = (rec or {}).get("nodes") or {}
    if node_id and isinstance(nodes.get(node_id), dict):
        for port, v in (nodes[node_id].get("outputs") or {}).items():
            if isinstance(v, str) and _isfile(v):
                files[port] = Path(v)
    for w in (rec or {}).get("works") or []:
        if isinstance(w, dict) and w.get("file"):
            p = d / "outputs" / w["file"]
            if _isfile(p):
                files.setdefault("video" if p.suffix.lower() in VIDEO_EXT else
                                 "work_" + str(w.get("node", "")), p)
    return files


def _explainer_job(rec: dict, node_id: str = "") -> str:
    """job_id of the explainer.video node (the named one, else the first)."""
    nodes = (rec or {}).get("nodes") or {}
    types = {n.get("id"): n.get("type") for n in ((rec or {}).get("workflow") or {})
             .get("nodes", []) if isinstance(n, dict)}
    order = [node_id] if node_id else []
    order += [k for k in nodes if k != node_id]
    for k in order:
        n = nodes.get(k)
        if isinstance(n, dict) and types.get(k) == "explainer.video" \
                and (n.get("meta") or {}).get("job_id"):
            return str(n["meta"]["job_id"])
    return ""


def _workflow_metrics(run_id: str, node_id: str = "") -> dict:
    d, rec = _run(run_id)
    if rec is None:
        return {}
    out = {}
    job_id = _explainer_job(rec, node_id)
    if job_id and re.match(r"^[A-Za-z0-9._-]+$", job_id):
        out.update(_job_metrics(job_id))
    files = _run_files(d, rec, node_id)
    main = files.get("video") or next(
        (p for p in files.values() if p.suffix.lower() in VIDEO_EXT + AUDIO_EXT), None)
    if main is not None and not job_id:
        # no explainer sub-job: measure the run's own output (e.g. audio.mix result)
        out.update(_media_metrics(main))
    nodes = rec.get("nodes") or {}
    out["node_errors"] = {k: str(n.get("error") or "")[:300] for k, n in nodes.items()
                          if isinstance(n, dict) and n.get("status") == "failed"}
    return out


def _item(kind: str, item_id: str):
    d = DATA / FOLDERS[kind] / item_id
    return d, _read_json(d / "state.json")


def _item_files(kind: str, d: Path, rec) -> dict:
    files = {}
    names = list((rec or {}).get("files") or [])
    if kind == "avatar":
        names = ["video.mp4"] + names
    for fn in names:
        if isinstance(fn, str) and "/" not in fn and _isfile(d / fn):
            p = d / fn
            ext = p.suffix.lower()
            key = "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else \
                "image" if ext in (".png", ".jpg", ".jpeg", ".webp") else fn
            files.setdefault(key, p)
    return files


def _item_metrics(kind: str, item_id: str) -> dict:
    d, rec = _item(kind, item_id)
    if rec is None:
        return {}
    files = _item_files(kind, d, rec)
    main = files.get("video") or files.get("audio")
    target = (rec or {}).get("seconds") if kind == "video" else None
    out = _media_metrics(main, target) if main else {}
    return out


def _safe_id(x) -> bool:
    return isinstance(x, str) and bool(re.match(r"^[A-Za-z0-9._-]{1,100}$", x)) \
        and x not in (".", "..")


def measure(target: dict) -> dict:
    """Deterministic metrics for {kind, id, node_id?}; {} when nothing can be read."""
    try:
        kind, tid = (target or {}).get("kind"), (target or {}).get("id")
        if not _safe_id(tid):
            return {}
        if kind == "job":
            return _job_metrics(tid)
        if kind == "workflow":
            nid = (target or {}).get("node_id") or ""
            return _workflow_metrics(tid, nid if _safe_id(nid) else "")
        if kind in FOLDERS:
            return _item_metrics(kind, tid)
    except Exception:  # noqa: BLE001 - QC must never break feedback capture
        return {}
    return {}


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(DATA))
    except ValueError:
        return str(p)


def snapshot(target: dict) -> dict:
    """{skill_version, skill_commit, record, qc, files} for a feedback item."""
    kind, tid = target.get("kind"), target.get("id")
    rec, files = None, {}
    try:
        if _safe_id(tid):
            if kind == "job":
                _, rec, files = _job(tid)
            elif kind == "workflow":
                d, rec = _run(tid)
                files = _run_files(d, rec or {}, target.get("node_id") or "")
                job_id = _explainer_job(rec or {}, target.get("node_id") or "")
                if job_id and _safe_id(job_id):
                    for k, v in _job(job_id)[2].items():
                        files.setdefault("job_" + k, v)
            elif kind in FOLDERS:
                d, rec = _item(kind, tid)
                files = _item_files(kind, d, rec)
    except Exception:  # noqa: BLE001
        pass
    return {**skill_info(), "record": rec, "qc": measure(target),
            "files": {k: _rel(v) for k, v in files.items()}}
