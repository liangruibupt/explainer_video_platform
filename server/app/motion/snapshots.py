"""Per-version scene code, so opening a saved version in the editor shows what that version
rendered (docs/REMOTION_CONTRACT.md §7, "Versions and the editor").

A version's look = its project JSON (vN.project.json) + the scene components in
remotion/src/scenes at save time. Scene rewrites from the Opus panel change those files in
place, so each version keeps a copy:

  versions/vN.scenes/*.tsx|index.ts   snapshot taken when version N is saved
  versions/v1.scenes/                 the original film's scenes, built on first use: the live
                                      files, with any scene rewritten by the Opus panel replaced
                                      by the backup of its EARLIEST applied edit (= the original)
  versions/.live-<time>/              the live files before a restore overwrote them

Versions saved before this existed have no snapshot; opening them keeps the live scene files.

Voice (docs/REVOICE_CONTRACT.md §2 "Versions"): a re-voiced take changes src/data/*.json and
public/audio/voice_bgm.wav, so each version also keeps its voice:

  versions/vN.voice/src/data/*.json, public/audio/voice_bgm.wav, .voice/live
  versions/v1.voice/                  the original voice (take 0 when it exists, else live)

Versions saved before voice snapshots existed were all rendered with the original voice (no
take could be applied yet), so restoring one restores v1's voice; while no take was ever
applied that equals the live voice and nothing changes.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .versions import vdir


def _scenes(d: Path) -> Path:
    return d / "remotion" / "src" / "scenes"


def _files(p: Path) -> dict[str, bytes]:
    if not p.is_dir():
        return {}
    return {f.name: f.read_bytes() for f in sorted(p.iterdir())
            if f.is_file() and f.suffix in (".tsx", ".ts")}


def snap_dir(d: Path, n: int) -> Path:
    return vdir(d) / f"v{n}.scenes"


def voice_snap_dir(d: Path, n: int) -> Path:
    return vdir(d) / f"v{n}.voice"


def _voice_files(d: Path) -> dict[str, bytes]:
    """rel path under remotion/ -> bytes: src/data/*.json, the voice track and the live take
    number (always present; 0 when no take was ever applied)."""
    proj = d / "remotion"
    out: dict[str, bytes] = {}
    data = proj / "src" / "data"
    if data.is_dir():
        for f in sorted(data.glob("*.json")):
            out[f"src/data/{f.name}"] = f.read_bytes()
    wav = proj / "public" / "audio" / "voice_bgm.wav"
    if wav.is_file():
        out["public/audio/voice_bgm.wav"] = wav.read_bytes()
    live = proj / ".voice" / "live"
    out[".voice/live"] = live.read_bytes() if live.is_file() else b"0"
    return out


def _snap_files(p: Path) -> dict[str, bytes]:
    if not p.is_dir():
        return {}
    return {str(f.relative_to(p)): f.read_bytes() for f in sorted(p.rglob("*")) if f.is_file()}


def _write_tree(dst: Path, files: dict[str, bytes]) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for rel, data in files.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_bytes(data)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)


def _original_voice(d: Path) -> dict[str, bytes]:
    """The original film's voice files: take 0 (built before any take is applied) or, when no
    take exists, the live files (then nothing was ever re-voiced)."""
    from .revoice import LIVE, take_dir
    t0 = take_dir(d, 0)
    if not (t0 / "take.json").is_file():
        return _voice_files(d)
    out = {k: v for k, v in _voice_files(d).items() if k.startswith("src/data/")}
    for name, rel in LIVE.items():
        out[rel] = (t0 / name).read_bytes()
    out[".voice/live"] = b"0"
    return out


def ensure_v1_voice(d: Path) -> Path:
    dst = voice_snap_dir(d, 1)
    if not dst.is_dir():
        _write_tree(dst, _original_voice(d))
    return dst


def snapshot(d: Path, n: int) -> Path:
    """Copy the live scene files into versions/vN.scenes and the live voice into
    versions/vN.voice (atomic: temp dir + rename)."""
    _write_tree(voice_snap_dir(d, n), _voice_files(d))
    dst = snap_dir(d, n)
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for name, data in _files(_scenes(d)).items():
        (tmp / name).write_bytes(data)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)
    return dst


def _originals(d: Path) -> dict[str, bytes]:
    """file name -> original bytes, for every scene the Opus panel ever applied an edit to."""
    idx = d / "remotion" / ".assist" / "edits" / "index.json"
    try:
        recs = json.loads(idx.read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(recs, dict):
        recs = recs.get("edits", [])
    from ..remotion import project
    out: dict[str, bytes] = {}
    for r in sorted((r for r in recs if isinstance(r, dict)), key=lambda r: r.get("n", 0)):
        backup = idx.parent / str(r.get("n")) / "backup.tsx"
        sid = r.get("scene_id")
        if not sid or not backup.is_file():
            continue
        name = project.file_stem(sid) + ".tsx"
        out.setdefault(name, backup.read_bytes())
    return out


def ensure_v1(d: Path) -> Path:
    dst = snap_dir(d, 1)
    if dst.is_dir():
        ensure_v1_voice(d)
        return dst
    snapshot(d, 1)
    _write_tree(voice_snap_dir(d, 1), _original_voice(d))
    for name, data in _originals(d).items():
        if (dst / name).exists():
            (dst / name).write_bytes(data)
    return dst


def has_snapshot(d: Path, n: int) -> bool:
    return n == 1 or snap_dir(d, n).is_dir()


def restore(d: Path, n: int) -> bool:
    """Make the live scene files and voice files equal version N's snapshots. Returns True when
    files changed (the caller must rebuild the editor UI); False when already equal or no
    snapshot exists. The previous live files are kept under versions/.live-<time>/."""
    keep = vdir(d) / f".live-{time.strftime('%Y%m%d-%H%M%S')}-v{n}"
    return _restore_scenes(d, n, keep) | _restore_voice(d, n, keep)


def _restore_voice(d: Path, n: int, keep: Path) -> bool:
    """A version without a voice snapshot predates re-voicing: it used the original voice."""
    src = voice_snap_dir(d, n)
    want = _snap_files(src if src.is_dir() else ensure_v1_voice(d))
    if not want:
        return False
    live = _voice_files(d)
    diff = {k: v for k, v in want.items() if live.get(k) != v}
    if not diff:
        return False
    proj = d / "remotion"
    for rel in diff:
        if rel in live:
            (keep / "voice" / rel).parent.mkdir(parents=True, exist_ok=True)
            (keep / "voice" / rel).write_bytes(live[rel])
    for rel, data in diff.items():
        dst = proj / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.restore")
        tmp.write_bytes(data)
        tmp.replace(dst)
    return True


def _restore_scenes(d: Path, n: int, keep: Path) -> bool:
    src = ensure_v1(d) if n == 1 else snap_dir(d, n)
    want = _files(src)
    if not want:
        return False
    live_dir = _scenes(d)
    live = _files(live_dir)
    if all(live.get(k) == v for k, v in want.items()):
        return False
    keep.mkdir(parents=True, exist_ok=True)
    for name, data in live.items():
        (keep / name).write_bytes(data)
    for name, data in want.items():
        tmp = live_dir / f".{name}.restore"
        tmp.write_bytes(data)
        tmp.replace(live_dir / name)
    return True
