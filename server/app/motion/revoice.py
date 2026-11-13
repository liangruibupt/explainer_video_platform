"""Re-voicing edited scenes: takes (docs/REVOICE_CONTRACT.md §2, part A).

A take is one full voice track of a job, under jobs/<id>/remotion/.voice/:

  takes/<n>/scenes.json      narrations used (the job's scenes.json with some narrations changed)
  takes/<n>/durations.json   frames per scene
  takes/<n>/audio/sNN.mp3    per-scene Polly audio (unchanged scenes copied from the base take)
  takes/<n>/narration.wav    all scenes re-concatenated (narrate.concat)
  takes/<n>/voice_bgm.wav    narration + ducked music, re-mixed by mix_bgm.py over the whole film
  takes/<n>/words.json       per-character timing (captions.py format, seconds)
  takes/<n>/timeline.json    src/data formats (REMOTION_CONTRACT §3/§5); revision kept
  takes/<n>/captions.json
  takes/<n>/audio.json
  takes/<n>/take.json        {base, lead, tail, voice_ms, offsets_ms, total_ms}
  takes/<n>/record.json      the HTTP record (n >= 1)
  live                       number of the take the live project uses (absent = 0)
  backup-<n>/                live files before take n was applied (undo restores them)

Take 0 is the original film, built lazily from the job dir and the current src/data. A new
take starts from the LIVE take: changed scenes are re-synthesized with narrate.py's own SSML,
lexicon, voice and lead/tail; nothing is time-stretched. A scene whose new audio is shorter
keeps its frames (padding absorbs it); a longer one grows and every later scene shifts by the
delta, voice included. One unchanged scene is re-synthesized as a voice drift guard.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import threading
import time
import traceback
import wave
from pathlib import Path

from .. import pipeline
from ..remotion import media
from . import versions

SCRIPTS = media.SCRIPTS
MAX_TEXT = 600
CAPTION_CARD = "proj:caption:caption"
SCENE_PREFIX = "proj:shot:scene-"
# take file -> live path under remotion/
LIVE = {"timeline.json": "src/data/timeline.json", "captions.json": "src/data/captions.json",
        "audio.json": "src/data/audio.json", "voice_bgm.wav": "public/audio/voice_bgm.wav"}

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_running: dict[str, int] = {}
_mods: dict[str, object] = {}
_whisper = None
_whisper_lock = threading.Lock()


class RevoiceError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _lock(jid: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(jid, threading.RLock())


def script(name: str):
    """narrate.py / captions.py imported as modules (their CLIs only run under __main__)."""
    if name not in _mods:
        spec = importlib.util.spec_from_file_location(f"evp_{name}_script", SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _mods[name] = mod
    return _mods[name]


def whisper():
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel("small", device="cpu", compute_type="int8",
                                    download_root=str(pipeline.TOOLS / "hf" / "hub"))
        return _whisper


# ---------------------------------------------------------------- paths and small helpers

def proj_dir(d: Path) -> Path:
    return Path(d) / "remotion"


def voice_dir(d: Path) -> Path:
    return proj_dir(d) / ".voice"


def take_dir(d: Path, n: int) -> Path:
    return voice_dir(d) / "takes" / str(n)


def live(d: Path) -> int:
    try:
        return int((voice_dir(d) / "live").read_text().strip())
    except (OSError, ValueError):
        return 0


def _write(p: Path, data: bytes | str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    if isinstance(data, str):
        tmp.write_text(data)
    else:
        tmp.write_bytes(data)
    os.replace(tmp, p)


def _json(p: Path):
    return json.loads(Path(p).read_text())


def _dump(p: Path, obj, indent=None) -> None:
    _write(p, json.dumps(obj, ensure_ascii=False, indent=indent))


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def _env() -> dict:
    return pipeline._env()


def _ffprobe() -> str:
    return pipeline.FFPROBE


def music_args(job: dict, d: Path) -> list[str]:
    """The same mix_bgm.py music arguments the pipeline's mix step used for this job."""
    if job.get("music") == "ai" and job.get("music_file"):
        return ["--music", str(Path(d) / job["music_file"]), "--music-volume",
                str(pipeline.AI_MUSIC_VOLUME)]
    if job.get("music") == "ai":
        return [str(a) for a in pipeline._music_arg(job.get("music_fallback") or "synth")]
    return [str(a) for a in pipeline._music_arg(job.get("music") or "synth")]


# ---------------------------------------------------------------- takes

def load_take(d: Path, n: int) -> dict:
    t = take_dir(d, n)
    out = {"n": n, "dir": t, "meta": _json(t / "take.json"), "scenes": _json(t / "scenes.json"),
           "durations": _json(t / "durations.json"), "timeline": _json(t / "timeline.json"),
           "captions": _json(t / "captions.json"), "audio": _json(t / "audio.json")}
    wp = t / "words.json"
    out["words"] = _json(wp) if wp.is_file() else []
    return out


def ensure_take0(d: Path) -> Path:
    """Take 0 = the original voice: the job dir's narrate/mix/captions outputs and the live
    src/data files (built once, before any take can be applied)."""
    d = Path(d)
    t0 = take_dir(d, 0)
    if (t0 / "take.json").is_file():
        return t0
    if live(d) != 0:
        raise RevoiceError("找不到原始配音（take 0），无法重新配音", 500)
    proj = proj_dir(d)
    scenes = _json(d / "scenes.json")
    frames = _json(d / "durations.json")
    tmp = t0.with_name(".0.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "audio").mkdir(parents=True)
    nar = script("narrate")
    voice_ms, offsets, t = [], [], 0
    for i, _ in enumerate(scenes):
        src = d / "audio" / f"s{i:02d}.mp3"
        shutil.copyfile(src, tmp / "audio" / src.name)
        v = nar.ms(str(src), _ffprobe())
        voice_ms.append(v)
        offsets.append(t + nar.LEAD)
        t += nar.scene_frames(v)[0]
    _dump(tmp / "scenes.json", scenes, 1)
    _dump(tmp / "durations.json", frames)
    shutil.copyfile(d / "narration.wav", tmp / "narration.wav")
    if (d / "words.json").is_file():
        shutil.copyfile(d / "words.json", tmp / "words.json")
    for name, rel in LIVE.items():
        shutil.copyfile(proj / rel, tmp / name)
    _dump(tmp / "take.json", {"base": None, "lead": nar.LEAD, "tail": nar.TAIL,
                              "voice_ms": voice_ms, "offsets_ms": offsets, "total_ms": t,
                              "created": time.time()})
    if t0.exists():
        shutil.rmtree(t0)
    tmp.rename(t0)
    return t0


def _record_path(d: Path, n: int) -> Path:
    return take_dir(d, n) / "record.json"


def _numbers(d: Path) -> list[int]:
    root = voice_dir(d) / "takes"
    if not root.is_dir():
        return []
    return sorted(int(p.name) for p in root.iterdir() if p.name.isdigit())


def _public(d: Path, rec: dict) -> dict:
    return {**rec, "live": rec.get("n") == live(d)}


def get(d: Path, n: int) -> dict | None:
    try:
        return _public(d, _json(_record_path(d, n)))
    except (OSError, ValueError):
        return None


def list_records(d: Path) -> list[dict]:
    out = [get(d, n) for n in _numbers(d) if n >= 1]
    return sorted((r for r in out if r), key=lambda r: r["n"], reverse=True)


def _update(d: Path, n: int, **patch) -> dict:
    p = _record_path(d, n)
    rec = _json(p)
    rec.update(patch)
    _dump(p, rec, 1)
    return rec


def audio_path(d: Path, n: int, scene_id: str) -> Path | None:
    try:
        scenes = _json(take_dir(d, n) / "scenes.json")
    except (OSError, ValueError):
        return None
    for i, s in enumerate(scenes):
        if s.get("id") == scene_id:
            p = take_dir(d, n) / "audio" / f"s{i:02d}.mp3"
            return p if p.is_file() else None
    return None


def live_scenes(d: Path) -> list[dict]:
    """scenes.json of the live take (narrations as currently voiced); the job's own when no
    take exists yet. For the consistency check (part B)."""
    p = take_dir(d, live(d)) / "scenes.json"
    return _json(p if p.is_file() else Path(d) / "scenes.json")


def recover(data_root: Path) -> int:
    """Startup only: takes left queued/running by a previous process are marked failed."""
    changed = 0
    for p in Path(data_root).glob("jobs/*/remotion/.voice/takes/*/record.json"):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        jid = p.parents[4].name
        if rec.get("status") in ("queued", "running") and _running.get(jid) != rec.get("n"):
            rec.update(status="failed", error="配音期间服务重启，已中断", ended=time.time())
            _dump(p, rec, 1)
            changed += 1
    return changed


# ---------------------------------------------------------------- submit / run

def submit(jid: str, d: Path, narrations, all_scenes: bool = False, *, start: bool = True) -> dict:
    d = Path(d)
    if narrations is None:
        narrations = {}
    if not isinstance(narrations, dict):
        raise RevoiceError("narrations 必须是 {scene_id: 旁白} 对象", 400)
    if not isinstance(all_scenes, bool):
        raise RevoiceError("all 必须是 true/false", 400)
    with _lock(jid):
        if jid in _running:
            raise RevoiceError(f"第 {_running[jid]} 次重新配音还在进行，请等它完成", 409)
        ensure_take0(d)
        base_n = live(d)
        base = load_take(d, base_n)
        texts = {s["id"]: s["narration"] for s in base["scenes"]}
        for sid, text in narrations.items():
            if sid not in texts:
                raise RevoiceError(f"没有这个段落：{sid}", 400)
            if not isinstance(text, str) or not text.strip():
                raise RevoiceError(f"{sid} 的旁白不能为空", 400)
            if len(text) > MAX_TEXT:
                raise RevoiceError(f"{sid} 的旁白超过 {MAX_TEXT} 字", 400)
        new = {sid: t.strip() for sid, t in narrations.items()}
        changed = [s["id"] for s in base["scenes"]
                   if all_scenes or (s["id"] in new and new[s["id"]] != texts[s["id"]])]
        if not changed:
            raise RevoiceError("没有改动的旁白，不需要重新配音", 400)
        n = max([0] + _numbers(d)) + 1
        t = take_dir(d, n)
        t.mkdir(parents=True)
        scenes = []
        for s in base["scenes"]:
            if s["id"] in changed:
                scenes.append({"id": s["id"], "old_text": s["narration"],
                               "new_text": new.get(s["id"], s["narration"])})
        rec = {"n": n, "status": "queued", "created": time.time(), "ended": None, "base": base_n,
               "all": all_scenes, "scenes": scenes, "shift_from": None, "total_frames_old":
               sum(base["durations"]), "total_frames_new": None, "voice_drift": False,
               "drift_check": None, "levels": None, "error": None, "timing": None}
        _dump(t / "record.json", rec, 1)
        _running[jid] = n
    if start:
        threading.Thread(target=run, args=(jid, d, n), name=f"revoice-{jid}-{n}",
                         daemon=True).start()
    return _public(d, rec)


def _rms_db(x) -> float:
    import numpy as np
    if len(x) == 0:
        return float("nan")
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    return round(20 * math.log10(r / 32768 + 1e-12), 2)


def _pcm(p: Path):
    """(samples as int16 array with channels interleaved -> mono mean, rate)."""
    import numpy as np
    with wave.open(str(p)) as w:
        ch, sr = w.getnchannels(), w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return a.reshape(-1, ch).astype(np.float64).mean(axis=1), sr


def _spans(x, sr, spans) -> "object":
    import numpy as np
    parts = [x[max(0, int(a * sr / 1000)):max(0, int(b * sr / 1000))] for a, b in spans if b > a]
    return np.concatenate(parts) if parts else np.zeros(0)


def levels(take: dict, scene_ids: list[str]) -> dict:
    """RMS of the mix over the voice of `scene_ids`, and of the music-only gaps between voices
    (150 ms trimmed each side), plus each scene's voice-only RMS from narration.wav."""
    t, m = take["dir"], take["meta"]
    idx = [i for i, s in enumerate(take["scenes"]) if s["id"] in scene_ids]
    mix, sr = _pcm(t / "voice_bgm.wav")
    nar, nsr = _pcm(t / "narration.wav")
    on, vo = m["offsets_ms"], m["voice_ms"]
    gaps = [(0, on[0] - 150)] + [(on[i] + vo[i] + 150, on[i + 1] - 150) for i in range(len(on) - 1)]
    return {"voice_rms_db": _rms_db(_spans(mix, sr, [(on[i], on[i] + vo[i]) for i in idx])),
            "music_gap_db": _rms_db(_spans(mix, sr, gaps)),
            "scenes": {take["scenes"][i]["id"]: _rms_db(_spans(nar, nsr, [(on[i], on[i] + vo[i])]))
                       for i in idx}}


def run(jid: str, d: Path, n: int) -> None:
    d = Path(d)
    t0 = time.time()
    timing = {"polly_s": 0.0, "whisper_s": 0.0, "mix_s": 0.0, "total_s": 0.0}
    try:
        _update(d, n, status="running", started=t0)
        build_take(jid, d, n, timing)
    except Exception as e:  # noqa: BLE001 - recorded on the take
        log = take_dir(d, n) / "revoice.log"
        with log.open("a") as f:
            f.write(traceback.format_exc())
        timing["total_s"] = round(time.time() - t0, 2)
        _update(d, n, status="failed", ended=time.time(), error=str(e)[:800], timing=timing)
    finally:
        with _lock(jid):
            if _running.get(jid) == n:
                _running.pop(jid, None)


def build_take(jid: str, d: Path, n: int, timing: dict) -> dict:
    t0 = time.time()
    job = pipeline.read_job(jid)
    rec = _json(_record_path(d, n))
    base = load_take(d, rec["base"])
    t = take_dir(d, n)
    log = t / "revoice.log"
    (t / "audio").mkdir(exist_ok=True)
    nar = script("narrate")
    lex = nar.load_lexicon()
    voice = job.get("voice") or "Zhiyu"
    fps = base["timeline"].get("fps", media.FPS)
    bm = base["meta"]
    lead, tail = bm["lead"], bm["tail"]
    new_text = {s["id"]: s["new_text"] for s in rec["scenes"]}
    scenes = copy.deepcopy(base["scenes"])
    for s in scenes:
        if s["id"] in new_text:
            s["narration"] = new_text[s["id"]]

    # 1. Polly: changed scenes, plus one unchanged scene as the drift guard
    tp = time.time()
    voice_ms = list(bm["voice_ms"])
    for i, s in enumerate(scenes):
        dst = t / "audio" / f"s{i:02d}.mp3"
        if s["id"] in new_text:
            nar.synth(s["narration"], str(dst), lex=lex, voice=voice)
            voice_ms[i] = nar.ms(str(dst), _ffprobe())
        else:
            shutil.copyfile(base["dir"] / "audio" / dst.name, dst)
    drift, check = False, None
    same = [(len(s["narration"]), i) for i, s in enumerate(scenes) if s["id"] not in new_text]
    if same:
        i = min(same)[1]
        probe = t / ".drift.mp3"
        nar.synth(scenes[i]["narration"], str(probe), lex=lex, voice=voice)
        ok = probe.read_bytes() == (base["dir"] / "audio" / f"s{i:02d}.mp3").read_bytes()
        probe.unlink()
        drift, check = not ok, {"scene_id": scenes[i]["id"], "identical": ok}
    else:  # every scene re-voiced: same-text scenes show whether Polly still matches
        hits = [i for i, s in enumerate(scenes) if s["narration"] == base["scenes"][i]["narration"]]
        if hits:
            i = hits[0]
            ok = ((t / "audio" / f"s{i:02d}.mp3").read_bytes()
                  == (base["dir"] / "audio" / f"s{i:02d}.mp3").read_bytes())
            check = {"scene_id": scenes[i]["id"], "identical": ok}
    timing["polly_s"] = round(time.time() - tp, 2)

    # 2. frames: shorter keeps the old length, longer grows and shifts everything after it
    old_frames = base["durations"]
    frames, shift, offsets = [], 0, []
    for i, s in enumerate(scenes):
        offsets.append(bm["offsets_ms"][i] + round(shift * 1000 / fps))
        if s["id"] in new_text:
            raw = nar.scene_frames(voice_ms[i], fps, lead, tail)[1]
            frames.append(max(old_frames[i], raw))
        else:
            frames.append(old_frames[i])
        shift += frames[i] - old_frames[i]
    total_ms = bm["total_ms"] + round(shift * 1000 / fps)
    clips = [str(t / "audio" / f"s{i:02d}.mp3") for i in range(len(scenes))]

    # 3. whole narration + whole mix
    tm = time.time()
    nar.concat(clips, offsets, total_ms, str(t / "narration.wav"), pipeline.FFMPEG)
    with log.open("a") as f:
        r = subprocess.run([pipeline.PY, str(SCRIPTS / "mix_bgm.py"), str(t),
                            *music_args(job, d)], env=_env(), stdout=f,
                           stderr=subprocess.STDOUT, timeout=900)
    if r.returncode:
        raise RuntimeError(f"mix_bgm.py 失败（退出码 {r.returncode}）：{log.read_text()[-500:]}")
    os.replace(t / "narration_bgm.wav", t / "voice_bgm.wav")
    (t / "bgm.wav").unlink(missing_ok=True)
    timing["mix_s"] = round(time.time() - tm, 2)

    # 4. words: Whisper for changed scenes (onto the NEW text), others shifted. Scene starts
    # follow captions.py exactly (frames / fps accumulated, + lead), so the unchanged lines of a
    # changed scene keep their frames.
    tw = time.time()
    words = []
    if job.get("captions"):
        cap = script("captions")
        shift, start = 0, 0.0
        for i, s in enumerate(scenes):
            if s["id"] in new_text:
                words += cap.scene_words(whisper(), clips[i], s, start + lead / 1000)
            else:
                ds = shift / fps
                words += [{**w, "start": round(w["start"] + ds, 3), "end": round(w["end"] + ds, 3)}
                          for w in base["words"] if w["scene"] == s["id"]]
            shift += frames[i] - old_frames[i]
            start += frames[i] / fps
        _dump(t / "words.json", words)
    timing["whisper_s"] = round(time.time() - tw, 2)

    # 5. src/data files (media.py builders; timeline.revision unchanged)
    total = sum(frames)
    tl = copy.deepcopy(base["timeline"])
    at = 0
    for s, k in zip(tl["scenes"], frames):
        s["from"], s["duration"] = at, k
        at += k
    stage = t / ".stage"
    try:
        audio = media.build_audio(scenes, frames, Path(d), stage, lambda m: None, fps)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    caps = media.build_captions(words, total, fps) if job.get("captions") and words else []
    _dump(t / "scenes.json", scenes, 1)
    _dump(t / "durations.json", frames)
    _dump(t / "timeline.json", tl, 1)
    _dump(t / "captions.json", caps)
    _dump(t / "audio.json", audio, 1)
    _dump(t / "take.json", {"base": rec["base"], "lead": lead, "tail": tail, "voice_ms": voice_ms,
                            "offsets_ms": offsets, "total_ms": total_ms, "created": time.time()})

    # 6. record
    take = load_take(d, n)
    ids = list(new_text)
    lv, lb = levels(take, ids), levels(base, ids)
    out = []
    for i, s in enumerate(scenes):
        if s["id"] not in new_text:
            continue
        raw = nar.scene_frames(voice_ms[i], fps, lead, tail)[1]
        out.append({"id": s["id"], "old_text": base["scenes"][i]["narration"],
                    "new_text": s["narration"], "old_frames": old_frames[i],
                    "new_frames": frames[i], "delta_frames": frames[i] - old_frames[i],
                    "voice_ms_old": bm["voice_ms"][i], "voice_ms_new": voice_ms[i],
                    "voice_delta_s": round((voice_ms[i] - bm["voice_ms"][i]) / 1000, 3),
                    "raw_frames": raw})
    grow = [s["id"] for s in out if s["delta_frames"] > 0]
    timing["total_s"] = round(time.time() - t0, 2)
    return _update(d, n, status="ready", ended=time.time(), scenes=out,
                   shift_from=grow[0] if grow else None, total_frames_new=total,
                   voice_drift=drift, drift_check=check,
                   levels={"voice_rms_db": lv["voice_rms_db"], "music_gap_db": lv["music_gap_db"],
                           "base_voice_rms_db": lb["voice_rms_db"],
                           "base_music_gap_db": lb["music_gap_db"],
                           "scenes": {k: {"old_db": lb["scenes"][k], "new_db": lv["scenes"][k]}
                                      for k in ids}},
                   timing=timing)


# ---------------------------------------------------------------- project transform

def manifest_key(tl: dict) -> str | None:
    """manifestKey() of the workbench (src/cards/manifest.ts) for a job with a revision:
    `${name}@${total}f#${revision}`, name = first scene label, total = last scene end."""
    if not tl.get("revision"):
        return None
    name = (tl["scenes"][0].get("label") if tl["scenes"] else None) or "explainer"
    total = max((s["from"] + s["duration"] for s in tl["scenes"]), default=0)
    return f"{name}@{total}f#{tl['revision']}"


def _lines_by_scene(caps: list, tl: dict) -> dict[str, list]:
    starts = {s["id"]: s["from"] for s in tl["scenes"]}
    out: dict[str, list] = {s["id"]: [] for s in tl["scenes"]}
    for c in caps:
        if c.get("scene") in out:
            out[c["scene"]].append(c)
    return {k: [(c, (c["text"], c["from"] - starts[k], c["chars"])) for c in v]
            for k, v in out.items()}


def transform(project: dict, old: dict, new: dict, uid: str = "rv") -> tuple[dict, list[str]]:
    """Move the user's project from take `old` to take `new` (§2 Apply): scene clips shift by
    their scene's start change and follow its length if they still had it; captions of scenes
    whose lines changed are rebuilt from new captions.json with the user's caption style;
    every other clip shifts with the scene it starts in; the voice clip spans the new film."""
    otl, ntl = old["timeline"], new["timeline"]
    if [s["id"] for s in otl["scenes"]] != [s["id"] for s in ntl["scenes"]]:
        raise RevoiceError("配音前后的段落不一致", 500)
    p = copy.deepcopy(project)
    if not isinstance(p, dict) or not isinstance(p.get("tracks"), list):
        raise RevoiceError("project.tracks 必须是轨道列表", 400)
    os_ = {s["id"]: s for s in otl["scenes"]}
    ns_ = {s["id"]: s for s in ntl["scenes"]}
    shift = {k: ns_[k]["from"] - os_[k]["from"] for k in os_}

    def scene_at(frame) -> str:
        if isinstance(frame, bool) or not isinstance(frame, (int, float)):
            raise RevoiceError("clip.start 必须是数字", 400)
        for s in otl["scenes"]:
            if frame < s["from"] + s["duration"]:
                return s["id"]
        return otl["scenes"][-1]["id"]

    oline, nline = _lines_by_scene(old["captions"], otl), _lines_by_scene(new["captions"], ntl)
    same = {k for k in os_ if [x[1] for x in oline[k]] == [x[1] for x in nline[k]]}
    redo = [k for k in os_ if k not in same]
    by_start = {}  # old caption line start -> (scene, index) for lines the editor imported
    for k in os_:
        for i, (c, _) in enumerate(oline[k]):
            by_start.setdefault(c["from"], (k, i))
    voice = (old.get("audio") or {}).get("voice") or (new.get("audio") or {}).get("voice")
    nvoice = (new.get("audio") or {}).get("voice")
    counts = {"scene": 0, "caption_kept": 0, "caption_new": 0, "caption_dropped": 0, "other": 0}
    style: dict[str, tuple[int, dict]] = {}  # scene -> (track index, template clip)
    any_style: tuple[int, dict] | None = None
    for ti, tr in enumerate(p["tracks"]):
        if not isinstance(tr, dict) or not isinstance(tr.get("clips"), list):
            raise RevoiceError("每条轨道都需要 clips 列表", 400)
        keep = []
        for c in tr["clips"]:
            if not isinstance(c, dict):
                raise RevoiceError("片段必须是对象", 400)
            card = str(c.get("cardId", ""))
            start = c.get("start", 0)
            if voice and versions._is_voice(c, voice):
                c["start"], c["inOffset"] = 0, 0
                c["duration"] = versions.voice_clip(ntl, nvoice or voice)["duration"]
            elif card.startswith(SCENE_PREFIX):
                sid = card[len(SCENE_PREFIX):].split("~")[0]
                if sid in os_:
                    if c.get("duration") == os_[sid]["duration"]:
                        c["duration"] = ns_[sid]["duration"]
                    c["start"] = start + shift[sid]
                    counts["scene"] += 1
            elif card == CAPTION_CARD:
                hit = by_start.get(start)
                if any_style is None:
                    any_style = (ti, c)
                if hit and hit[0] in same:
                    k, i = hit
                    nl = nline[k][i][0]
                    c["start"], c["duration"] = nl["from"], nl["duration"]
                    c.setdefault("props", {})["chars"] = nl["chars"]
                    counts["caption_kept"] += 1
                elif hit:
                    style.setdefault(hit[0], (ti, c))
                    counts["caption_dropped"] += 1
                    continue
                else:  # added or moved by the user: rides along with its scene
                    c["start"] = start + shift[scene_at(start)]
                    counts["other"] += 1
            else:
                c["start"] = start + shift[scene_at(start)]
                counts["other"] += 1
            keep.append(c)
        tr["clips"] = keep
    k_new = 0
    for sid in redo:
        tmpl = style.get(sid) or any_style
        if tmpl is None:
            continue  # the user removed every caption: do not add them back
        ti, base = tmpl
        for nl, _ in nline[sid]:
            k_new += 1
            c = copy.deepcopy(base)
            c.update(id=f"clip_{uid}_{k_new:03d}", start=nl["from"], duration=nl["duration"],
                     label=nl["text"][:12])
            c["props"] = {**(base.get("props") or {}), "text": nl["text"], "chars": nl["chars"]}
            p["tracks"][ti]["clips"].append(c)
            counts["caption_new"] += 1
    for tr in p["tracks"]:
        if any(c.get("cardId") == CAPTION_CARD for c in tr["clips"]):
            tr["clips"].sort(key=lambda c: c.get("start", 0))
    key = manifest_key(ntl)
    if key:
        p["source"] = key
    fps = ntl.get("fps", 30)
    changes = []
    for s in otl["scenes"]:
        k = s["id"]
        dd = ns_[k]["duration"] - s["duration"]
        if k in redo or dd:
            what = (f"{dd / fps:+.2f} 秒，后面的段落顺延" if dd > 0 else
                    f"{dd / fps:+.2f} 秒，后面的段落提前" if dd < 0 else "长度不变")
            changes.append(f"{s.get('label', k)}：配音已更新，{what}")
    if counts["caption_new"] or counts["caption_dropped"]:
        changes.append(f"字幕：{counts['caption_dropped']} 行旧字幕换成 {counts['caption_new']} 行新字幕")
    old_total, new_total = versions._total(otl), versions._total(ntl)
    if old_total != new_total:
        changes.append(f"成片总长 {old_total} → {new_total} 帧")
    return p, changes


# ---------------------------------------------------------------- apply / undo

def _live_matches(d: Path, take: dict) -> bool:
    proj = proj_dir(d)
    return all(_sha(proj / rel) == _sha(take["dir"] / name) for name, rel in LIVE.items())


def _install(d: Path, src: Path, n_live: int) -> None:
    proj = proj_dir(d)
    for name, rel in LIVE.items():
        _write(proj / rel, (src / name).read_bytes())
    _write(voice_dir(d) / "live", str(n_live))


def _rebuild(jid: str, d: Path) -> bool | None:
    from . import scene_edit, workbench
    if not scene_edit.linked_here(jid, d):
        return None
    try:
        return bool(workbench.rebuild(jid))
    except Exception:  # noqa: BLE001 - the dev server still serves the unbundled UI
        return False


def apply(jid: str, d: Path, n: int, project) -> dict:
    d = Path(d)
    with _lock(jid):
        rec = get(d, n)
        if not rec:
            raise RevoiceError("没有这次配音", 404)
        if rec["status"] not in ("ready", "undone"):
            raise RevoiceError(f"只有已完成的配音可以应用，当前状态：{rec['status']}")
        cur = live(d)
        if cur != rec["base"]:
            raise RevoiceError(f"这次配音基于第 {rec['base']} 版配音，但当前用的是第 {cur} 版；"
                               "请基于当前配音重新生成")
        base, take = load_take(d, rec["base"]), load_take(d, n)
        if not _live_matches(d, base):
            raise RevoiceError("工程的配音文件已被改动（例如打开了别的版本），请基于当前配音重新生成")
        try:
            versions.validate(project, base["timeline"])
            out, changes = transform(project, base, take, uid=f"rv{n}")
            versions.validate(out, take["timeline"])
        except versions.VersionError as e:
            raise RevoiceError(str(e), 400)
        bk = voice_dir(d) / f"backup-{n}"
        if bk.exists():
            shutil.rmtree(bk)
        bk.mkdir(parents=True)
        proj = proj_dir(d)
        for name, rel in LIVE.items():
            shutil.copyfile(proj / rel, bk / name)
        (bk / "live").write_text(str(cur))
        _install(d, take["dir"], n)
        rebuilt = _rebuild(jid, d)
        _update(d, n, status="applied", applied=time.time(), prev_live=cur, rebuilt=rebuilt)
        return {"status": "applied", "project": out, "changes": changes, "rebuilt": rebuilt}


def undo(jid: str, d: Path, n: int, project) -> dict:
    d = Path(d)
    with _lock(jid):
        rec = get(d, n)
        if not rec:
            raise RevoiceError("没有这次配音", 404)
        if rec["status"] != "applied" or live(d) != n:
            raise RevoiceError("只能撤销当前正在使用的那次配音")
        bk = voice_dir(d) / f"backup-{n}"
        if not all((bk / name).is_file() for name in LIVE) or not (bk / "live").is_file():
            raise RevoiceError("找不到应用前的备份，无法撤销")
        take = load_take(d, n)
        if not _live_matches(d, take):
            raise RevoiceError("配音文件在应用后又被改动过，无法安全撤销")
        prev_n = int((bk / "live").read_text().strip())
        prev = load_take(d, prev_n)
        try:
            versions.validate(project, take["timeline"])
            out, changes = transform(project, take, prev, uid=f"un{n}")
            versions.validate(out, prev["timeline"])
        except versions.VersionError as e:
            raise RevoiceError(str(e), 400)
        _install(d, bk, prev_n)
        rebuilt = _rebuild(jid, d)
        _update(d, n, status="undone", undone=time.time(), rebuilt=rebuilt)
        return {"status": "undone", "project": out, "changes": changes, "rebuilt": rebuilt,
                "live": prev_n}
