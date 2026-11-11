import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.remotion import media

FF = os.environ.get("FFMPEG", str(media.TOOLS / "ffmpeg"))
need_ff = pytest.mark.skipif(not Path(FF).exists() and not shutil.which("ffmpeg"),
                             reason="ffmpeg missing")


def _w(scene, text, s, e):
    return {"scene": scene, "text": text, "start": s, "end": e}


def test_sfx_table_importable_and_unchanged_kinds():
    t = media.sfx_table()
    assert set(t) == {"whoosh", "hiss", "pop", "rise", "thud"}
    assert t["pop"].startswith("aevalsrc=")


def test_frame_at_matches_ass_filter_rounding():
    assert media.frame_at(0) == 0
    assert media.frame_at(450) == 14     # 13*33.33=433 < 450 <= 467
    assert media.frame_at(467) == 14     # round(466.67)=467
    assert media.frame_at(468) == 15
    for ms in range(0, 3000, 7):
        f = media.frame_at(ms)
        assert round(f * 1000 / 30) >= ms and (f == 0 or round((f - 1) * 1000 / 30) < ms)


def test_lines_split_like_captions_py():
    # punctuation break, max 16 chars, and a scene change always breaks
    words = [_w("a", "抬", 0.45, 0.75), _w("a", "头", 0.75, 0.95), _w("a", "看，", 0.95, 1.17)]
    words += [_w("a", c, 1.2 + i * 0.1, 1.3 + i * 0.1) for i, c in enumerate("一二三四五六七八九十甲乙丙丁戊己庚")]
    words += [_w("b", "好", 4.0, 4.2), _w("b", "的？", 4.2, 4.5)]
    caps = media.build_captions(words, 10_000)
    assert [c["text"] for c in caps] == ["抬头看", "一二三四五六七八九十甲乙丙丁戊己", "庚", "好的？"]
    assert [c["scene"] for c in caps] == ["a", "a", "a", "b"]
    for c in caps:
        assert len(c["chars"]) == len(c["text"])


def test_caption_timing_follows_ass_karaoke():
    words = [_w("a", "抬", 0.45, 0.75), _w("a", "头", 0.75, 0.95), _w("a", "看，", 0.95, 1.17)]
    (c,) = media.build_captions(words, 10_000)
    # ASS: Dialogue 0:00:00.45 -> 0:00:01.42, {\k30}抬{\k20}头{\k22}看
    assert c["from"] == 14 and c["from"] + c["duration"] == media.frame_at(1420)
    assert c["chars"] == [[0, 9], [9, 15], [15, 22]]


def test_kept_punctuation_shares_its_syllable():
    words = [_w("a", "叫“", 1.0, 1.2), _w("a", "瑞", 1.2, 1.4), _w("a", "射”", 1.4, 1.6)]
    (c,) = media.build_captions(words, 10_000)
    assert c["text"] == "叫“瑞射”"
    assert c["chars"][0] == c["chars"][1] and c["chars"][3] == c["chars"][4]


def test_overlapping_lines_are_trimmed_and_clamped():
    words = [_w("a", "甲。", 1.0, 2.0), _w("a", "乙", 2.1, 2.5)]
    a, b = media.build_captions(words, 70)
    assert a["from"] + a["duration"] == b["from"]   # 2.25 s tail cut at 2.1 s
    assert b["from"] + b["duration"] == 70          # clamped to the film
    assert all(e <= b["duration"] for _, e in b["chars"])


def _job(tmp_path, captions=True, sfx=None):
    d = tmp_path / "job"
    d.mkdir()
    (d / "durations.json").write_text("[60, 90]")
    scenes = [{"id": "s00", "narration": "抬头看", "sfx": sfx or []}, {"id": "s01", "narration": "好"}]
    (d / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False))
    (d / "words.json").write_text(json.dumps(
        [_w("s00", "抬", 0.45, 0.75), _w("s00", "头", 0.75, 0.95), _w("s00", "看，", 0.95, 1.17),
         _w("s01", "好", 2.5, 2.8)], ensure_ascii=False))
    return d, {"captions": captions, "scenes": scenes}


def _tone(p: Path, secs=0.5, sr=24000):
    with wave.open(str(p), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x10\x00" * int(secs * sr))


@need_ff
def test_prepare_media_writes_audio_and_captions(tmp_path):
    d, job = _job(tmp_path, sfx=[{"at": 12, "kind": "whoosh"}, {"at": 40, "kind": "pop"},
                                 {"at": 50, "kind": "whoosh"}, {"at": 5, "kind": "ding.wav"},
                                 {"at": 1, "kind": "missing.wav"}])
    _tone(d / "narration_bgm.wav", 5.0)
    _tone(d / "ding.wav", 0.5)
    proj = tmp_path / "proj"
    logs = []
    media.prepare_media(job, d, proj, logs.append)
    a = json.loads((proj / "src/data/audio.json").read_text())
    assert a["voice"] == {"id": "voice", "from": 0, "duration": 150, "src": "audio/voice_bgm.wav",
                          "volume": 1}
    assert (proj / "public/audio/voice_bgm.wav").stat().st_size == (d / "narration_bgm.wav").stat().st_size
    kinds = [(x["from"], x["label"], x["src"]) for x in a["sfx"]]
    assert kinds[0] == (12, "whoosh", "audio/sfx/whoosh.wav")
    assert kinds[1] == (40, "pop", "audio/sfx/pop.wav")
    assert kinds[2][2] == "audio/sfx/whoosh.wav"
    assert kinds[3][1] == "ding.wav" and kinds[3][2].startswith("audio/sfx/file")
    assert len(kinds) == 4 and any("missing.wav" in m for m in logs)
    files = sorted(p.name for p in (proj / "public/audio/sfx").iterdir())
    assert "whoosh.wav" in files and "pop.wav" in files and len(files) == 3
    by = {x["label"]: x["duration"] for x in a["sfx"]}
    assert by["whoosh"] == 21 and by["pop"] == 8 and by["ding.wav"] == 15
    with wave.open(str(proj / "public/audio/sfx/pop.wav")) as w:
        assert w.getnchannels() == 2 and w.getframerate() == 24000
    caps = json.loads((proj / "src/data/captions.json").read_text())
    assert [c["text"] for c in caps] == ["抬头看", "好"]
    assert caps[1]["scene"] == "s01"


@need_ff
def test_sfx_clamped_to_film_end(tmp_path):
    d, job = _job(tmp_path, sfx=[{"at": 140, "kind": "rise"}, {"at": 200, "kind": "pop"}])
    proj = tmp_path / "proj"
    media.prepare_media(job, d, proj, lambda m: None)
    a = json.loads((proj / "src/data/audio.json").read_text())
    assert [(x["from"], x["duration"]) for x in a["sfx"]] == [(140, 10)]
    assert a["voice"] is None    # no narration_bgm.wav


def test_captions_off_gives_empty_array(tmp_path):
    d, job = _job(tmp_path, captions=False)
    proj = tmp_path / "proj"
    media.prepare_media(job, d, proj, lambda m: None)
    assert json.loads((proj / "src/data/captions.json").read_text()) == []
    assert json.loads((proj / "src/data/audio.json").read_text())["sfx"] == []


def test_sfx_script_cli_still_runs(tmp_path):
    if not Path(FF).exists():
        pytest.skip("ffmpeg missing")
    d, _ = _job(tmp_path, sfx=[{"at": 3, "kind": "pop"}])
    _tone(d / "narration.wav", 5.0)
    env = dict(os.environ, FFMPEG=FF, FFPROBE=str(Path(FF).with_name("ffprobe")))
    r = subprocess.run(["python3", str(media.SCRIPTS / "sfx.py"), str(d / "scenes.json"), str(d)],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "0.10s pop" in r.stdout and (d / "sfx.wav").exists()
