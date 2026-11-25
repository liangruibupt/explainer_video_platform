"""Model assets for 3D previs (docs/PREVIS_CONTRACT.md 4.4 / 4.5): Python wrapper of
`node server/app/previs/js/dist/previs.mjs build-asset | normalize-glb | turntable`.

build(spec, project_id=None)                          -> library record (kind "model")
normalize(raw_glb, target, source, name, project_id=None) -> library record

dist/previs.mjs is rebuilt (js/build.sh) when missing or older than any JS source. A failed
spec / GLB check raises BuildError with the CheckIssue list. Nothing runs at import time.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from ..assets import store

JS = Path(__file__).parent / "js"
DIST = JS / "dist" / "previs.mjs"
NODE = os.environ.get("NODE", "node")
TARGET_AXES = ("w", "h", "d", "longest")
_build_lock = threading.Lock()


class BuildError(ValueError):
    """Spec or GLB failed the checks; issues = [CheckIssue dict, ...]."""

    def __init__(self, msg: str, issues: list | None = None):
        super().__init__(msg)
        self.issues = issues or []


def _sources():
    for p in JS.rglob("*"):
        if p.suffix in (".ts", ".tsx") and not p.name.endswith(".test.ts") \
                and "node_modules" not in p.parts and "dist" not in p.parts:
            yield p


def ensure_dist() -> Path:
    """Run js/build.sh when dist/previs.mjs is missing or older than a source file."""
    with _build_lock:
        newest = max((p.stat().st_mtime for p in _sources()), default=0)
        if not DIST.is_file() or DIST.stat().st_mtime < newest:
            r = subprocess.run(["bash", str(JS / "build.sh")], capture_output=True, text=True,
                               timeout=300)
            if r.returncode != 0 or not DIST.is_file():
                raise RuntimeError(f"previs build.sh failed: {(r.stderr or r.stdout)[-1500:]}")
    return DIST


def _cli(args: list, timeout: int = 300) -> subprocess.CompletedProcess:
    r = subprocess.run([NODE, str(ensure_dist()), *[str(a) for a in args]], capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode == 2:
        issues = []
        for line in r.stdout.splitlines():
            try:
                issues.append(json.loads(line))
            except ValueError:
                continue
        raise BuildError("资产校验失败", issues)
    if r.returncode != 0:
        raise RuntimeError(f"previs.mjs {args[0]} failed (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout)[-1500:]}")
    return r


def turntable(glb: Path, meta: Path, out_dir: Path) -> dict:
    """thumb.jpg + turntable.mp4 in out_dir (about 30 s on this box: one bundle + 120 frames)."""
    _cli(["turntable", glb, meta, out_dir], timeout=900)
    return {"thumb.jpg": out_dir / "thumb.jpg", "turntable.mp4": out_dir / "turntable.mp4"}


def _preview(glb: Path, meta: Path, out: Path) -> dict:
    # a failed preview does not lose the model: the asset is stored without thumb / turntable
    try:
        return turntable(glb, meta, out)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        print(f"[previs] turntable failed for {glb}: {e}", flush=True)
        return {}


def build(spec: dict, project_id=None) -> dict:
    """Parametric AssetSpec -> new library asset (file.glb, meta.json, spec.json, thumb.jpg,
    turntable.mp4). Raises BuildError (with issues) when the spec fails the checks."""
    if not isinstance(spec, dict):
        raise BuildError("spec 必须是 JSON 对象", [{"code": "bad_shape_param", "severity": "error",
                                                    "message": "spec 不是 JSON 对象"}])
    with tempfile.TemporaryDirectory(prefix="evp-asset-") as td:
        d = Path(td)
        (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False))
        _cli(["build-asset", d / "spec.json", d / "file.glb", d / "meta.json"])
        files = _preview(d / "file.glb", d / "meta.json", d / "tt")
        files["spec.json"] = d / "spec.json"
        meta = json.loads((d / "meta.json").read_text())
        return store.ingest(d / "file.glb", "file.glb", name=str(spec.get("name") or spec.get("id")),
                            project_id=project_id, model_meta=meta, files=files)


def check_target(target: dict) -> dict:
    if not isinstance(target, dict) or target.get("axis") not in TARGET_AXES:
        raise BuildError("target.axis 只能是 w / h / d / longest")
    try:
        metres = float(target.get("metres"))
    except (TypeError, ValueError):
        metres = 0.0
    if not 0 < metres <= 1000:
        raise BuildError("target.metres 必须在 (0, 1000] 米之间")
    return {"axis": target["axis"], "metres": metres}


def normalize(raw_glb: Path, target: dict, source: dict, name: str, project_id=None,
              max_tris: int = 60000) -> dict:
    """Any GLB (image-to-3D output, upload) -> scaled to target, grounded, centred, simplified
    to max_tris, one part "body" -> new library asset. source is ModelMeta.source."""
    t = check_target(target)
    store.check_glb(Path(raw_glb))
    with tempfile.TemporaryDirectory(prefix="evp-asset-") as td:
        d = Path(td)
        src = d / "raw.glb"
        shutil.copyfile(raw_glb, src)
        _cli(["normalize-glb", src, d / "file.glb", d / "meta.json",
              "--target", f"{t['axis']}:{t['metres']}", "--max-tris", int(max_tris),
              "--source", json.dumps(source or {"type": "upload", "filename": "upload.glb",
                                                "target": t}, ensure_ascii=False)])
        files = _preview(d / "file.glb", d / "meta.json", d / "tt")
        meta = json.loads((d / "meta.json").read_text())
        return store.ingest(d / "file.glb", "file.glb", name=name, project_id=project_id,
                            model_meta=meta, files=files)
