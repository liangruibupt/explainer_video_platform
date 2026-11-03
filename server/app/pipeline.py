"""Runs one explainer-video job through the base skill's scripts, step by step."""
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

from . import llm

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "explainer-video-pipeline" / "scripts"
DATA = Path(os.environ.get("EVP_DATA", ROOT / "data")).resolve()
TOOLS = Path(os.environ.get("EVP_TOOLS", Path.home() / ".cache" / "explainer-video"))
PY = os.environ.get("EVP_PYTHON", str(TOOLS / "venv" / "bin" / "python"))
FFMPEG = os.environ.get("FFMPEG", str(TOOLS / "ffmpeg"))
FFPROBE = os.environ.get("FFPROBE", str(TOOLS / "ffprobe"))

STEPS = [
    ("storyboard", "Opus 5.5 写分镜"),
    ("narrate", "Polly 合成旁白"),
    ("scene_code", "Opus 5.5 写绘制代码"),
    ("sfx", "放置音效"),
    ("mix", "混音"),
    ("captions", "Whisper 逐字字幕"),
    ("capture", "逐帧渲染"),
    ("compose", "FFmpeg 合成"),
]
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
_lock = threading.Lock()


def job_dir(job_id: str) -> Path:
    return DATA / "jobs" / job_id


def read_job(job_id: str) -> dict:
    p = job_dir(job_id) / "job.json"
    return json.loads(p.read_text()) if p.exists() else {}


def list_jobs() -> list:
    root = DATA / "jobs"
    if not root.exists():
        return []
    jobs = [read_job(p.name) for p in root.iterdir() if (p / "job.json").exists()]
    return sorted(jobs, key=lambda j: j.get("created", 0), reverse=True)


def _save(job: dict) -> None:
    with _lock:
        p = job_dir(job["id"]) / "job.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1))
        tmp.replace(p)


def create_job(job_id: str, topic: str, language: str, seconds: int, ratio: str,
               captions: bool, music: str, voice: str, brief: str = "",
               scene_context: str = "", background_png: bytes = b"") -> dict:
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)
    if scene_context:
        (d / "scene_context.txt").write_text(scene_context)
    if background_png:
        (d / "background.png").write_bytes(background_png)
    job = {"id": job_id, "topic": topic, "language": language, "seconds": seconds,
           "ratio": ratio, "captions": captions, "music": music, "voice": voice,
           "brief": brief, "has_background": bool(background_png),
           "status": "queued", "created": time.time(), "updated": time.time(),
           "steps": [{"key": k, "label": l, "status": "pending"} for k, l in STEPS],
           "error": None, "video": None, "scenes": []}
    _save(job)
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return job


def _env() -> dict:
    return dict(os.environ, FFMPEG=FFMPEG, FFPROBE=FFPROBE, HF_HOME=str(TOOLS / "hf"))


def _sh(args: list, cwd: Path, log: Path) -> None:
    with log.open("a") as f:
        f.write(f"\n$ {' '.join(map(str, args))}\n")
        f.flush()
        r = subprocess.run([str(a) for a in args], cwd=cwd, env=_env(), stdout=f,
                           stderr=subprocess.STDOUT, timeout=3600)
    if r.returncode != 0:
        tail = log.read_text()[-1500:]
        raise RuntimeError(f"{Path(str(args[1])).name} 失败（退出码 {r.returncode}）\n{tail}")


def _run(job_id: str) -> None:
    job = read_job(job_id)
    d = job_dir(job_id)
    log = d / "pipeline.log"
    w, h = SIZES.get(job["ratio"], SIZES["16:9"])
    job["status"] = "running"
    _save(job)

    def step(key, fn):
        s = next(x for x in job["steps"] if x["key"] == key)
        s["status"], s["started"] = "running", time.time()
        _save(job)
        fn()
        s["status"], s["ended"] = "done", time.time()
        job["updated"] = time.time()
        _save(job)

    def skip(key):
        next(x for x in job["steps"] if x["key"] == key)["status"] = "skipped"
        _save(job)

    try:
        def do_storyboard():
            scenes = llm.storyboard(job["topic"], job["language"], job["seconds"],
                                    brief=job.get("brief", ""))
            (d / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False, indent=1))
            job["scenes"] = scenes
        step("storyboard", do_storyboard)

        step("narrate", lambda: _sh([PY, SCRIPTS / "narrate.py", "scenes.json", ".",
                                     "--tts", "polly", "--voice", job["voice"]], d, log))
        frames = json.loads((d / "durations.json").read_text())
        for s, n in zip(job["scenes"], frames):
            s["frames"] = n
        _save(job)

        def do_scene_code():
            def note(msg):
                with log.open("a") as f:
                    f.write(msg + "\n")
            bg = ""
            if (d / "background.png").exists():
                import base64
                bg = "data:image/png;base64," + base64.b64encode((d / "background.png").read_bytes()).decode()
            ctx = (d / "scene_context.txt").read_text() if (d / "scene_context.txt").exists() else ""
            (d / "index.html").write_text(llm.scene_html(job["scenes"], frames, w, h, log=note,
                                                         context=ctx, bg_data_url=bg))
        step("scene_code", do_scene_code)

        if any(s.get("sfx") for s in job["scenes"]):
            step("sfx", lambda: _sh([PY, SCRIPTS / "sfx.py", "scenes.json", "."], d, log))
        else:
            skip("sfx")
        step("mix", lambda: _sh([PY, SCRIPTS / "mix_bgm.py", ".", "--music", job["music"]], d, log))
        if job["captions"]:
            step("captions", lambda: _sh([PY, SCRIPTS / "captions.py", "scenes.json", ".",
                                          "--size", f"{w}x{h}"], d, log))
        else:
            skip("captions")
        step("capture", lambda: _sh([PY, SCRIPTS / "capture_html.py", "index.html", "silent.mp4",
                                     "--frames", sum(frames), "--size", f"{w}x{h}"], d, log))

        def do_compose():
            args = [FFMPEG, "-y", "-v", "error", "-i", "silent.mp4", "-i", "narration_bgm.wav"]
            if job["captions"]:
                args += ["-vf", "ass=captions.ass", "-c:v", "libx264", "-crf", "18",
                         "-pix_fmt", "yuv420p"]
            else:
                args += ["-c:v", "copy"]
            args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest",
                     "-movflags", "+faststart", "final.mp4"]
            _sh(args, d, log)
            poster_at = f"{max(1.0, frames[0] / 30 * 0.6):.2f}"
            _sh([FFMPEG, "-y", "-v", "error", "-ss", poster_at, "-i", "final.mp4",
                 "-frames:v", "1", "-vf", "scale=640:-2", "poster.jpg"], d, log)
            out = subprocess.check_output([FFPROBE, "-v", "error", "-show_entries",
                                           "format=duration", "-of", "csv=p=0",
                                           str(d / "final.mp4")]).decode().strip()
            job["duration"] = round(float(out), 2)
            job["video"] = "final.mp4"
        step("compose", do_compose)
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 - surface any step failure to the UI
        for s in job["steps"]:
            if s["status"] == "running":
                s["status"] = "failed"
        job["status"], job["error"] = "failed", f"{e}"
        with log.open("a") as f:
            f.write(traceback.format_exc())
    job["updated"] = time.time()
    _save(job)


def delete_job(job_id: str) -> bool:
    d = job_dir(job_id)
    if not (d / "job.json").exists():
        return False
    shutil.rmtree(d)
    return True
