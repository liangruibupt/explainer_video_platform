"""renderer="remotion" for explainer jobs (docs/REMOTION_CONTRACT.md). FIXED file.

The pipeline calls only the two functions below. Each one is implemented by a part in its own
module; until that module exists the call raises, so a job fails with a clear message.
  write_project  -> app/remotion/project.py   (part A: shot cards + Opus scene components)
  prepare_media  -> app/remotion/media.py     (part B: voice track, sfx, caption data)
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def write_project(job: dict, d: Path, frames: list, w: int, h: int, *, images: dict,
                  clips: dict, background: Path | None, context: str,
                  log: Callable[[str], None]) -> Path:
    """Create d/'remotion' (template + generated src/scenes + src/data/timeline.json) and set
    job['scenes'][i]['card']. images/clips map scene id -> file Path. Returns the project dir."""
    try:
        from . import project
    except ModuleNotFoundError as e:  # part A not merged yet
        if e.name != f"{__name__}.project":
            raise
        raise RuntimeError(f"remotion renderer: project generator missing ({e})") from e
    return project.write_project(job, d, frames, w, h, images=images, clips=clips,
                                 background=background, context=context, log=log)


def prepare_media(job: dict, d: Path, proj: Path, log: Callable[[str], None]) -> None:
    """Fill proj/public/audio/* and proj/src/data/{captions,audio}.json from the job dir
    (narration_bgm.wav, words.json, scenes.json sfx cues, durations.json)."""
    try:
        from . import media
    except ModuleNotFoundError as e:  # part B not merged yet
        if e.name != f"{__name__}.media":
            raise
        raise RuntimeError(f"remotion renderer: media step missing ({e})") from e
    media.prepare_media(job, d, proj, log)
