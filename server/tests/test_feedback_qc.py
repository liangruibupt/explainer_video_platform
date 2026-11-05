"""Phase 5 part A: qc.measure on small synthetic media made with ffmpeg."""
import json
import shutil
import subprocess

import pytest

from app import feedback, pipeline
from app.feedback import qc
from app.pipeline import DATA

FF = qc._ff()
JOB = "20261104-080000-qc0001"
RUN = "20261104-090000-qc0002"


@pytest.fixture(autouse=True)
def clean():
    for sub in ("jobs", "wf_runs", "videos"):
        shutil.rmtree(DATA / sub, ignore_errors=True)
    yield


def ff(*args):
    subprocess.run([FF, "-y", "-v", "error", *map(str, args)], check=True, timeout=120)


def tone(path, freq, amp, seconds, sr=24000):
    ff("-f", "lavfi", "-i", f"aevalsrc={amp}*sin(2*PI*{freq}*t):s={sr}:d={seconds}:c=mono",
       path)


def make_job(seconds_target=4, voice_amp=0.3, music_amp=0.02, captions=None, video=True,
             record=None):
    d = DATA / "jobs" / JOB
    d.mkdir(parents=True)
    tone(d / "narration.wav", 220, voice_amp, 4)
    tone(d / "bgm.wav", 440, music_amp, 4)
    ff("-i", d / "narration.wav", "-i", d / "bgm.wav", "-filter_complex",
       "[0:a][1:a]amix=inputs=2:normalize=0", "-ac", "2", d / "narration_bgm.wav")
    if video:
        ff("-f", "lavfi", "-i", "color=c=black:s=160x90:r=10:d=4", "-i",
           d / "narration_bgm.wav", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-shortest", d / "final.mp4")
    if captions is not None:
        (d / "captions.ass").write_text(captions, encoding="utf-8")
    rec = {"id": JOB, "seconds": seconds_target, "captions": True, "music": "synth",
           "video": "final.mp4" if video else None, "status": "done",
           "scenes": [{"id": "a"}, {"id": "b"}],
           "steps": [{"key": "mix", "status": "done"}, {"key": "compose", "status": "done"}]}
    rec.update(record or {})
    (d / "job.json").write_text(json.dumps(rec))
    return d


ASS_HEAD = ("[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def dlg(s, e, text):
    return f"Dialogue: 0,{s},{e},Cap,,0,0,0,,{text}\n"


def test_loud_music_quiet_voice():
    make_job(voice_amp=0.02, music_amp=0.5)
    m = qc.measure({"kind": "job", "id": JOB})
    assert m["voice_minus_music_db"] < 6
    assert m["music_lufs"] > m["voice_lufs"]


def test_clean_mix_and_duration():
    make_job(seconds_target=2, voice_amp=0.3, music_amp=0.01,
             captions=ASS_HEAD + dlg("0:00:00.00", "0:00:02.00", "{\\k20}今天{\\k30}我们，讲")
             + dlg("0:00:02.00", "0:00:04.00", "西湖"))
    m = qc.measure({"kind": "job", "id": JOB})
    assert m["voice_minus_music_db"] > 20
    assert m["target_s"] == 2 and 3.5 < m["duration_s"] < 4.5
    assert 1.7 < m["duration_ratio"] < 2.3
    assert -40 < m["lufs"] < 0 and m["silence_ratio"] < 0.05
    assert m["caption_count"] == 2 and m["caption_max_cps"] == 2.5  # 5 chars / 2 s
    assert m["caption_overlap_s"] == 0
    assert m["scene_count"] == 2 and m["failed_steps"] == []
    assert "node_errors" not in m


def test_fast_and_overlapping_captions():
    make_job(video=False, captions=ASS_HEAD
             + dlg("0:00:00.00", "0:00:01.00", "这一行字幕在一秒钟内塞进了二十个汉字看不过来啊")
             + dlg("0:00:00.50", "0:00:02.00", "重叠"))
    m = qc.measure({"kind": "job", "id": JOB})
    assert m["caption_max_cps"] >= 20
    assert m["caption_overlap_s"] == pytest.approx(0.5)
    # no final.mp4: duration comes from the mix
    assert 3.5 < m["duration_s"] < 4.5


def test_words_json_fallback_and_missing_files():
    d = DATA / "jobs" / JOB
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({"id": JOB, "seconds": 60, "steps": [
        {"key": "narrate", "status": "failed"}]}))
    words = [{"scene": "a", "text": c, "start": i * 0.1, "end": i * 0.1 + 0.1}
             for i, c in enumerate("快快快快快快快快快快")]
    (d / "words.json").write_text(json.dumps(words))
    (d / "narration.wav").write_bytes(b"not a wav")  # broken file: no key, no raise
    m = qc.measure({"kind": "job", "id": JOB})
    assert m["caption_count"] == 1 and m["caption_max_cps"] == pytest.approx(10, rel=0.01)
    assert m["failed_steps"] == ["narrate"]
    assert "voice_lufs" not in m and "lufs" not in m and "duration_s" not in m


def test_missing_targets_never_raise():
    assert qc.measure({"kind": "job", "id": "nope"}) == {}
    assert qc.measure({"kind": "job", "id": "../../etc"}) == {}
    assert qc.measure({"kind": "workflow", "id": "nope"}) == {}
    assert qc.measure({"kind": "music", "id": "nope"}) == {}
    assert qc.measure({"kind": "zzz", "id": "x"}) == {}
    assert qc.measure(None) == {}


def test_silence_ratio_half_silent(tmp_path):
    p = tmp_path / "a.wav"
    ff("-f", "lavfi", "-i", "aevalsrc=if(lt(t\\,2)\\,0.3*sin(2*PI*220*t)\\,0):s=24000:d=4:c=mono",
       p)
    assert qc.silence_ratio(p) == pytest.approx(0.5, abs=0.05)


def test_music_volume_from_log_and_library():
    d = make_job(voice_amp=0.3, music_amp=0.3)
    base = qc.measure({"kind": "job", "id": JOB})["voice_minus_music_db"]
    (d / "pipeline.log").write_text("$ python mix_bgm.py . --music synth --music-volume 0.1\n")
    quieter = qc.measure({"kind": "job", "id": JOB})["voice_minus_music_db"]
    # 0.72 -> 0.1 is 20*log10(7.2) = 17.1 dB less music
    assert quieter - base == pytest.approx(17.15, abs=0.1)


def test_workflow_run_with_explainer_job_and_errors():
    make_job()
    from app.workflows import engine
    d = engine.run_dir(RUN)
    (d / "outputs").mkdir(parents=True)
    run = {"id": RUN, "status": "failed",
           "workflow": {"nodes": [{"id": "ex", "type": "explainer.video",
                                   "params": {"seconds": 4}},
                                  {"id": "mx", "type": "audio.mix"}], "edges": []},
           "nodes": {"ex": {"status": "done", "type": "explainer.video",
                            "meta": {"job_id": JOB}, "outputs": {}},
                     "mx": {"status": "failed", "type": "audio.mix", "error": "boom"}},
           "works": []}
    (d / "run.json").write_text(json.dumps(run))
    m = qc.measure({"kind": "workflow", "id": RUN})
    assert m["node_errors"] == {"mx": "boom"}
    assert "voice_minus_music_db" in m and m["scene_count"] == 2
    snap = qc.snapshot({"kind": "workflow", "id": RUN})
    assert snap["record"]["id"] == RUN and snap["skill_version"] == feedback.skill_version()
    assert snap["files"]["job_video"] == f"jobs/{JOB}/final.mp4"


def test_video_item_and_snapshot_files():
    d = DATA / "videos" / "v1"
    d.mkdir(parents=True)
    ff("-f", "lavfi", "-i", "color=c=black:s=160x90:r=10:d=3", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", d / "0.mp4")
    (d / "state.json").write_text(json.dumps({"id": "v1", "status": "done", "seconds": 5,
                                              "files": ["0.mp4"]}))
    m = qc.measure({"kind": "video", "id": "v1"})
    assert m["target_s"] == 5 and m["duration_ratio"] == pytest.approx(0.6, abs=0.05)
    assert "lufs" not in m  # silent video: no audio stream
    snap = qc.snapshot({"kind": "video", "id": "v1"})
    assert snap["files"] == {"video": "videos/v1/0.mp4"}


def test_snapshot_job_files():
    make_job(captions=ASS_HEAD + dlg("0:00:00.00", "0:00:01.00", "好"))
    snap = qc.snapshot({"kind": "job", "id": JOB})
    f = snap["files"]
    assert f["video"] == f"jobs/{JOB}/final.mp4" and f["mix"].endswith("narration_bgm.wav")
    assert f["narration"].endswith("narration.wav") and f["captions"].endswith("captions.ass")
    assert snap["qc"]["caption_count"] == 1
    assert pipeline.job_dir(JOB).is_dir()
