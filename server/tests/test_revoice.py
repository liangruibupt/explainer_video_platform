"""Re-voicing takes (docs/REVOICE_CONTRACT.md §2, part A).

Polly and faster-whisper are mocked in most tests (Polly = a deterministic ffmpeg tone whose
length follows the text, Whisper = evenly spaced characters); ffmpeg, narrate.concat, mix_bgm.py
and the media.py builders run for real. test_real_narrate_path calls Polly and faster-whisper
for real on two short scenes. The workbench is mocked (never started, relinked or rebuilt)."""
import hashlib
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main, pipeline
from app.motion import revoice, scene_edit, snapshots, versions, workbench
from app.remotion import media, project

JOB = "20261112-000000-abcdef"
TEXTS = ["开场白。", "第二段，约百分之二十五。", "最后一段收尾。"]
CAP_STYLE = {"size": 4.5, "color": "#ffffff", "highlight": "#ffd700", "outline": "#000000",
             "bottom": 6}


def fake_synth(text, path, *, lex, tts="polly", voice="Zhiyu", lang="cmn-CN", region="us-east-1"):
    """Deterministic stand-in for Polly: 120 ms of tone per character, pitch from the text."""
    f = 200 + int(hashlib.sha1(text.encode()).hexdigest()[:3], 16) % 600
    subprocess.run([pipeline.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=f={f}:d={0.12 * len(text):.3f}:r=24000", "-ac", "1", "-ar", "24000",
                    "-c:a", "libmp3lame", "-b:a", "48k", "-map_metadata", "-1", "-fflags",
                    "+bitexact", "-flags:a", "+bitexact", path], check=True)


class FakeWhisper:
    def transcribe(self, audio, language="zh", word_timestamps=True):
        dur = revoice.script("narrate").ms(str(audio), pipeline.FFPROBE) / 1000
        n = 12
        words = [SimpleNamespace(word="啊", start=round(dur * k / n, 3),
                                 end=round(dur * (k + 1) / n, 3)) for k in range(n)]
        return [SimpleNamespace(words=words)], None


def make_job(root: Path, texts=TEXTS, synth=fake_synth, model=None) -> Path:
    """A done Remotion job built the way the pipeline builds one (narrate, mix, captions,
    media.prepare_media), with the given TTS and Whisper."""
    d = root / "jobs" / JOB
    (d / "audio").mkdir(parents=True)
    nar, cap = revoice.script("narrate"), revoice.script("captions")
    lex = nar.load_lexicon()
    scenes = [{"id": f"sc{i}", "title": f"标题{i}", "narration": t,
               "sfx": [{"at": 10, "kind": "pop"}]} for i, t in enumerate(texts)]
    clips, frames, starts, t = [], [], [], 0
    for i, s in enumerate(scenes):
        p = d / "audio" / f"s{i:02d}.mp3"
        synth(s["narration"], str(p), lex=lex)
        body, n = nar.scene_frames(nar.ms(str(p), pipeline.FFPROBE))
        clips.append(str(p)); starts.append(t + nar.LEAD); t += body; frames.append(n)
    (d / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False))
    (d / "durations.json").write_text(json.dumps(frames))
    nar.concat(clips, starts, t, str(d / "narration.wav"), pipeline.FFMPEG)
    subprocess.run([pipeline.PY, str(media.SCRIPTS / "mix_bgm.py"), str(d), "--music", "synth"],
                   env=pipeline._env(), check=True, capture_output=True)
    words, start = [], 0.0
    for i, (s, n) in enumerate(zip(scenes, frames)):
        words += cap.scene_words(model or FakeWhisper(), clips[i], s, start + 0.45)
        start += n / 30
    (d / "words.json").write_text(json.dumps(words, ensure_ascii=False))
    proj = d / "remotion"
    (proj / "src" / "data").mkdir(parents=True)
    (proj / "src" / "workbench.ts").write_text("// stub\n")
    tl = project.timeline(scenes, frames, 1920, 1080, "#000000", {})
    (proj / "src" / "data" / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False))
    job = {"id": JOB, "status": "done", "renderer": "remotion", "captions": True,
           "music": "synth", "voice": "Zhiyu", "scenes": scenes}
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False))
    media.prepare_media(job, d, proj, lambda m: None)
    return d


def build_project(d: Path) -> dict:
    """What the workbench imports from the manifest (projectImport.buildProjectFromManifest)."""
    data = d / "remotion" / "src" / "data"
    tl = json.loads((data / "timeline.json").read_text())
    caps = json.loads((data / "captions.json").read_text())
    audio = json.loads((data / "audio.json").read_text())
    base = {"inOffset": 0, "speed": 1, "opacity": 1, "scale": 1, "x": 0, "y": 0}
    k = iter(range(10_000))
    clip = lambda **kw: {**base, "id": f"clip_{next(k)}", **kw}
    return {
        "name": tl["scenes"][0]["label"], "fps": 30, "width": 1920, "height": 1080,
        "background": tl["background"], "source": revoice.manifest_key(tl),
        "tracks": [
            {"id": "t_cap", "name": "Captions", "clips": [
                clip(cardId=revoice.CAPTION_CARD, start=c["from"], duration=c["duration"],
                     props={**CAP_STYLE, "text": c["text"], "chars": c["chars"]},
                     label=c["text"][:12]) for c in caps]},
            {"id": "t_shot", "name": "Shots", "clips": [
                clip(cardId=f"proj:shot:scene-{s['id']}", start=s["from"], duration=s["duration"],
                     props={"title": s["label"], "segments": "GPU|0.7\nToken|0.6"},
                     label=s["label"]) for s in tl["scenes"]]},
            {"id": "t_music", "name": "Music", "clips": [
                clip(cardId="audio-clip", start=0, duration=audio["voice"]["duration"],
                     props={"file": audio["voice"]["src"], "volume": 1}, label="配音（已锁定）")]},
            {"id": "t_sfx", "name": "SFX", "clips": [
                clip(cardId="audio-clip", start=x["from"], duration=x["duration"],
                     props={"file": x["src"], "volume": 1}, label=x["label"])
                for x in audio["sfx"]]},
        ]}


def live_bytes(d: Path) -> dict:
    proj = d / "remotion"
    return {rel: (proj / rel).read_bytes() for rel in revoice.LIVE.values()}


@pytest.fixture
def mocks(monkeypatch):
    monkeypatch.setattr(revoice.script("narrate"), "synth", fake_synth)
    monkeypatch.setattr(revoice, "whisper", lambda: FakeWhisper())
    monkeypatch.setattr(workbench, "status", lambda: {"running": False, "job_id": None, "port": 0})
    monkeypatch.setattr(workbench, "_read_state", lambda: {})
    monkeypatch.setattr(workbench, "build", lambda *a, **k: pytest.fail("build must not run"))
    monkeypatch.setattr(workbench, "rebuild", lambda *a, **k: pytest.fail("rebuild must not run"))


@pytest.fixture
def job(tmp_path, monkeypatch, mocks):
    monkeypatch.setattr(pipeline, "DATA", tmp_path)
    revoice._running.pop(JOB, None)
    yield make_job(tmp_path)
    revoice._running.pop(JOB, None)


def run_take(d: Path, narrations: dict, **kw) -> dict:
    rec = revoice.submit(JOB, d, narrations, start=False, **kw)
    revoice.run(JOB, d, rec["n"])
    out = revoice.get(d, rec["n"])
    assert out["status"] == "ready", out.get("error")
    return out


# ---------------------------------------------------------------- scripts stay CLIs

def test_scripts_import_without_running_cli():
    nar, cap = revoice.script("narrate"), revoice.script("captions")
    assert callable(nar.main) and callable(cap.main)
    lex = nar.load_lexicon()
    ssml = nar.to_ssml("调优 TTFT 与 Prefill", lex)
    assert ssml.startswith("<speak>") and 'interpret-as="characters">TTFT' in ssml
    assert nar.scene_frames(9792) == (11092, 333)


def test_take0_reproduces_original_narration(job):
    t0 = revoice.ensure_take0(job)
    take = revoice.load_take(job, 0)
    nar = revoice.script("narrate")
    out = job / "check.wav"
    clips = [str(t0 / "audio" / f"s{i:02d}.mp3") for i in range(len(TEXTS))]
    nar.concat(clips, take["meta"]["offsets_ms"], take["meta"]["total_ms"], str(out), pipeline.FFMPEG)
    assert out.read_bytes() == (job / "narration.wav").read_bytes()
    assert (t0 / "voice_bgm.wav").read_bytes() == (job / "narration_bgm.wav").read_bytes()


# ---------------------------------------------------------------- takes

def test_longer_scene_grows_and_shifts_later_scenes(job):
    old_tl = json.loads((job / "remotion/src/data/timeline.json").read_text())
    old_audio = json.loads((job / "remotion/src/data/audio.json").read_text())
    before = live_bytes(job)
    rec = run_take(job, {"sc1": "第二段，约百分之三十，再多说几个字。"})
    take = revoice.load_take(job, rec["n"])
    s = rec["scenes"][0]
    assert s["id"] == "sc1" and s["delta_frames"] > 0 and s["new_frames"] == s["raw_frames"]
    assert rec["shift_from"] == "sc1" and rec["voice_drift"] is False
    assert rec["drift_check"] == {"scene_id": "sc0", "identical": True}
    assert rec["total_frames_new"] == rec["total_frames_old"] + s["delta_frames"]
    tl = take["timeline"]
    assert tl["revision"] == old_tl["revision"]
    assert tl["scenes"][2]["from"] == old_tl["scenes"][2]["from"] + s["delta_frames"]
    assert tl["scenes"][0] == old_tl["scenes"][0]
    # sfx follow their scene; the voice spans the film
    assert take["audio"]["sfx"][2]["from"] == old_audio["sfx"][2]["from"] + s["delta_frames"]
    assert take["audio"]["sfx"][:2] == old_audio["sfx"][:2]
    assert take["audio"]["voice"]["duration"] == rec["total_frames_new"]
    # captions: the new text in sc1, sc2 shifted, sc0 unchanged
    t0 = revoice.load_take(job, 0)
    assert "约百分之三十" in "".join(c["text"] for c in take["captions"] if c["scene"] == "sc1")
    old0 = [c for c in t0["captions"] if c["scene"] == "sc0"]
    assert [c for c in take["captions"] if c["scene"] == "sc0"] == old0
    old2 = [c for c in t0["captions"] if c["scene"] == "sc2"]
    new2 = [c for c in take["captions"] if c["scene"] == "sc2"]
    assert [c["from"] - o["from"] for c, o in zip(new2, old2)] == [s["delta_frames"]] * len(old2)
    for c in take["captions"]:
        sc = next(x for x in tl["scenes"] if x["id"] == c["scene"])
        assert sc["from"] <= c["from"] < sc["from"] + sc["duration"]
    # voice later in the film moved by exactly the frame delta; one mix over the whole film
    shift_ms = round(s["delta_frames"] * 1000 / 30)
    assert take["meta"]["offsets_ms"][2] == t0["meta"]["offsets_ms"][2] + shift_ms
    assert (take["dir"] / "voice_bgm.wav").stat().st_size > 0
    assert abs(rec["levels"]["music_gap_db"] - rec["levels"]["base_music_gap_db"]) < 1
    assert set(rec["timing"]) == {"polly_s", "whisper_s", "mix_s", "total_s"}
    assert live_bytes(job) == before  # a take never touches the live project by itself


def test_shorter_scene_keeps_its_frames(job):
    rec = run_take(job, {"sc2": "收尾。"})
    s = rec["scenes"][0]
    assert s["raw_frames"] < s["old_frames"] == s["new_frames"] and s["delta_frames"] == 0
    assert rec["shift_from"] is None and rec["total_frames_new"] == rec["total_frames_old"]


def test_drift_guard(job, monkeypatch):
    nar = revoice.script("narrate")

    def drifting(text, path, **kw):
        fake_synth(text + "x", path, **kw)  # Polly now answers differently for the same text
    monkeypatch.setattr(nar, "synth", drifting)
    rec = run_take(job, {"sc1": "第二段，约百分之三十。"})
    assert rec["voice_drift"] is True and rec["drift_check"]["identical"] is False


def test_all_scenes(job):
    rec = run_take(job, {"sc1": "第二段，约百分之三十。"}, all_scenes=True)
    assert [s["id"] for s in rec["scenes"]] == ["sc0", "sc1", "sc2"]
    assert rec["voice_drift"] is False and rec["drift_check"]["identical"] is True


def test_submit_validation_and_one_per_job(job):
    with pytest.raises(revoice.RevoiceError) as e:
        revoice.submit(JOB, job, {"nope": "x"}, start=False)
    assert e.value.status == 400
    with pytest.raises(revoice.RevoiceError) as e:
        revoice.submit(JOB, job, {"sc0": TEXTS[0]}, start=False)
    assert e.value.status == 400 and "没有改动" in str(e.value)
    with pytest.raises(revoice.RevoiceError):
        revoice.submit(JOB, job, {"sc0": "  "}, start=False)
    rec = revoice.submit(JOB, job, {"sc0": "新的开场。"}, start=False)
    with pytest.raises(revoice.RevoiceError) as e:
        revoice.submit(JOB, job, {"sc0": "再新一点。"}, start=False)
    assert e.value.status == 409
    revoice.run(JOB, job, rec["n"])
    assert revoice.get(job, rec["n"])["status"] == "ready"
    assert revoice.submit(JOB, job, {"sc0": "再新一点。"}, start=False)["n"] == rec["n"] + 1


def test_failed_take_is_recorded(job, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("polly down")
    monkeypatch.setattr(revoice.script("narrate"), "synth", boom)
    rec = revoice.submit(JOB, job, {"sc0": "新的开场。"}, start=False)
    revoice.run(JOB, job, rec["n"])
    out = revoice.get(job, rec["n"])
    assert out["status"] == "failed" and "polly down" in out["error"]
    assert JOB not in revoice._running


def test_recover_marks_stale_takes_failed(job, tmp_path):
    rec = revoice.submit(JOB, job, {"sc0": "新的开场。"}, start=False)
    revoice._running.pop(JOB)
    assert revoice.recover(tmp_path) == 1
    assert revoice.get(job, rec["n"])["status"] == "failed"


# ---------------------------------------------------------------- apply / undo

def test_apply_transforms_project_and_undo_restores_bytes(job):
    before = live_bytes(job)
    proj0 = build_project(job)
    # user edits survive: a prop, a caption colour, a moved sfx
    proj0["tracks"][1]["clips"][1]["props"]["segments"] = "GPU|0.7\nToken|0.6\nAgent|0.4"
    for c in proj0["tracks"][0]["clips"]:
        c["props"]["color"] = "#ffeeaa"
    rec = run_take(job, {"sc1": "第二段，约百分之三十，再多说几个字。"})
    delta = rec["scenes"][0]["delta_frames"]
    out = revoice.apply(JOB, job, rec["n"], proj0)
    assert out["status"] == "applied" and out["rebuilt"] is None and out["changes"]
    p = out["project"]
    take = revoice.load_take(job, rec["n"])
    versions.validate(p, take["timeline"])
    assert p["source"] == revoice.manifest_key(take["timeline"])
    assert p["source"].endswith(f"@{rec['total_frames_new']}f#{take['timeline']['revision']}")
    shots = p["tracks"][1]["clips"]
    assert shots[1]["props"]["segments"] == "GPU|0.7\nToken|0.6\nAgent|0.4"
    assert [c["start"] for c in shots] == [s["from"] for s in take["timeline"]["scenes"]]
    assert [c["duration"] for c in shots] == [s["duration"] for s in take["timeline"]["scenes"]]
    caps = p["tracks"][0]["clips"]
    assert [(c["start"], c["props"]["text"]) for c in caps] == [(c["from"], c["text"])
                                                                 for c in take["captions"]]
    assert all(c["props"]["color"] == "#ffeeaa" for c in caps)
    sfx0 = proj0["tracks"][3]["clips"]
    sfx1 = p["tracks"][3]["clips"]
    assert [c["start"] for c in sfx1] == [sfx0[0]["start"], sfx0[1]["start"], sfx0[2]["start"] + delta]
    voice = p["tracks"][2]["clips"][0]
    assert voice["start"] == 0 and voice["duration"] == rec["total_frames_new"]
    # the live project now uses the take; the voice lock locks to it
    assert revoice.live(job) == rec["n"]
    assert live_bytes(job)["public/audio/voice_bgm.wav"] == (take["dir"] / "voice_bgm.wav").read_bytes()
    tl, audio = versions._load_job_data(job / "remotion")
    locked, warns = versions.apply_voice_lock(p, tl, audio)
    assert warns == [] and versions.drift_warnings(locked, tl) == []
    assert revoice.get(job, rec["n"])["live"] is True
    # undo: byte-for-byte, and the project goes back
    back = revoice.undo(JOB, job, rec["n"], p)
    assert back["status"] == "undone" and revoice.live(job) == 0
    assert live_bytes(job) == before
    q = back["project"]
    assert [(c["start"], c["duration"]) for c in q["tracks"][1]["clips"]] == \
        [(c["start"], c["duration"]) for c in proj0["tracks"][1]["clips"]]
    assert [c["start"] for c in q["tracks"][3]["clips"]] == [c["start"] for c in sfx0]
    assert q["source"] == proj0["source"]
    assert [c["props"]["text"] for c in q["tracks"][0]["clips"]] == \
        [c["props"]["text"] for c in proj0["tracks"][0]["clips"]]
    # an undone take can be applied again
    assert revoice.apply(JOB, job, rec["n"], proj0)["status"] == "applied"


def test_apply_guards(job):
    rec1 = run_take(job, {"sc0": "新的开场。"})
    rec2 = run_take(job, {"sc2": "新的收尾，长一点长一点。"})
    proj0 = build_project(job)
    with pytest.raises(revoice.RevoiceError):
        revoice.undo(JOB, job, rec1["n"], proj0)  # not applied
    revoice.apply(JOB, job, rec1["n"], proj0)
    with pytest.raises(revoice.RevoiceError) as e:  # built on take 0, live is now take 1
        revoice.apply(JOB, job, rec2["n"], proj0)
    assert "基于第 0 版" in str(e.value)
    bad = build_project(job)
    bad["fps"] = 25
    rec3 = run_take(job, {"sc2": "基于第一版的收尾。"})
    assert rec3["base"] == rec1["n"]
    with pytest.raises(revoice.RevoiceError) as e:
        revoice.apply(JOB, job, rec3["n"], bad)
    assert e.value.status == 400 and revoice.live(job) == rec1["n"]


def test_rebuild_only_when_linked_here(job, monkeypatch):
    built = []
    monkeypatch.setattr(workbench, "rebuild", lambda jid: built.append(jid) or True)
    monkeypatch.setattr(workbench, "status", lambda: {"running": True, "job_id": JOB, "port": 0,
                                                      "proj": "/elsewhere/remotion"})
    rec = run_take(job, {"sc0": "新的开场。"})
    assert revoice.apply(JOB, job, rec["n"], build_project(job))["rebuilt"] is None and not built
    revoice.undo(JOB, job, rec["n"], build_project(job))
    monkeypatch.setattr(workbench, "status", lambda: {"running": True, "job_id": JOB, "port": 0,
                                                      "proj": str(job / "remotion")})
    assert scene_edit.linked_here(JOB, job)
    assert revoice.apply(JOB, job, rec["n"], build_project(job))["rebuilt"] is True
    assert built == [JOB]


# ---------------------------------------------------------------- versions keep their voice

def test_snapshots_keep_each_versions_voice(job):
    vd = versions.vdir(job)
    vd.mkdir()
    (job / "remotion" / "src" / "scenes").mkdir()
    (job / "remotion" / "src" / "scenes" / "Sc0.tsx").write_text("// scene\n")
    original = live_bytes(job)
    # a version saved before voice snapshots existed (scene snapshot only)
    snapshots.ensure_v1(job)
    snapshots.snapshot(job, 2)
    import shutil
    shutil.rmtree(snapshots.voice_snap_dir(job, 2))
    rec = run_take(job, {"sc1": "第二段，约百分之三十，再多说几个字。"})
    revoice.apply(JOB, job, rec["n"], build_project(job))
    snapshots.snapshot(job, 3)  # saved with the new voice
    new = live_bytes(job)
    assert (snapshots.voice_snap_dir(job, 3) / ".voice/live").read_text() == str(rec["n"])
    assert snapshots.restore(job, 2) is True  # pre-voice version -> original voice
    assert live_bytes(job) == original and revoice.live(job) == 0
    assert snapshots.restore(job, 2) is False
    assert snapshots.restore(job, 3) is True
    assert live_bytes(job) == new and revoice.live(job) == rec["n"]
    assert snapshots.restore(job, 1) is True
    assert live_bytes(job) == original
    keep = sorted(vd.glob(".live-*"))
    assert keep and any((k / "voice" / "public/audio/voice_bgm.wav").is_file() for k in keep)


def test_v1_voice_is_take0_even_after_apply(job):
    rec = run_take(job, {"sc0": "新的开场。"})
    original = live_bytes(job)
    revoice.apply(JOB, job, rec["n"], build_project(job))
    vd = versions.vdir(job)
    vd.mkdir()
    (job / "remotion" / "src" / "scenes").mkdir()
    (job / "remotion" / "src" / "scenes" / "Sc0.tsx").write_text("// scene\n")
    snapshots.ensure_v1(job)
    v1 = snapshots._snap_files(snapshots.voice_snap_dir(job, 1))
    assert v1["public/audio/voice_bgm.wav"] == original["public/audio/voice_bgm.wav"]
    assert v1[".voice/live"] == b"0"


# ---------------------------------------------------------------- HTTP

def test_http_flow(job, monkeypatch):
    c = TestClient(main.app)
    base = f"/api/jobs/{JOB}/revoice"
    assert c.post(base, json={"narrations": {"sc9": "x"}}).status_code == 400
    assert c.post(base, data="nope").status_code == 400
    r = c.post(base, json={"narrations": {"sc1": "第二段，约百分之三十，再多说几个字。"}})
    assert r.status_code == 202, r.text
    n = r.json()["n"]
    t0 = time.time()
    while c.get(f"{base}/{n}").json()["status"] in ("queued", "running"):
        assert time.time() - t0 < 120
        time.sleep(0.3)
    rec = c.get(f"{base}/{n}").json()
    assert rec["status"] == "ready", rec
    assert [x["n"] for x in c.get(base).json()] == [n]
    a = c.get(f"{base}/{n}/audio/sc1.mp3")
    assert a.status_code == 200 and a.headers["content-type"] == "audio/mpeg"
    assert c.get(f"{base}/{n}/audio/nope.mp3").status_code == 404
    assert c.get(f"{base}/99").status_code == 404
    assert c.post(f"{base}/{n}/apply", json={}).status_code == 400
    r = c.post(f"{base}/{n}/apply", json={"project": build_project(job)})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    r = c.post(f"{base}/{n}/undo", json={"project": r.json()["project"]})
    assert r.status_code == 200 and r.json()["status"] == "undone"
    assert c.post(f"{base}/{n}/undo", json={"project": build_project(job)}).status_code == 409


def test_http_needs_done_remotion_job(job):
    p = job / "job.json"
    j = json.loads(p.read_text())
    p.write_text(json.dumps({**j, "renderer": "html"}))
    assert TestClient(main.app).get(f"/api/jobs/{JOB}/revoice").status_code == 409


# ---------------------------------------------------------------- real Polly + Whisper

def _aws_ok() -> bool:
    try:
        import boto3
        return boto3.Session().get_credentials() is not None
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _aws_ok(), reason="needs AWS credentials for Polly")
def test_real_narrate_path(tmp_path, monkeypatch):
    """Real Polly through narrate.py's own synth/SSML, real faster-whisper: two short scenes,
    the second re-voiced. The unchanged first scene is the drift guard."""
    monkeypatch.setattr(pipeline, "DATA", tmp_path)
    monkeypatch.setattr(workbench, "status", lambda: {"running": False, "job_id": None, "port": 0})
    real = revoice.script("narrate").synth
    d = make_job(tmp_path, ["你好，欢迎收看。", "今天讲百分之二十五。"], synth=real,
                 model=revoice.whisper())
    rec = run_take(d, {"sc1": "今天讲百分之三十。"})
    assert rec["voice_drift"] is False and rec["drift_check"] == {"scene_id": "sc0",
                                                                    "identical": True}
    take = revoice.load_take(d, rec["n"])
    text = "".join(c["text"] for c in take["captions"] if c["scene"] == "sc1")
    assert "百分之三十" in text
    s = rec["scenes"][0]
    assert abs(rec["levels"]["scenes"]["sc1"]["new_db"] - rec["levels"]["scenes"]["sc1"]["old_db"]) < 1.5
    assert s["new_frames"] >= s["old_frames"]
