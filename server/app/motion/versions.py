"""Saving an edited workbench project as a new version of a job (docs/REMOTION_CONTRACT.md §7).

jobs/<id>/versions/
  index.json            [{n, file, status, created, warnings, error}] for n >= 2 (atomic writes)
  vN.project.json       the project as rendered (after the voice lock)
  vN.mp4                the render
  vN.log                Remotion CLI log
  .render-public/       dereferenced copy of the public files (Remotion refuses symlinks)
Version 1 is the job's final.mp4 and is not stored in index.json. job.json is never touched.

The voice track (narration + ducked music) is locked: whatever the editor did with it, the saved
version gets the original clip back. Shots whose start or length moved more than LOCK_TOL frames
away from the narration timeline produce a warning on the record.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from pathlib import Path

from ..remotion import render as rrender
from . import workbench

MAX_PROJECT_BYTES = 8_000_000
LOCK_TOL = 15
VOICE_LABEL = "配音（已锁定）"
VOICE_TRACK = "配音（已锁定）"
MAX_TRACKS = 64
MAX_CLIPS = 4000

_index_lock = threading.Lock()
_queue: "queue.Queue[tuple[str, Path, int]]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_active: set[tuple[str, int]] = set()


class VersionError(ValueError):
    pass


# ---------------------------------------------------------------- index

def vdir(d: Path) -> Path:
    return d / "versions"


def _read_index(d: Path) -> list[dict]:
    try:
        recs = json.loads((vdir(d) / "index.json").read_text())
        return recs if isinstance(recs, list) else []
    except (OSError, ValueError):
        return []


def _write_index(d: Path, recs: list[dict]) -> None:
    vd = vdir(d)
    vd.mkdir(exist_ok=True)
    tmp = vd / f".index.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    os.replace(tmp, vd / "index.json")


def _update(d: Path, n: int, **patch) -> dict | None:
    with _index_lock:
        recs = _read_index(d)
        for r in recs:
            if r.get("n") == n:
                r.update(patch)
                _write_index(d, recs)
                return r
    return None


def list_versions(d: Path) -> list[dict]:
    final = d / "final.mp4"
    v1 = {"n": 1, "file": "final.mp4", "status": "done" if final.exists() else "failed",
          "created": final.stat().st_mtime if final.exists() else None, "warnings": [],
          "error": None if final.exists() else "final.mp4 missing"}
    return [v1] + sorted(_read_index(d), key=lambda r: r.get("n", 0))


def video_path(d: Path, n: int) -> Path | None:
    for r in list_versions(d):
        if r["n"] == n and r["status"] == "done":
            p = d / r["file"] if n == 1 else vdir(d) / r["file"]
            return p if p.exists() else None
    return None


def recover(data_root: Path) -> int:
    """Startup only (never at import): records left queued/rendering by a previous process
    are marked failed. Returns how many were changed."""
    changed = 0
    for idx in (data_root / "jobs").glob("*/versions/index.json"):
        d = idx.parent.parent
        with _index_lock:
            recs = _read_index(d)
            hit = False
            for r in recs:
                if r.get("status") in ("queued", "rendering") and (d.name, r.get("n")) not in _active:
                    r.update(status="failed", error="渲染期间服务重启，渲染中断")
                    hit = True
                    changed += 1
            if hit:
                _write_index(d, recs)
    return changed


# ---------------------------------------------------------------- project checks

def _load_job_data(proj: Path) -> tuple[dict, dict]:
    data = proj / "src" / "data"
    tl = json.loads((data / "timeline.json").read_text())
    try:
        audio = json.loads((data / "audio.json").read_text())
    except (OSError, ValueError):
        audio = {"voice": None, "sfx": []}
    return tl, audio


def _num(v, name: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
        raise VersionError(f"{name} 必须是 [{lo}, {hi}] 之间的数字")
    return v


def validate(project, tl: dict) -> dict:
    """Shape and size checks; fps/size must equal the job's timeline. Returns the project."""
    if not isinstance(project, dict):
        raise VersionError("project 必须是对象")
    raw = json.dumps(project, ensure_ascii=False)
    if len(raw.encode()) > MAX_PROJECT_BYTES:
        raise VersionError(f"工程 JSON 超过 {MAX_PROJECT_BYTES} 字节")
    for k in ("fps", "width", "height"):
        if project.get(k) != tl.get(k):
            raise VersionError(f"工程的 {k}={project.get(k)!r} 与任务不一致（应为 {tl.get(k)!r}）")
    tracks = project.get("tracks")
    if not isinstance(tracks, list) or not tracks or len(tracks) > MAX_TRACKS:
        raise VersionError(f"project.tracks 必须是 1 到 {MAX_TRACKS} 条轨道的列表")
    total = sum(s["duration"] for s in tl["scenes"])
    limit = max(total * 3, tl["fps"] * 60)
    n = 0
    for t in tracks:
        if not isinstance(t, dict) or not isinstance(t.get("clips"), list):
            raise VersionError("每条轨道都需要 clips 列表")
        for c in t["clips"]:
            n += 1
            if not isinstance(c, dict) or not isinstance(c.get("cardId"), str):
                raise VersionError("每个片段都需要 cardId")
            _num(c.get("start"), "clip.start", 0, limit)
            _num(c.get("duration"), "clip.duration", 1, limit)
            if c["start"] + c["duration"] > limit:
                raise VersionError(f"片段结束帧超过 {limit}（原片长度的 3 倍）")
            if not isinstance(c.get("props", {}), dict):
                raise VersionError("clip.props 必须是对象")
    if n > MAX_CLIPS:
        raise VersionError(f"片段数超过 {MAX_CLIPS}")
    return project


def _is_voice(clip: dict, voice: dict) -> bool:
    return clip.get("cardId") == "audio-clip" and (clip.get("props") or {}).get("file") == voice["src"]


def _total(tl: dict) -> int:
    return max((s["from"] + s["duration"] for s in tl["scenes"]), default=0)


def voice_clip(tl: dict, voice: dict) -> dict:
    """The voice clip exactly as the workbench imports it (projectImport.packAudio)."""
    start = max(0, round(voice["from"]))
    dur = max(2, min(round(voice["duration"]), _total(tl) - start))
    return {"id": "clip_voice_locked", "cardId": "audio-clip", "start": start, "duration": dur,
            "inOffset": 0, "speed": 1, "opacity": 1, "scale": 1, "x": 0, "y": 0,
            "props": {"file": voice["src"], "volume": voice.get("volume", 1)},
            "label": VOICE_LABEL}


def apply_voice_lock(project: dict, tl: dict, audio: dict) -> tuple[dict, list[str]]:
    """Put the original voice clip back (position, length, volume, speed, trim). Returns the new
    project and a warning when the editor had changed it."""
    voice = audio.get("voice")
    p = json.loads(json.dumps(project))
    if not voice:
        return p, []
    want = voice_clip(tl, voice)
    found: list[tuple[int, dict]] = []
    for ti, t in enumerate(p["tracks"]):
        for c in t["clips"]:
            if _is_voice(c, voice):
                found.append((ti, c))
    keys = ("start", "duration", "inOffset", "speed")
    intact = (len(found) == 1 and not p["tracks"][found[0][0]].get("hidden")
              and all(found[0][1].get(k) == want[k] for k in keys)
              and (found[0][1].get("props") or {}).get("volume", 1) == want["props"]["volume"]
              and found[0][1].get("opacity", 1) == 1)
    if intact:
        return p, []
    for t in p["tracks"]:
        t["clips"] = [c for c in t["clips"] if not _is_voice(c, voice)]
    home = found[0][0] if found else None
    if home is not None and not p["tracks"][home].get("hidden") and not p["tracks"][home]["clips"]:
        p["tracks"][home]["clips"].append(want)
    else:
        if home is not None and not p["tracks"][home]["clips"]:
            p["tracks"].pop(home)
        p["tracks"].append({"id": "track_voice_locked", "name": VOICE_TRACK, "clips": [want]})
    return p, ["配音轨已锁定：编辑器里对配音的改动未保存，已恢复原配音（位置、长度、音量）"]


def drift_warnings(project: dict, tl: dict) -> list[str]:
    """A shot whose start or length is more than LOCK_TOL frames off the narration timeline."""
    fps = tl.get("fps", 30)
    clips = [c for t in project["tracks"] if not t.get("hidden") for c in t["clips"]]
    out = []
    for s in tl["scenes"]:
        card = f"proj:shot:scene-{s['id']}"
        mine = [c for c in clips if c.get("cardId") == card or c.get("cardId", "").startswith(card + "~")]
        name = f"镜头 {s['id']}「{s['label']}」"
        if not mine:
            out.append(f"{name}已从时间线移除，配音仍按原时间播放")
            continue
        c = min(mine, key=lambda c: abs(c["start"] - s["from"]))
        ds = round(c["start"] - s["from"])
        dd = round(c["duration"] - s["duration"])
        if abs(ds) > LOCK_TOL:
            out.append(f"{name}起点偏离配音 {ds:+d} 帧（{ds / fps:+.1f} 秒）")
        if abs(dd) > LOCK_TOL:
            out.append(f"{name}时长与配音相差 {dd:+d} 帧（{dd / fps:+.1f} 秒）")
    return out


# ---------------------------------------------------------------- render

def submit(job_id: str, d: Path, project) -> dict:
    """Validate, lock the voice, store vN.project.json and queue the render. Returns the record."""
    proj = d / "remotion"
    if not (proj / "src" / "workbench.ts").exists():
        raise VersionError("该任务没有 Remotion 工程")
    tl, audio = _load_job_data(proj)
    project = validate(project, tl)
    locked, warnings = apply_voice_lock(project, tl, audio)
    warnings += drift_warnings(locked, tl)
    vd = vdir(d)
    vd.mkdir(exist_ok=True)
    with _index_lock:
        recs = _read_index(d)
        n = max([1] + [r.get("n", 0) for r in recs]) + 1
        (vd / f"v{n}.project.json").write_text(json.dumps(locked, ensure_ascii=False))
        rec = {"n": n, "file": f"v{n}.mp4", "status": "queued", "created": time.time(),
               "warnings": warnings, "error": None}
        recs.append(rec)
        _write_index(d, recs)
        _active.add((job_id, n))
    _ensure_worker()
    _queue.put((job_id, d, n))
    return dict(rec)


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="motion-versions", daemon=True)
            _worker.start()


def _loop() -> None:
    while True:
        job_id, d, n = _queue.get()
        try:
            render_version(job_id, d, n)
        finally:
            _active.discard((job_id, n))
            _queue.task_done()


def _sync_public(proj: Path, dst: Path) -> None:
    """Dereferenced copy of what the workbench serves: the job's public/* plus the workbench's
    BGM library and demo textures (the vite export plugin does the same into .render-public)."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    src = proj / "public"
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=False)
    for rel, p in workbench.repo_public_entries():
        target = dst / rel
        if target.exists():  # the job's own files win (e.g. its own textures/)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(p, target, symlinks=False)


def render_version(job_id: str, d: Path, n: int) -> None:
    vd = vdir(d)
    proj = d / "remotion"
    log = vd / f"v{n}.log"
    tmp = vd / f".v{n}.tmp.mp4"
    _update(d, n, status="rendering", started=time.time())
    try:
        project = json.loads((vd / f"v{n}.project.json").read_text())
        _sync_public(proj, vd / ".render-public")
        rel = workbench.render_link(job_id, proj)
        # render.py has no env parameter: renders run one at a time on this worker thread, and
        # WB_PROJ is only read by the workbench's remotion.config.ts.
        os.environ["WB_PROJ"] = rel
        try:
            rrender.render(workbench.WB, tmp, log, entry="src/remotion/index.ts", comp="Main",
                           props={"project": project, "renderExact": True},
                           public_dir=vd / ".render-public", cwd=workbench.WB)
        finally:
            os.environ.pop("WB_PROJ", None)
        os.replace(tmp, vd / f"v{n}.mp4")
        _update(d, n, status="done", ended=time.time())
    except Exception as e:  # noqa: BLE001 - recorded on the version
        tail = ""
        try:
            lines = [l for l in log.read_text(errors="replace").splitlines() if l.strip()]
            tail = next((l for l in reversed(lines) if "Error" in l), lines[-1] if lines else "")
        except OSError:
            pass
        _update(d, n, status="failed", ended=time.time(),
                error=(f"{e}: {tail}" if tail else str(e))[:800])
    finally:
        for p in (tmp, tmp.with_suffix(".props.json")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
