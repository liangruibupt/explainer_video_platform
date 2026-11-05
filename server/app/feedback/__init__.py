"""Phase 5: feedback-driven skill improvement (docs/PHASE5_CONTRACTS.md).

This package root holds only the skill version helper, so pipeline.create_job and
workflows/engine.start can import it without pulling in the store or the API.
"""
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = ROOT / "skills" / "explainer-video-pipeline" / "VERSION"
_commit = None
_commit_lock = threading.Lock()


def skill_version() -> str:
    try:
        return VERSION_FILE.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def skill_commit() -> str:
    """Short sha of HEAD of the checkout the server runs from; cached; 'unknown' when it
    is not a git repo or git is missing."""
    global _commit
    with _commit_lock:
        if _commit is None:
            try:
                r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True, timeout=10)
                _commit = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() \
                    else "unknown"
            except (OSError, subprocess.SubprocessError):
                _commit = "unknown"
        return _commit


def skill_info() -> dict:
    return {"skill_version": skill_version(), "skill_commit": skill_commit()}
