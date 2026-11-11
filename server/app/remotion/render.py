"""Remotion CLI wrappers shared by the parts (docs/REMOTION_CONTRACT.md §6). FIXED file.

All renders go through these functions, so concurrency, browser and timeouts are set in one
place. `proj` is a job's Remotion project dir (jobs/<id>/remotion).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

TOOLS = Path(os.environ.get("EVP_TOOLS", Path.home() / ".cache" / "explainer-video"))
BASE = Path(os.environ.get("EVP_REMOTION_BASE", TOOLS / "remotion-base"))
TEMPLATE = Path(__file__).parent / "template"
CONCURRENCY = int(os.environ.get("EVP_REMOTION_CONCURRENCY", "2"))


def link_base(proj: Path) -> None:
    """Point proj/node_modules at the shared install (one npm install for every job)."""
    nm = proj / "node_modules"
    if nm.is_symlink() or nm.exists():
        return
    nm.symlink_to(BASE / "node_modules", target_is_directory=True)


def copy_template(proj: Path) -> None:
    """Copy the fixed template files into a new project dir (never overwrites src/scenes)."""
    proj.mkdir(parents=True, exist_ok=True)
    for p in TEMPLATE.rglob("*"):
        rel = p.relative_to(TEMPLATE)
        dst = proj / rel
        if p.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif not dst.exists():
            shutil.copy2(p, dst)
    (proj / "public").mkdir(exist_ok=True)
    link_base(proj)


def _bin(name: str) -> str:
    return str(BASE / "node_modules" / ".bin" / name)


def _run(args: list, cwd: Path, log: Path | None, timeout: int, env: dict | None = None) -> None:
    out = log.open("a") if log else subprocess.DEVNULL
    try:
        r = subprocess.run([str(a) for a in args], cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                           timeout=timeout, env=dict(os.environ, **env) if env else None)
    finally:
        if log:
            out.close()
    if r.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} {args[1] if len(args) > 1 else ''} "
                           f"failed (exit {r.returncode}), see log")


def typecheck(proj: Path, log: Path | None = None, timeout: int = 180) -> None:
    _run([_bin("tsc"), "-p", "tsconfig.json"], proj, log, timeout)


def still(proj: Path, frame: int, out: Path, log: Path | None = None, timeout: int = 300) -> None:
    _run([_bin("remotion"), "still", "src/index.ts", "Main", out, f"--frame={frame}",
          "--log=error"], proj, log, timeout)


STILLS_JS = Path(__file__).parent / "stills.cjs"
STILLS_CONCURRENCY = int(os.environ.get("EVP_STILLS_CONCURRENCY", "2"))


def stills(proj: Path, frames: list, outs: list, log: Path | None = None,
           timeout: int = 900) -> dict:
    """Render many stills of one project with ONE bundle and ONE browser (stills.cjs) instead of
    one `remotion still` process per frame. frames[k] -> outs[k] (PNG, same pixels as still()).
    Returns {frame: error text} for frames that failed (empty = all rendered); raises
    RuntimeError when bundling or the browser fails."""
    proj, outs = Path(proj), [Path(o) for o in outs]
    if not frames:
        return {}
    if os.environ.get("EVP_BATCH_STILLS", "1").strip().lower() in ("0", "false", "off", "no"):
        errs = {}
        for f, o in zip(frames, outs):  # the old path: one `remotion still` process per frame
            try:
                o.unlink(missing_ok=True)
                still(proj, int(f), o.resolve(), log)
            except Exception as e:  # noqa: BLE001
                errs[int(f)] = str(e)
        return errs
    for o in outs:
        o.parent.mkdir(parents=True, exist_ok=True)
        o.unlink(missing_ok=True)
    jf = (proj / ".check" / "stills.job.json").resolve()
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps({"proj": str(proj.resolve()), "comp": "Main",
                              "bundle": str((proj / ".check" / "bundle").resolve()),
                              "gl": "angle", "concurrency": STILLS_CONCURRENCY,
                              "frames": [{"frame": int(f), "out": str(o.resolve())}
                                         for f, o in zip(frames, outs)]}))
    r = subprocess.run([os.environ.get("NODE", "node"), str(STILLS_JS), str(BASE / "node_modules"),
                        str(jf)], cwd=proj, capture_output=True, text=True, timeout=timeout)
    if log:
        with log.open("a") as fh:
            fh.write(r.stdout + r.stderr)
    errs = {}
    for ln in r.stdout.splitlines():
        if ln.startswith("fail "):
            k, _, msg = ln[5:].partition(" ")
            errs[int(k)] = msg
    if r.returncode and not errs:
        raise RuntimeError(f"stills.cjs failed (exit {r.returncode}): {(r.stderr or r.stdout)[-600:]}")
    for f, o in zip(frames, outs):
        if int(f) not in errs and not o.exists():
            errs[int(f)] = "no image written"
    return errs


def render(proj: Path, out: Path, log: Path | None = None, *, entry: str = "src/index.ts",
           comp: str = "Main", props: dict | None = None, public_dir: Path | None = None,
           cwd: Path | None = None, env: dict | None = None, timeout: int = 3600) -> None:
    """Render a composition to H.264 + AAC. `cwd`/`entry`/`public_dir`/`env` let part C render
    the workbench's own entry with an edited project as props."""
    out = Path(out)
    raw = out.with_name(out.stem + ".raw" + out.suffix)
    args = [_bin("remotion"), "render", entry, comp, raw, f"--concurrency={CONCURRENCY}",
            "--codec=h264", "--audio-codec=aac", "--log=error"]
    if props is not None:
        pf = out.with_suffix(".props.json")
        pf.write_text(json.dumps(props, ensure_ascii=False))
        args.append(f"--props={pf}")
    if public_dir is not None:
        args.append(f"--public-dir={public_dir}")
    _run(args, cwd or proj, log, timeout, env=env)
    _fix_audio_priming(raw, out, log)


# Remotion 4.0.484 writes its AAC track with the encoder's 2048-sample priming but no edit
# list, so players start the audio 42.7 ms late (measured by cross-correlation on two jobs).
# Drop those samples and re-encode; ffmpeg's muxer writes a proper edit list for its own.
AAC_PRIMING_SAMPLES = 2048
FFMPEG = os.environ.get("FFMPEG", str(TOOLS / "ffmpeg"))


def _fix_audio_priming(raw: Path, out: Path, log: Path | None) -> None:
    has_audio = subprocess.run(
        [os.environ.get("FFPROBE", str(TOOLS / "ffprobe")), "-v", "error", "-select_streams",
         "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(raw)],
        capture_output=True, text=True).stdout.strip()
    if not has_audio:
        raw.replace(out)
        return
    tmp = out.with_name(out.stem + ".fix" + out.suffix)
    _run([FFMPEG, "-y", "-v", "error", "-i", raw, "-map", "0:v", "-map", "0:a", "-c:v", "copy",
          "-af", f"atrim=start_sample={AAC_PRIMING_SAMPLES},asetpts=N/SR/TB",
          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp], raw.parent, log, 600)
    tmp.replace(out)
    raw.unlink(missing_ok=True)
