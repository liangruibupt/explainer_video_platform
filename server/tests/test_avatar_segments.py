"""Auto-segmentation of long 数字人 clips (avatar.split_text / split_audio / join /
generate_long, the page job and the avatar.infinitetalk node). GpuEC2 and Polly are stubbed
through the fixtures of test_avatar.py; ffmpeg runs for real on tiny synthetic media."""
import io
import json
import subprocess
import wave
from pathlib import Path

import pytest

from app import avatar, avatar_api, gpu_client, pipeline
from app.workflows.nodes import NODES
from app.workflows.validate import validate
from test_avatar import FakeGpu, client, ff, isolate, media, post_files, wait  # noqa: F401


def tone_wav(spec, rate=16000) -> bytes:
    """16 kHz mono wav from [(seconds, loud?)]: loud = 300 Hz sine, else digital silence."""
    import math
    frames = bytearray()
    for sec, loud in spec:
        for i in range(int(sec * rate)):
            v = int(9000 * math.sin(2 * math.pi * 300 * i / rate)) if loud else 0
            frames += v.to_bytes(2, "little", signed=True)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return b.getvalue()


def clip(path, seconds, color="gray", size="320x240"):
    return ff("-f", "lavfi", "-i", f"color=c={color}:s={size}:r=25:d={seconds}",
              "-pix_fmt", "yuv420p", "-c:v", "libx264", out=path)


class SegGpu(FakeGpu):
    """Answers every job with a silent video as long as the request's audio."""

    def __init__(self, tmp, fail_at=None):
        super().__init__(b"")
        self.tmp, self.fail_at, self.n = Path(tmp), fail_at, 0

    def poll(self, jid):
        k = int(jid[3:])
        if self.fail_at == k:
            return {"ok": False, "error": "CUDA out of memory"}
        return super().poll(jid) | {"_k": k}

    def fetch(self, res, key="video_b64"):
        import base64
        req = self.reqs[res["_k"] - 1]
        wav = base64.b64decode(req["audio_b64"])
        sec = avatar.wav_seconds(wav)
        # the worker's output length differs slightly from the audio (frame rounding)
        p = clip(self.tmp / f"seg{res['_k']}.mp4", round(sec + 0.12, 2),
                 ["red", "green", "blue", "white"][(res["_k"] - 1) % 4])
        return [p.read_bytes()]


@pytest.fixture
def seggpu(monkeypatch, tmp_path):
    g = SegGpu(tmp_path)
    monkeypatch.setitem(gpu_client._gpus, "avatar", g)
    return g


LONG = ("直播间的家人们晚上好！" * 3 + "今天给大家带来的是我们自家超市的爆款组合，有机纯牛奶一箱十二盒。" * 6
        + "库存不多，一号链接已经上好了，喜欢的家人们抓紧时间拍！" * 4)


# ---------------------------------------------------------------- split logic

def test_check_segment_s():
    assert avatar.check_segment_s(None) == 55 and avatar.check_segment_s("") == 55
    assert avatar.check_segment_s("0") == 0 and avatar.check_segment_s(60) == 60
    for bad in (61, 5, "x", -1):
        with pytest.raises(avatar.AvatarError):
            avatar.check_segment_s(bad)


def test_split_text_at_sentence_ends_within_limit():
    chunks = avatar.split_text(LONG, 30)
    assert len(chunks) >= 2 and "".join(chunks) == LONG.strip()
    assert all(avatar.speech_seconds(c) <= 30 for c in chunks)
    assert all(c[-1] in "。！？；" for c in chunks)          # no cut words
    # balanced: no tiny tail chunk
    secs = [avatar.speech_seconds(c) for c in chunks]
    assert min(secs) > 0.5 * max(secs)
    assert avatar.split_text(LONG, 0) == [LONG.strip()]
    assert avatar.split_text("你好，欢迎光临。", 55) == ["你好，欢迎光临。"]


def test_split_text_real_live90_gives_two_segments():
    s = json.loads((Path(avatar.__file__).parents[2] / "infra/avatar/bench/scripts.json")
                   .read_text())["live90"]
    chunks = avatar.split_text(s, 55)
    assert len(chunks) == 2 and "".join(chunks) == s
    assert all(avatar.speech_seconds(c) <= 55 for c in chunks)


def test_split_text_long_sentence_without_ends():
    s = "，".join(["今天的福利特别多大家不要走开"] * 40) + "。"
    chunks = avatar.split_text(s, 20)
    assert "".join(chunks) == s and all(avatar.speech_seconds(c) <= 20 for c in chunks)


def test_cut_points_prefer_silence_near_even_split():
    assert avatar.cut_points(50, 55, []) == []
    assert avatar.cut_points(89.5, 55, [(10, 10.5), (44, 44.4), (70, 70.5)]) == [44.2]
    # silence past the limit is never used; no silence -> even hard cut
    assert avatar.cut_points(100, 55, [(57, 58)]) == [50.0]
    cuts = avatar.cut_points(130, 55, [])
    assert len(cuts) == 2 and all(b - a <= 55 for a, b in zip([0] + cuts, cuts + [130]))


def test_split_audio_cuts_in_silence():
    wav = tone_wav([(20, True), (0.6, False), (10, True), (0.6, False), (24, True)])
    assert avatar.split_audio(wav, 0) == [wav]
    quiet = avatar.silences(wav)
    assert len(quiet) == 2 and abs(quiet[0][0] - 20) < 0.1
    parts = avatar.split_audio(wav, 40)
    secs = [avatar.wav_seconds(p) for p in parts]
    assert len(parts) == 2 and all(s <= 40 for s in secs)
    assert abs(secs[0] - 30.9) < 0.2                      # silence nearest the even split
    assert abs(sum(secs) - avatar.wav_seconds(wav)) < 1e-3  # nothing lost
    assert avatar.concat_wavs(parts) == wav


def test_split_audio_hard_cut_without_silence():
    wav = tone_wav([(50, True)])
    parts = avatar.split_audio(wav, 30)
    assert [round(avatar.wav_seconds(p), 2) for p in parts] == [25.0, 25.0]


def test_segment_count_and_estimate():
    assert avatar.segment_count(90, 55) == 2 and avatar.segment_count(90, 0) == 1
    assert avatar.segment_count(0, 55, LONG) == len(avatar.split_text(LONG, 55))
    e1, e2 = avatar.estimate(90, "", "480", True, 0), avatar.estimate(90, "", "480", True, 55)
    assert (e1["segments"], e2["segments"]) == (1, 2) and e2["render_min"] == e1["render_min"] + 1


# ---------------------------------------------------------------- join

def test_join_duration_matches_audio(tmp_path):
    durs = [12.4, 9.8, 7.3]
    vids = [clip(tmp_path / f"v{k}.mp4", d + delta, c)
            for k, (d, delta, c) in enumerate(zip(durs, (0.1, -0.2, 0.04), ("red", "green", "blue")))]
    wav = tmp_path / "a.wav"
    wav.write_bytes(tone_wav([(sum(durs), True)]))
    out = avatar.join(vids, durs, wav, tmp_path / "out.mp4")
    st = json.loads(subprocess.check_output(
        [pipeline.FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,duration,width",
         "-of", "json", str(out)]))["streams"]
    v = next(s for s in st if s["codec_type"] == "video")
    a = next(s for s in st if s["codec_type"] == "audio")
    assert abs(float(v["duration"]) - sum(durs)) < 0.1
    assert abs(float(a["duration"]) - sum(durs)) < 0.1
    assert v["width"] == 320
    # the seam: segment 2's colour (green) is on screen just after the first seam
    frame = tmp_path / "f.png"
    ff("-ss", str(durs[0] + 0.5), "-i", out, "-frames:v", "1", "-s", "1x1", "-f", "rawvideo",
       "-pix_fmt", "rgb24", out=frame)
    r, g, b = frame.read_bytes()[:3]
    assert g > 100 and r < 60


# ---------------------------------------------------------------- generate_long

def test_generate_long_reuses_done_segments(seggpu, media, tmp_path):
    segs = [tone_wav([(6, True)]), tone_wav([(4.5, True)])]
    img = media["png"].read_bytes()
    seen, meta = [], {}
    seggpu.fail_at = 2
    with pytest.raises(RuntimeError, match="第 2/2 段失败.*out of memory"):
        avatar.generate_long(segs, tmp_path, image=img, prompt="p", seed=5,
                             on_segment=lambda k, n, c: seen.append((k, n, c)))
    assert len(seggpu.reqs) == 2 and seen == [(1, 2, False), (2, 2, False)]
    assert len(list((tmp_path / "segments").glob("01-*.mp4"))) == 1
    seggpu.fail_at, seen[:] = None, []
    mp4 = avatar.generate_long(segs, tmp_path, image=img, prompt="p", seed=5, meta_out=meta,
                               on_segment=lambda k, n, c: seen.append((k, n, c)))
    assert seen == [(1, 2, True), (2, 2, False)] and len(seggpu.reqs) == 3  # seg 1 reused
    assert meta["segments"] == 2 and meta["reused"] == 1 and meta["seams"] == [6.0]
    # every segment: same portrait, seed, prompt, size, model
    reqs = seggpu.reqs
    assert {(r["image_b64"], r["seed"], r["prompt"], r["size"], r["model"]) for r in reqs} \
        .__len__() == 1
    out = tmp_path / "o.mp4"
    out.write_bytes(mp4)
    assert abs(avatar.duration(out) - 10.5) < 0.1
    # a different seed is a different segment (no stale reuse)
    avatar.generate_long(segs, tmp_path, image=img, prompt="p", seed=6)
    assert len(seggpu.reqs) == 5


def test_generate_long_single_and_v2v(seggpu, media, tmp_path):
    mp4 = avatar.generate_long([tone_wav([(3, True)])], tmp_path, image=media["png"].read_bytes())
    assert mp4 and len(seggpu.reqs) == 1
    with pytest.raises(avatar.AvatarError, match="参考视频"):
        avatar.generate_long([tone_wav([(3, True)])] * 2, tmp_path,
                             video=media["mp4"].read_bytes())


# ---------------------------------------------------------------- page job + node

def test_page_job_segments_script(seggpu, isolate, media, monkeypatch):  # noqa: F811
    monkeypatch.setattr(avatar, "speech", lambda text, voice="Zhiyu": tone_wav(
        [(avatar.speech_seconds(text) - 0.7, True)]))
    r = post_files({"portrait": media["png"]}, script=LONG, segment_s="30")
    assert r.status_code == 201, r.text
    s0 = r.json()
    n = len(avatar.split_text(LONG, 30))
    assert s0["segment_s"] == 30 and s0["segments"] == n and s0["estimate"]["segments"] == n
    s = wait(s0["id"], 120)
    assert s["status"] == "done", s
    texts = [x["text"] for x in s["notes"]]
    assert any(f"第 {n}/{n} 段" in t for t in texts) and texts[-1] == f"完成（{n} 段拼接）"
    assert len(seggpu.reqs) == n and s["worker_meta"]["segments"] == n
    d = avatar_api.ROOT / s["id"]
    assert abs(s["video_seconds"] - avatar.duration(d / "audio.wav")) < 0.1
    assert len(list((d / "speech").glob("*.wav"))) == n


def test_page_job_retry_reuses_segments(seggpu, isolate, media, monkeypatch):  # noqa: F811
    monkeypatch.setattr(avatar_api.avatar, "split_audio", avatar.split_audio)
    audio = Path(avatar_api.DATA) / "long.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(tone_wav([(18, True), (0.6, False), (16, True)]))
    seggpu.fail_at = 2
    r = post_files({"portrait": media["png"], "audio": audio}, segment_s="20")
    s = wait(r.json()["id"], 120)
    assert s["status"] == "failed" and "第 2/2 段" in s["error"]
    assert any("按停顿切成 2 段" in x["text"] for x in s["notes"])
    assert client.post(f"/api/avatars/{s['id']}/retry").status_code == 200
    seggpu.fail_at = None
    s = wait(s["id"], 120)
    assert s["status"] == "done" and len(seggpu.reqs) == 3, s
    assert any("复用" in x["text"] for x in s["notes"])
    assert client.post(f"/api/avatars/{s['id']}/retry").status_code == 409
    assert client.delete(f"/api/avatars/{s['id']}").status_code == 200


def test_page_off_and_v2v_not_segmented(seggpu, isolate, media):  # noqa: F811
    r = post_files({"portrait": media["png"]}, script=LONG, segment_s="0")
    assert r.json()["segments"] == 1
    assert wait(r.json()["id"], 120)["status"] == "done" and len(seggpu.reqs) == 1
    r = post_files({"ref_video": media["mp4"]}, script=LONG, segment_s="30")
    assert r.status_code == 201 and r.json()["segment_s"] == 0 and r.json()["segments"] == 1
    r = post_files({"portrait": media["png"]}, script="你好", segment_s="90")
    assert r.status_code == 422 and "segment_s" in r.json()["detail"]
    e = client.get("/api/avatars/estimate", params={"script": LONG, "segment_s": 30}).json()
    assert e["segments"] == len(avatar.split_text(LONG, 30))
    assert client.get("/api/avatars/estimate", params={"segment_s": 3}).status_code == 422
    o = client.get("/api/avatars/options").json()["limits"]
    assert (o["segment_s"], o["segment_max_s"]) == (55, 60)


class Ctx:
    def __init__(self, d):
        self.dir, self.meta, self.notes = d, {}, []

    def note(self, msg):
        self.notes.append(msg)

    def on_wait(self, sec):
        pass


def test_node_segment_param(seggpu, media, tmp_path):
    d = NODES["avatar.infinitetalk"]
    assert d["params"]["segment_s"]["default"] == 55 and d["params"]["segment_s"]["max"] == 60
    audio = tmp_path / "a.wav"
    audio.write_bytes(tone_wav([(18, True), (0.6, False), (16, True)]))
    p = {"model": "", "prompt": "a host", "size": "480", "fast": True, "seed": 3, "segment_s": 20}
    ctx = Ctx(tmp_path)
    out = d["run"]({"audio": str(audio), "portrait": str(media["png"])}, p, ctx)
    assert ctx.meta["segments"] == 2 and len(seggpu.reqs) == 2
    assert any("第 2/2 段" in n for n in ctx.notes)
    assert abs(avatar.duration(out["video"]) - 34.6) < 0.15
    ctx2 = Ctx(tmp_path)
    d["run"]({"audio": str(audio), "portrait": str(media["png"])}, p | {"segment_s": 0}, ctx2)
    assert ctx2.meta["segments"] == 1
    with pytest.raises(ValueError, match="segment_s"):
        d["run"]({"audio": str(audio), "portrait": str(media["png"])}, p | {"segment_s": 7}, ctx)
    wf = {"nodes": [{"id": "n1", "type": "avatar.infinitetalk", "params": {"segment_s": 90}}],
          "edges": []}
    assert any(e["code"] == "bad_param" for e in validate(wf, {"avatar"}))
