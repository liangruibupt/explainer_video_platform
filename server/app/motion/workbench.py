"""Lifecycle of the shotcraft Motion Workbench dev server (docs/REMOTION_CONTRACT.md §7).

One dev server on 127.0.0.1:PORT, linked to one job's Remotion project. Linking and starting go
through the workbench's own `scripts/open.mjs` (it links `proj` + `public/*`, runs gen-index,
starts vite detached in its own session and records the pid in `.dev.pid`; on a relink it only
restarts a vite whose command line it verified as its own). We add `.evp-motion.json` next to it
with the job id, and only ever stop a pid that `.dev.pid` names AND whose command line is this
workbench's vite on our port.

The workbench dir is shared by every studio on the box (the `proj` link is a singleton), so the
state lives there, not in EVP_DATA.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

TOOLS = Path(os.environ.get("EVP_TOOLS", Path.home() / ".cache" / "explainer-video"))
WB = Path(os.environ.get("EVP_WORKBENCH", TOOLS / "shotcraft" / "workbench"))
PORT = int(os.environ.get("EVP_MOTION_PORT", "5198"))
BASE = "/motion/"
SETUP = Path(__file__).resolve().parents[3] / "infra" / "motion" / "setup.sh"

_lock = threading.RLock()


class MotionError(RuntimeError):
    pass


def _state_file() -> Path:
    return WB / ".evp-motion.json"


def _pid_file() -> Path:
    return WB / ".dev.pid"


def _vite_bin() -> Path:
    return WB / "node_modules" / "vite" / "bin" / "vite.js"


def _node() -> str:
    return os.environ.get("EVP_NODE") or shutil.which("node") or "node"


def _read_pid() -> int | None:
    try:
        n = int(_pid_file().read_text().strip())
        return n if n > 1 else None
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def is_our_vite(pid: int) -> bool:
    """The pid's command line must be this workbench's vite on our port (pids get reused)."""
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    cmd = r.stdout.strip()
    return r.returncode == 0 and str(_vite_bin()) in cmd and f"--port {PORT}" in cmd


def _managed_pid() -> int | None:
    pid = _read_pid()
    if pid and _alive(pid) and is_our_vite(pid):
        return pid
    return None


def _read_state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    tmp = _state_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    os.replace(tmp, _state_file())


def check_ready() -> None:
    """The vendored workbench must have our patches and its node_modules (infra/motion/setup.sh)."""
    if not (WB / "package.json").exists():
        raise MotionError(f"找不到动效工作台：{WB}")
    vite_cfg = (WB / "vite.config.ts").read_text()
    if "WB_BASE" not in vite_cfg or not _vite_bin().exists():
        raise MotionError(f"动效工作台未安装：请先运行 {SETUP}")


def status() -> dict:
    pid = _managed_pid()
    st = _read_state() if pid else {}
    ours = st.get("pid") == pid
    return {"running": bool(pid), "job_id": st.get("job_id") if ours else None,
            "proj": st.get("proj") if ours else None, "port": PORT}


# A production build of the editor UI, served by the studio itself (motion_api.py). The dev
# server ships ~300 unbundled modules (~15 MB); through an SSM port forward that took about a
# minute before anything showed. The build is 2 files (~2 MB JS, ~0.6 MB gzipped) and takes
# ~3 s. The dev server stays up for everything else (public files, /motion/api/export).
DIST = WB / ".evp-dist"


def build(job_id: str, log: Path) -> bool:
    """Build the UI for the linked project into DIST and pre-gzip its assets. False on failure
    (the dev server keeps serving the unbundled UI)."""
    import gzip
    env = {**os.environ, "WB_BASE": BASE}
    with log.open("a") as fh:
        fh.write(f"[evp] vite build for {job_id}\n")
        fh.flush()
        try:
            r = subprocess.run([_node(), str(_vite_bin()), "build", "--outDir", str(DIST),
                                "--emptyOutDir", "--logLevel", "warn"], cwd=WB, env=env,
                               stdout=fh, stderr=subprocess.STDOUT, timeout=300)
        except subprocess.TimeoutExpired:
            fh.write("[evp] vite build timed out; serving the dev UI\n")
            return False
    if r.returncode or not (DIST / "index.html").exists():
        return False
    for f in (DIST / "assets").glob("*"):
        if f.suffix in (".js", ".css"):
            with f.open("rb") as src, gzip.open(f.with_name(f.name + ".gz"), "wb", 6) as dst:
                shutil.copyfileobj(src, dst)
    return True


def rebuild(job_id: str) -> bool:
    """Rebuild the UI after the linked job's scene files changed (version restore, scene edit)."""
    with _lock:
        st = _read_state()
        if st.get("job_id") != job_id:
            return False
        ok = build(job_id, WB / ".evp-open.log")
        _write_state({**st, "built": job_id if ok else None})
        return ok


def built_dist() -> Path | None:
    """DIST when it holds the build of the job the running dev server is linked to."""
    st = _read_state()
    if st.get("built") and st.get("built") == st.get("job_id") and (DIST / "index.html").exists():
        return DIST
    return None


def start(job_id: str, proj: Path, timeout: int = 150) -> dict:
    """Link `proj` (a job's remotion/ dir) and (re)start the dev server. No-op when that job is
    already the one being served."""
    with _lock:
        check_ready()
        cur = status()
        st = _read_state()
        if cur["running"] and cur["job_id"] == job_id and st.get("proj") == str(proj):
            if st.get("built") != job_id and build(job_id, WB / ".evp-open.log"):
                _write_state({**st, "built": job_id})
            return cur
        log = WB / ".evp-open.log"
        env = {**os.environ, "WB_BASE": BASE}
        with log.open("a") as fh:
            fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} open {job_id}\n")
            fh.flush()
            try:
                r = subprocess.run([_node(), str(WB / "scripts" / "open.mjs"), str(proj),
                                    "--no-open", "--port", str(PORT)], cwd=WB, env=env,
                                   stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
            except subprocess.TimeoutExpired as e:
                raise MotionError(f"工作台 {timeout} 秒内没有启动，见 {log}") from e
        if r.returncode:
            tail = log.read_text(errors="replace").strip().splitlines()[-3:]
            raise MotionError("工作台启动失败：" + " / ".join(tail))
        pid = _managed_pid()
        if not pid:
            raise MotionError(f"工作台已启动但无法核实进程号，见 {log}")
        _write_state({"job_id": job_id, "proj": str(proj), "pid": pid, "port": PORT,
                      "started": time.time(),
                      "built": job_id if build(job_id, log) else None})
        return status()


def stop(wait: float = 10.0) -> dict:
    """Stop the dev server, only if it is verifiably ours."""
    with _lock:
        pid = _managed_pid()
        if not pid:
            return {"stopped": False, **status()}
        try:
            os.killpg(pid, signal.SIGTERM)  # vite was started with setsid: pid == its group
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        t0 = time.time()
        while _alive(pid) and time.time() - t0 < wait:
            time.sleep(0.2)
        for f in (_pid_file(), _state_file()):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        return {"stopped": True, **status()}


def render_link(job_id: str, proj: Path) -> str:
    """A per-job link inside the workbench dir, so a version render can bundle that job's
    project (remotion.config.ts honours WB_PROJ) without relinking the live editor. Bare imports
    from the linked sources still resolve to the workbench's node_modules (symlinks: false)."""
    d = WB / ".evp-proj"
    d.mkdir(exist_ok=True)
    target = proj / "src"
    # keyed by job id AND project path: several studios (different EVP_DATA) can hold the same
    # job id, and their renders must not re-point each other's link
    import hashlib
    name = f"{job_id}-{hashlib.sha1(str(target.resolve()).encode()).hexdigest()[:8]}"
    link = d / name
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return f".evp-proj/{name}"
    if link.is_symlink():
        link.unlink()
    link.symlink_to(target, target_is_directory=True)
    return f".evp-proj/{name}"


def repo_public_entries() -> list[tuple[str, Path]]:
    """Workbench-level public entries the editor offers besides the job's own public/ files
    (BGM library, demo textures). cardpreviews is UI only, as in the vite export plugin."""
    out = []
    pub = WB / "public"
    for name in ("bgmlib", "legacy-textures"):
        p = pub / name
        if p.exists():
            out.append((name, p))
    live = pub / "textures" / "live"
    if live.exists():
        out.append(("textures/live", live))
    return out
