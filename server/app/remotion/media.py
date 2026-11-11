"""Part B of the Remotion renderer (docs/REMOTION_CONTRACT.md §5): voice track, sfx clips and
caption data for a job's Remotion project.

prepare_media(job, d, proj, log) fills
  proj/public/audio/voice_bgm.wav     copy of d/narration_bgm.wav (narration + ducked music, no sfx)
  proj/public/audio/sfx/<kind>.wav    one synthesized clip per kind (sfx.py's ffmpeg sources)
  proj/src/data/audio.json            AudioData
  proj/src/data/captions.json         CaptionLine[] split and timed exactly like captions.py's ASS
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "explainer-video-pipeline" / "scripts"
TOOLS = Path(os.environ.get("EVP_TOOLS", Path.home() / ".cache" / "explainer-video"))
FPS = 30
MAX_CHARS = 16          # captions.py --max-chars default (the pipeline does not pass it)
TAIL = 0.25             # captions.py keeps each line 0.25 s past its last character
BREAK = "，。！？；：,.!?;:"   # captions.py line breaks after these
STRIP = "，。、；：,.;:"       # captions.py strips these from the shown text
SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _ffmpeg() -> str:
    return os.environ.get("FFMPEG", str(TOOLS / "ffmpeg"))


def sfx_table() -> dict:
    """sfx.py's kind -> lavfi source table, imported from the script so both renderers agree."""
    spec = importlib.util.spec_from_file_location("evp_sfx_script", SCRIPTS / "sfx.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SFX


def frame_at(ms: int, fps: int = FPS) -> int:
    """First frame whose timestamp (ffmpeg's ass filter: pts rounded to ms) is >= ms, i.e. the
    first frame on which libass shows an event starting at ms."""
    f = max(0, ms * fps // 1000)
    while round(f * 1000 / fps) < ms:
        f += 1
    while f > 0 and round((f - 1) * 1000 / fps) >= ms:
        f -= 1
    return f


def _lines(words: list) -> list:
    """Re-split words.json into caption lines exactly like captions.py (per scene)."""
    out = []
    for scene, grp in itertools.groupby(words, key=lambda w: w["scene"]):
        cur = []
        for w in grp:
            cur.append(w)
            if len(cur) >= MAX_CHARS or w["text"][-1:] in BREAK:
                out.append((scene, cur))
                cur = []
        if cur:
            out.append((scene, cur))
    return out


def build_captions(words: list, total: int, fps: int = FPS) -> list:
    """CaptionLine[] from words.json. Times follow the ASS file captions.py writes: event
    start/end at centisecond precision, one karaoke syllable per script character (its text
    may carry kept punctuation such as ？ or “), highlight at the syllable start."""
    caps = []
    for scene, ln in _lines(words):
        start_cs = round(ln[0]["start"] * 100)
        end_cs = round((ln[-1]["end"] + TAIL) * 100)
        text, spans, t = "", [], start_cs
        for w, nxt in zip(ln, ln[1:] + [None]):
            dur = (nxt["start"] if nxt else w["end"]) - w["start"]
            k = max(1, round(dur * 100))
            shown = w["text"].rstrip(STRIP)
            text += shown
            spans += [(t, t + k)] * len(shown)
            t += k
        fr = frame_at(start_cs * 10, fps)
        to = frame_at(end_cs * 10, fps)
        caps.append({"id": f"c{len(caps) + 1:03d}", "scene": scene, "from": fr,
                     "duration": max(1, to - fr), "text": text,
                     "chars": [[frame_at(a * 10, fps) - fr, frame_at(b * 10, fps) - fr]
                               for a, b in spans]})
    # libass stacks overlapping events; one line at a time reads better here, so a line ends
    # where the next one starts. Nothing runs past the film.
    for c, nxt in zip(caps, caps[1:] + [None]):
        end = min(c["from"] + c["duration"], total)
        if nxt is not None:
            end = min(end, nxt["from"])
        c["duration"] = max(1, end - c["from"])
        c["chars"] = [[min(a, c["duration"]), min(b, c["duration"])] for a, b in c["chars"]]
    return [c for c in caps if c["from"] < total]


def _wav_frames(p: Path, fps: int = FPS) -> int:
    with wave.open(str(p)) as w:
        return max(1, -(-w.getnframes() * fps // w.getframerate()))


def _render_sfx(src: list, out: Path) -> None:
    """Write one clip the way sfx.py feeds it into the mix (24 kHz mono, then the html mix's
    final -ac 2 upmix, which copies mono to both channels at the same level)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ffmpeg(), "-y", "-v", "error", *src, "-af",
                    "aresample=24000,aformat=channel_layouts=mono", "-ar", "24000", "-ac", "2",
                    "-c:a", "pcm_s16le", str(out)], check=True)


def build_audio(scenes: list, frames: list, d: Path, proj: Path, log: Callable[[str], None],
                fps: int = FPS) -> dict:
    total = sum(frames)
    table = sfx_table()
    sfx_dir = proj / "public" / "audio" / "sfx"
    if sfx_dir.exists():
        shutil.rmtree(sfx_dir)
    made: dict = {}
    clips, start = [], 0
    for s, n in zip(scenes, frames):
        for e in s.get("sfx") or []:
            kind, at = str(e.get("kind", "")), start + int(e.get("at", 0))
            if at >= total:
                continue
            if kind not in made:
                if kind in table:
                    name, src = f"{kind}.wav", ["-f", "lavfi", "-i", table[kind]]
                else:
                    p = Path(kind) if Path(kind).is_absolute() else d / kind
                    if not p.is_file():
                        log(f"remotion media: sfx file not found, skipped: {kind}")
                        made[kind] = None
                        continue
                    name, src = f"file{len(made):02d}_{SAFE.sub('_', p.stem)[:40]}.wav", ["-i", str(p)]
                _render_sfx(src, sfx_dir / name)
                made[kind] = name
            if not made[kind]:
                continue
            dur = min(_wav_frames(sfx_dir / made[kind], fps), total - at)
            clips.append({"id": f"sfx{len(clips) + 1:02d}", "from": at, "duration": dur,
                          "src": f"audio/sfx/{made[kind]}", "volume": 1,
                          "label": kind if kind in table else Path(kind).name})
        start += n
    voice = None
    src = d / "narration_bgm.wav"
    if src.is_file():
        dst = proj / "public" / "audio" / "voice_bgm.wav"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        voice = {"id": "voice", "from": 0, "duration": total, "src": "audio/voice_bgm.wav",
                 "volume": 1}
    else:
        log("remotion media: narration_bgm.wav missing, film has no voice track")
    return {"voice": voice, "sfx": clips}


def prepare_media(job: dict, d: Path, proj: Path, log: Callable[[str], None]) -> None:
    d, proj = Path(d), Path(proj)
    frames = json.loads((d / "durations.json").read_text())
    sp = d / "scenes.json"
    scenes = json.loads(sp.read_text()) if sp.is_file() else job.get("scenes") or []
    data = proj / "src" / "data"
    data.mkdir(parents=True, exist_ok=True)
    audio = build_audio(scenes, frames, d, proj, log)
    caps = []
    if job.get("captions"):
        wp = d / "words.json"
        if wp.is_file():
            caps = build_captions(json.loads(wp.read_text()), sum(frames))
        else:
            log("remotion media: words.json missing, no captions")
    (data / "audio.json").write_text(json.dumps(audio, ensure_ascii=False, indent=1))
    (data / "captions.json").write_text(json.dumps(caps, ensure_ascii=False))
    log(f"remotion media: {len(audio['sfx'])} sfx clips, {len(caps)} caption lines")
