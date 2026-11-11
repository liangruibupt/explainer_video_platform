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
    """synth/none/ai plus every library track whose file is downloaded."""
    out = {"synth": "合成垫乐", "none": "无音乐", "ai": "AI 生成（古典/氛围…按主题）"}
    for k, v in MUSIC_LIB.items():
        if not k.startswith("_") and (TOOLS / "music" / v["file"]).is_file():
            out[k] = v["label"]
    return out


def _music_arg(key: str) -> list:
    if key in ("synth", "none"):
        return ["--music", key]
    v = MUSIC_LIB[key]
    return ["--music", str(TOOLS / "music" / v["file"]), "--music-volume", str(v.get("volume", 0.72))]


AI_MUSIC_VOLUME = 0.35  # bgm is loudnorm'd to -24 LUFS in mix_bgm.py; 0.35 (-9 dB) puts
# music-only stretches near -35 dB RMS, about 11 dB under the narration (-24 dB), and the
# sidechain ducks it further while someone speaks. 0.8 measured -26.4 dB: too close to voice.
INSTRUMENTAL_LYRICS = "[Intro]\n\n[Instrumental]\n\n[Outro]"
_whisper = None


def transcript_words(path: Path) -> str:
    """faster-whisper (small, CPU) transcript of an audio file: a vocals proxy, as in
    infra/music/check_audio.py. Empty for instrumental music. A lone 'You' is a common
    false hit on instrumentals and is ignored."""
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel("small", device="cpu", compute_type="int8",
                                download_root=str(TOOLS / "hf" / "hub"))
    segs, _ = _whisper.transcribe(str(path), vad_filter=True)
    text = " ".join(s.text.strip() for s in segs).strip()
    return "" if text.strip(" .!?").lower() in ("", "you") else text

STEPS = [
    ("storyboard", "Opus 5.5 写分镜"),
    ("illustrate", "Qwen-Image 生成插图"),
    ("clips", "MiniMax H3 生成视频镜头"),
    ("music_ai", "YuE2 生成背景音乐"),
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
               illustrations: int = 0, clips: int = 0, renderer: str = "html") -> dict:
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)
    if scene_context:
        (d / "scene_context.txt").write_text(scene_context)
    if background_png:
        (d / "background.png").write_bytes(background_png)
    job = {"id": job_id, "topic": topic, "language": language, "seconds": seconds,
           "ratio": ratio, "captions": captions, "music": music, "voice": voice,
           "brief": brief, "has_background": bool(background_png),
           "illustrations": illustrations, "clips": clips, "renderer": renderer,
           "status": "queued", "created": time.time(), "updated": time.time(),
           "steps": [{"key": k, "label": l, "status": "pending"} for k, l in STEPS],
           "error": None, "video": None, "scenes": []}
    from .feedback import skill_info  # phase 5: skill_version + skill_commit
    job.update(skill_info())
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


def _fallback_music(job: dict) -> str:
    """Library track when AI music fails: guqin for Chinese classical topics, else synth."""
    style = (job.get("music_style") or "").lower()
    classical = any(k in style for k in ("chinese", "guqin", "xiao", "pipa", "erhu", "guzheng"))
    return "guqin" if classical and "guqin" in music_choices() else "synth"


def _prep_clip(raw: Path, out_mp4: Path, frames_dir: Path, w: int, h: int, n: int,
               log: Path) -> int:
    """Turn a 5 s generated clip into a silent W x H background exactly n frames (30 fps)
    long: cover-scale + crop, drop audio, ping-pong (forward then reversed) as often as
    needed. Writes out_mp4 and frames_dir/00001.jpg ...; returns the frame count."""
    pp = out_mp4.with_name(out_mp4.stem + "_pp.mp4")
    vf = (f"[0:v]fps=30,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          "setsar=1,split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0[v]")
    _sh([FFMPEG, "-y", "-v", "error", "-i", raw, "-filter_complex", vf, "-map", "[v]", "-an",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", pp], out_mp4.parent, log)
    _sh([FFMPEG, "-y", "-v", "error", "-stream_loop", "-1", "-i", pp, "-frames:v", n, "-an",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", out_mp4], out_mp4.parent, log)
    pp.unlink(missing_ok=True)
    frames_dir.mkdir(exist_ok=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()
    _sh([FFMPEG, "-y", "-v", "error", "-i", out_mp4, "-q:v", "3", "-frames:v", n,
         frames_dir / "%05d.jpg"], out_mp4.parent, log)
    return len(list(frames_dir.glob("*.jpg")))


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
            meta = {}
            scenes = llm.storyboard(job["topic"], job["language"], job["seconds"],
                                    brief=job.get("brief", ""),
                                    illustrations=job.get("illustrations", 0),
                                    clips=job.get("clips", 0),
                                    music_ai=job["music"] == "ai", meta=meta)
            (d / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False, indent=1))
            job["scenes"] = scenes
            if job["music"] == "ai":
                job["music_style"] = meta.get("music_style") or (
                    "calm cinematic underscore, soft piano and strings, slow, " + llm.MUSIC_TAIL)
        step("storyboard", do_storyboard)

        def waiter(s_step):
            def waiting(sec):
                note = f"等待 GPU（{sec // 60} 分钟）" if sec >= 60 else ""
                if s_step.get("note") != note:
                    s_step["note"] = note
                    _save(job)
            return waiting

        # AI video clips and AI music also start right after the storyboard, so every GPU
        # module is woken at the same time (video cold start ~12.5 min, music ~9 min).
        clip_wanted = [s for s in job["scenes"] if s.get("clip")]
        clip_thread, clip_err = None, []
        if clip_wanted:
            s_clip = next(x for x in job["steps"] if x["key"] == "clips")
            s_clip["status"], s_clip["started"] = "running", time.time()
            _save(job)

            def do_clips():
                from . import video
                from concurrent.futures import ThreadPoolExecutor

                def one(s):
                    try:
                        mp4 = video.generate(s["clip"]["prompt"], aspect=job["ratio"],
                                             short_edge=768, duration=5.0, seed=7,
                                             on_wait=waiter(s_clip))
                        (d / f"clip_{s['id']}_raw.mp4").write_bytes(mp4)
                    except Exception as e:  # noqa: BLE001 - a missing clip is not fatal
                        clip_err.append(f"{s['id']}: {e}")
                with ThreadPoolExecutor(max_workers=len(clip_wanted)) as ex:
                    list(ex.map(one, clip_wanted))
            clip_thread = threading.Thread(target=do_clips, daemon=True)
            clip_thread.start()
        else:
            skip("clips")

        music_thread = None
        if job["music"] == "ai":
            s_mus = next(x for x in job["steps"] if x["key"] == "music_ai")
            s_mus["status"], s_mus["started"] = "running", time.time()
            _save(job)

            def do_music_ai():
                from . import music
                style = job["music_style"]
                tries = [("full", 831001), ("off", 831002)]
                for k, (cot, seed) in enumerate(tries):
                    try:
                        r = music.generate(style, INSTRUMENTAL_LYRICS, cot=cot, seed=seed,
                                           on_wait=waiter(s_mus))
                        p = d / f"music_ai_{k}.{r.get('format') or 'flac'}"
                        p.write_bytes(r["flac"])
                        words = transcript_words(p)
                        with log.open("a") as f:
                            f.write(f"music_ai try {k + 1} (cot={cot}, seed={seed}): "
                                    f"{r.get('seconds')} s, transcript {words!r}\n")
                        if not words:
                            job["music_file"] = p.name
                            job["music_source"] = f"YuE2 AI 生成（第 {k + 1} 次，cot={cot}）"
                            s_mus["note"] = f"{round(r.get('seconds') or 0)} 秒纯音乐，无人声"
                            s_mus["status"], s_mus["ended"] = "done", time.time()
                            _save(job)
                            return
                        s_mus["note"] = "检测到人声，换 seed 重试"
                        _save(job)
                    except Exception as e:  # noqa: BLE001 - falls back to a library track
                        with log.open("a") as f:
                            f.write(f"music_ai try {k + 1} failed: {e}\n")
                fb = _fallback_music(job)
                job["music_fallback"] = fb
                job["music_source"] = f"AI 音乐失败或有人声，改用{music_choices().get(fb, fb)}"
                s_mus["note"] = job["music_source"]
                s_mus["status"], s_mus["ended"] = "done", time.time()
                _save(job)
            music_thread = threading.Thread(target=do_music_ai, daemon=True)
            music_thread.start()
        else:
            skip("music_ai")

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
            clips = {}
            if clip_thread:
                clip_thread.join()
                for err in clip_err:
                    note(f"clip skipped: {err}")
                for s in clip_wanted:
                    raw = d / f"clip_{s['id']}_raw.mp4"
                    n = s.get("frames") or 150
                    try:
                        if not raw.exists():
                            raise RuntimeError("no clip")
                        clips[s["id"]] = _prep_clip(raw, d / f"clip_{s['id']}.mp4",
                                                    d / f"clip_{s['id']}", w, h, n, log)
                    except Exception as e:  # noqa: BLE001 - drop this scene's clip only
                        if raw.exists():
                            note(f"clip prep failed for {s['id']}: {e}")
                        s.pop("clip", None)
                s_clip["status"], s_clip["ended"] = "done", time.time()
                s_clip.pop("note", None)
                if not clips:
                    s_clip["note"] = "视频镜头生成失败，已跳过（见日志）"
                elif len(clips) < len(clip_wanted):
                    s_clip["note"] = f"{len(clips)}/{len(clip_wanted)} 段成功"
                _save(job)
            if (d / "background.png").exists():
                bg = "data:image/png;base64," + base64.b64encode((d / "background.png").read_bytes()).decode()
            ctx = (d / "scene_context.txt").read_text() if (d / "scene_context.txt").exists() else ""
            if job.get("renderer") == "remotion":
                from . import remotion
                remotion.write_project(
                    job, d, frames, w, h,
                    images={k: d / "img" / f"{k}.png" for k in imgs},
                    clips={k: d / f"clip_{k}.mp4" for k in clips},
                    background=(d / "background.png") if (d / "background.png").exists() else None,
                    context=ctx, log=note)
                _save(job)
                return
            (d / "index.html").write_text(llm.scene_html(job["scenes"], frames, w, h, log=note,
                                                         context=ctx, bg_data_url=bg,
                                                         images=imgs, clips=clips))
        step("scene_code", do_scene_code)

        remotion_job = job.get("renderer") == "remotion"
        # Remotion jobs place SFX as editable audio clips inside the project (part B), so the
        # voice track (narration + ducked music) is mixed without them.
        if any(s.get("sfx") for s in job["scenes"]) and not remotion_job:
            step("sfx", lambda: _sh([PY, SCRIPTS / "sfx.py", "scenes.json", "."], d, log))
        else:
            skip("sfx")
        def do_mix():
            if music_thread:
                music_thread.join()
            if job["music"] == "ai" and job.get("music_file"):
                arg = ["--music", d / job["music_file"], "--music-volume", str(AI_MUSIC_VOLUME)]
            elif job["music"] == "ai":
                arg = _music_arg(job.get("music_fallback") or "synth")
            else:
                arg = _music_arg(job["music"])
            _sh([PY, SCRIPTS / "mix_bgm.py", ".", *arg], d, log)
            lines = [x for x in log.read_text().splitlines() if x.startswith("mix ")]
            if lines:
                next(x for x in job["steps"] if x["key"] == "mix")["note"] = lines[-1][4:]
        step("mix", do_mix)
        if job["captions"]:
            step("captions", lambda: _sh([PY, SCRIPTS / "captions.py", "scenes.json", ".",
                                          "--size", f"{w}x{h}"], d, log))
        else:
            skip("captions")
        if remotion_job:
            def do_render():
                from . import remotion
                from .remotion import render as rr
                proj = d / "remotion"
                def note(msg):
                    with log.open("a") as f:
                        f.write(msg + "\n")
                remotion.prepare_media(job, d, proj, note)
                rr.render(proj, d / "final.mp4", log)
            step("capture", do_render)
        else:
            step("capture", lambda: _sh([PY, SCRIPTS / "capture_html.py", "index.html", "silent.mp4",
                                         "--frames", sum(frames), "--size", f"{w}x{h}"], d, log))

        def do_compose():
            # Remotion jobs: final.mp4 already carries voice, sfx and captions (do_render).
            if not remotion_job:
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
