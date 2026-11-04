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
MUSIC_LIB = json.loads((ROOT / "music" / "library.json").read_text())


def music_choices() -> dict:
    """synth/none plus every library track whose file is downloaded."""
    out = {"synth": "合成垫乐", "none": "无音乐"}
    for k, v in MUSIC_LIB.items():
        if not k.startswith("_") and (TOOLS / "music" / v["file"]).is_file():
            out[k] = v["label"]
    return out


def _music_arg(key: str) -> list:
    if key in ("synth", "none"):
        return ["--music", key]
    v = MUSIC_LIB[key]
    return ["--music", str(TOOLS / "music" / v["file"]), "--music-volume", str(v.get("volume", 0.72))]

STEPS = [
    ("storyboard", "Opus 5.5 写分镜"),
    ("illustrate", "Qwen-Image 生成插图"),
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
               scene_context: str = "", background_png: bytes = b"",
               illustrations: int = 0) -> dict:
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)
    if scene_context:
        (d / "scene_context.txt").write_text(scene_context)
    if background_png:
        (d / "background.png").write_bytes(background_png)
    job = {"id": job_id, "topic": topic, "language": language, "seconds": seconds,
           "ratio": ratio, "captions": captions, "music": music, "voice": voice,
           "brief": brief, "has_background": bool(background_png),
           "illustrations": illustrations,
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
                                    brief=job.get("brief", ""),
                                    illustrations=job.get("illustrations", 0))
            (d / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False, indent=1))
            job["scenes"] = scenes
        step("storyboard", do_storyboard)

        # Illustrations run in the background while narration is synthesised: the image
        # endpoint may be cold (about 8 minutes), so it is woken as early as possible.
        wanted = [s for s in job["scenes"] if s.get("illustration")]
        ill_thread, ill_err = None, []
        if wanted:
            s_ill = next(x for x in job["steps"] if x["key"] == "illustrate")
            s_ill["status"], s_ill["started"] = "running", time.time()
            _save(job)

            def do_illustrate():
                from . import image
                from concurrent.futures import ThreadPoolExecutor
                (d / "img").mkdir(exist_ok=True)

                def one(s):
                    def waiting(sec):
                        note = f"等待 GPU（{sec // 60} 分钟）" if sec >= 60 else ""
                        if s_ill.get("note") != note:
                            s_ill["note"] = note
                            _save(job)
                    try:
                        png = image.generate(s["illustration"]["prompt"], ratio=job["ratio"]
                                             if s["illustration"]["place"] == "background"
                                             else "1:1", seed=7, on_wait=waiting)[0]
                        (d / "img" / f"{s['id']}.png").write_bytes(png)
                    except Exception as e:  # noqa: BLE001 - a missing picture is not fatal
                        ill_err.append(f"{s['id']}: {e}")
                # The endpoint runs one request at a time; submit all, they queue in order.
                with ThreadPoolExecutor(max_workers=len(wanted)) as ex:
                    list(ex.map(one, wanted))
            ill_thread = threading.Thread(target=do_illustrate, daemon=True)
            ill_thread.start()
        else:
            skip("illustrate")

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
            import base64
            imgs = {}
            if ill_thread:
                ill_thread.join()
                s_ill["status"], s_ill["ended"] = "done", time.time()
                s_ill.pop("note", None)
                for err in ill_err:
                    note(f"illustration skipped: {err}")
                for s in wanted:
                    p = d / "img" / f"{s['id']}.png"
                    if p.exists():
                        imgs[s["id"]] = "data:image/png;base64," + base64.b64encode(
                            p.read_bytes()).decode()
                    else:
                        s.pop("illustration", None)
                if wanted and not imgs:
                    s_ill["note"] = "插图生成失败，已跳过（见日志）"
                elif len(imgs) < len(wanted):
                    s_ill["note"] = f"{len(imgs)}/{len(wanted)} 张成功"
                _save(job)
            bg = ""
            if (d / "background.png").exists():
                bg = "data:image/png;base64," + base64.b64encode((d / "background.png").read_bytes()).decode()
            ctx = (d / "scene_context.txt").read_text() if (d / "scene_context.txt").exists() else ""
            (d / "index.html").write_text(llm.scene_html(job["scenes"], frames, w, h, log=note,
                                                         context=ctx, bg_data_url=bg,
                                                         images=imgs))
        step("scene_code", do_scene_code)

        if any(s.get("sfx") for s in job["scenes"]):
            step("sfx", lambda: _sh([PY, SCRIPTS / "sfx.py", "scenes.json", "."], d, log))
        else:
            skip("sfx")
        step("mix", lambda: _sh([PY, SCRIPTS / "mix_bgm.py", ".", *_music_arg(job["music"])], d, log))
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
