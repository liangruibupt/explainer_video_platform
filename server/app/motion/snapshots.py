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


def snapshot(d: Path, n: int) -> Path:
    """Copy the live scene files into versions/vN.scenes (atomic: temp dir + rename)."""
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
        return dst
    snapshot(d, 1)
    for name, data in _originals(d).items():
        if (dst / name).exists():
            (dst / name).write_bytes(data)
    return dst


def has_snapshot(d: Path, n: int) -> bool:
    return n == 1 or snap_dir(d, n).is_dir()


def restore(d: Path, n: int) -> bool:
    """Make the live scene files equal version N's snapshot. Returns True when files changed
    (the caller must rebuild the editor UI); False when already equal or no snapshot exists.
    The previous live files are kept under versions/.live-<time>/."""
    src = ensure_v1(d) if n == 1 else snap_dir(d, n)
    want = _files(src)
    if not want:
        return False
    live_dir = _scenes(d)
    live = _files(live_dir)
    if all(live.get(k) == v for k, v in want.items()):
        return False
    keep = vdir(d) / f".live-{time.strftime('%Y%m%d-%H%M%S')}-v{n}"
    keep.mkdir(parents=True, exist_ok=True)
    for name, data in live.items():
        (keep / name).write_bytes(data)
    for name, data in want.items():
        tmp = live_dir / f".{name}.restore"
        tmp.write_bytes(data)
        tmp.replace(live_dir / name)
    return True
