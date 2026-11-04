"""Shared helpers for the avatar adapter: weights fetch, audio prep, VRAM sampling, mp4 mux."""
import json, os, subprocess, threading, time
from pathlib import Path

import numpy as np

WEIGHTS = Path(os.environ.get("AVATAR_WEIGHTS_DIR", "/weights"))
INDEX = Path(os.environ.get("AVATAR_INDEX", "/opt/ml/model/index.json"))


def log(*a):
    print(time.strftime("%H:%M:%S"), "[avatar]", *a, flush=True)


def index():
    """{model: {"s3": "s3://.../<sha>/", "exclude": [...]}} written by stage_weights.py."""
    return json.loads(INDEX.read_text())["models"]


_fetch_lock = threading.Lock()


def fetch(model):
    """Copy one model's pinned S3 prefix to local NVMe once; returns the local dir and seconds."""
    with _fetch_lock:
        ent = index()[model]
        dst = WEIGHTS / model
        done = dst / ".evp_complete"
        if done.exists():
            return dst, 0.0
        dst.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        cmd = ["s5cmd", "--numworkers", "64", "cp", "--concurrency", "8"]
        for pat in ent.get("exclude", []):
            cmd += ["--exclude", pat]
        cmd += [ent["s3"].rstrip("/") + "/*", str(dst) + "/"]
        log("fetch", model, " ".join(cmd))
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        dt = time.time() - t0
        done.write_text(str(dt))
        log("fetched", model, f"{dt:.0f} s")
        return dst, dt


def to_wav16k(src, dst):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
                    str(dst)], check=True)
    return os.path.getsize(dst) / 32000.0  # seconds (s16 mono 16 kHz, header ignored)


class VramPeak:
    """Sample device memory in use (nvml, so it includes the CUDA context and cache)."""

    def __init__(self, every=0.5):
        import pynvml
        pynvml.nvmlInit()
        self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.nv = pynvml
        self.every, self.peak, self._stop = every, 0, threading.Event()

    def total_gb(self):
        return self.nv.nvmlDeviceGetMemoryInfo(self.h).total / 2**30

    def __enter__(self):
        self.peak = 0
        self._stop.clear()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self.nv.nvmlDeviceGetMemoryInfo(self.h).used)
            time.sleep(self.every)

    def __exit__(self, *a):
        self._stop.set()
        self.t.join()

    @property
    def peak_gb(self):
        return round(self.peak / 2**30, 1)


def encode(frames, fps, audio, out, seconds=None, crf=18):
    """frames: iterable of HxWx3 uint8 arrays. Muxes the input speech; trims to the audio."""
    frames = iter(frames)
    first = np.asarray(next(frames))
    h, w = first.shape[:2]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-i", str(audio),
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-shortest"]
    if seconds:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-movflags", "+faststart", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    for f in (first, *frames):
        p.stdin.write(np.ascontiguousarray(np.asarray(f, dtype=np.uint8)).tobytes())
        n += 1
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg encode failed")
    return n, (w, h)


def init_dist():
    """Both model codebases expect torch.distributed; a 1-process group on this GPU."""
    import torch, torch.distributed as dist
    if dist.is_initialized():
        return
    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "29511")
    os.environ.setdefault("RANK", "0")
    os.environ.setdefault("LOCAL_RANK", "0")
    os.environ.setdefault("WORLD_SIZE", "1")
    torch.cuda.set_device(0)
    dist.init_process_group("nccl", rank=0, world_size=1)
