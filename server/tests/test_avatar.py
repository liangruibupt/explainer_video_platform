"""数字人 module (docs/AVATAR_CONTRACT.md): avatar.py, avatar_api.py, avatar_node.py, the
wf_product_pitch template and the task-center source. GpuEC2 and Polly are stubbed; boto3
is patched to fail so no test can reach AWS."""
import base64
import copy
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import avatar, avatar_api, avatar_node, gpu_client, images, pipeline
from app.main import app
from app.tasks import collect
from app.workflows import store as wf_store
from app.workflows.nodes import NODES
from app.workflows.validate import check_types, validate

client = TestClient(app)
MODS = {"image", "video", "music", "avatar"}
REAL_SPEECH = avatar.speech


def ff(*args, out):
    subprocess.run([pipeline.FFMPEG, "-v", "error", "-y", *map(str, args), str(out)],
                   check=True, timeout=120)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("avmedia")
    return {
        "png": ff("-f", "lavfi", "-i", "color=c=gray:s=512x768", "-frames:v", "1", out=d / "p.png"),
        "jpg": ff("-f", "lavfi", "-i", "color=c=blue:s=600x600", "-frames:v", "1", out=d / "p.jpg"),
        "tiny": ff("-f", "lavfi", "-i", "color=c=red:s=100x100", "-frames:v", "1", out=d / "t.png"),
        "mp3": ff("-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-ar", "44100",
                  out=d / "a.mp3"),
        "short": ff("-f", "lavfi", "-i", "sine=duration=0.3", out=d / "s.wav"),
        "mp4": ff("-f", "lavfi", "-i", "testsrc=s=480x640:r=25:d=3", "-f", "lavfi", "-i",
                  "sine=duration=3", "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac",
                  out=d / "v.mp4"),
    }


class FakeGpu:
    """GpuEC2 stand-in: records the request, answers like the worker contract."""

    def __init__(self, mp4: bytes, fail=None):
        self.mp4, self.fail, self.reqs = mp4, fail, []

    def submit(self, req, ensure=True):
        self.reqs.append(req)
        return f"job{len(self.reqs)}"

    def poll(self, jid):
        if self.fail:
            return {"ok": False, "error": self.fail}
        return {"ok": True, "files": {"video_b64": [f"jobs/avatar-ec2/out/{jid}/video.mp4"]},
                "meta": {"model": self.reqs[-1]["model"], "seconds": 3.0, "render_s": 12,
                         "size": self.reqs[-1]["size"], "fast": self.reqs[-1]["fast"]}}

    def fetch(self, res, key="video_b64"):
        assert key == "video_b64"
        return [self.mp4]

    def status(self):
        return {"state": "none"}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path, media):
    import boto3

    def no_aws(*a, **k):
        raise RuntimeError("boto3 disabled in tests")
    monkeypatch.setattr(boto3, "client", no_aws)
    cfg = tmp_path / "avatar.json"
    cfg.write_text(json.dumps({"module": "avatar", "default_model": "infinitetalk"}))
    monkeypatch.setattr(avatar, "config_path", lambda: cfg)
    fake = FakeGpu(media["mp4"].read_bytes())
    monkeypatch.setitem(gpu_client._gpus, "avatar", fake)
    calls = []

    def fake_speech(script, voice="Zhiyu"):  # Polly stub: a real wav of about 2 s
        avatar.check_script(script)
        calls.append((script, voice))
        return media["mp3"].read_bytes()
    monkeypatch.setattr(avatar, "speech", fake_speech)
    shutil.rmtree(avatar_api.ROOT, ignore_errors=True)
    yield {"gpu": fake, "speech": calls, "cfg": cfg}
    shutil.rmtree(avatar_api.ROOT, ignore_errors=True)


def wait(item_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/avatars/{item_id}").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.2)
    raise AssertionError("avatar job stuck")


def post_files(files, **fields):
    opened = {k: (Path(v).name, open(v, "rb")) for k, v in files.items()}
    try:
        return client.post("/api/avatars", files=opened, data={k: str(v) for k, v in fields.items()})
    finally:
        for _, fh in opened.values():
            fh.close()


# ---------------------------------------------------------------- avatar.py

def test_status_without_config_is_not_deployed(monkeypatch, tmp_path):
    monkeypatch.setattr(avatar, "config_path", lambda: tmp_path / "missing.json")
    s = avatar.status()
    assert s["deployed"] is False and s["label"] == "未部署" and s["state"] == "unavailable"
    with pytest.raises(RuntimeError, match="未部署"):
        avatar.generate(b"x", image=b"y")
    assert client.get("/api/avatar/status").json()["deployed"] is False


def test_status_with_config_uses_gpu():
    s = avatar.status()
    assert s["deployed"] is True and s["state"] == "idle" and s["default_model"] == "infinitetalk"


def test_audio_converted_to_16k_mono(media):
    wav, sec = avatar.to_wav16k(media["mp3"].read_bytes())
    assert wav[:4] == b"RIFF" and 2.8 <= sec <= 3.2
    p = Path(avatar_api.DATA) / "probe.wav"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(wav)
    out = subprocess.check_output([pipeline.FFPROBE, "-v", "error", "-show_entries",
                                   "stream=sample_rate,channels", "-of", "json", str(p)])
    st = json.loads(out)["streams"][0]
    assert st["sample_rate"] == "16000" and st["channels"] == 1
    with pytest.raises(avatar.AvatarError, match="太短"):
        avatar.to_wav16k(media["short"].read_bytes())
    with pytest.raises(avatar.AvatarError, match="无法解析"):
        avatar.to_wav16k(b"not audio at all" * 10)


def test_audio_longer_than_limit_rejected(monkeypatch, media):
    monkeypatch.setattr(avatar, "AUDIO_MAX_S", 2)
    with pytest.raises(avatar.AvatarError, match="超过 2 秒"):
        avatar.to_wav16k(media["mp3"].read_bytes())


def test_portrait_checks(media):
    png = avatar.portrait_png(media["jpg"].read_bytes())
    assert png.startswith(b"\x89PNG")
    with pytest.raises(avatar.AvatarError, match="太小"):
        avatar.portrait_png(media["tiny"].read_bytes())
    with pytest.raises(avatar.AvatarError, match="无法解析"):
        avatar.portrait_png(b"GIF89a...")


def test_generate_builds_contract_request(isolate, media):
    meta = {}
    mp4 = avatar.generate(media["mp3"].read_bytes(), image=media["png"].read_bytes(),
                          prompt="  a host  ", model="longcat", size="720", fast=False, seed=7,
                          meta_out=meta)
    assert mp4 == media["mp4"].read_bytes()
    req = isolate["gpu"].reqs[-1]
    assert set(req) == {"model", "audio_b64", "image_b64", "prompt", "size", "fast", "seed"}
    assert (req["model"], req["size"], req["fast"], req["seed"], req["prompt"]) == \
        ("longcat", "720", False, 7, "a host")
    assert base64.b64decode(req["audio_b64"])[:4] == b"RIFF"
    assert base64.b64decode(req["image_b64"]).startswith(b"\x89PNG")
    assert meta["render_s"] == 12
    avatar.generate(media["mp3"].read_bytes(), image=media["png"].read_bytes())
    assert isolate["gpu"].reqs[-1]["model"] == "infinitetalk"  # "" -> config default
    with pytest.raises(avatar.AvatarError):
        avatar.generate(media["mp3"].read_bytes(), image=media["png"].read_bytes(), model="x")
    with pytest.raises(avatar.AvatarError, match="二选一"):
        avatar.generate(media["mp3"].read_bytes())


def test_generate_with_reference_video(isolate, media):
    avatar.generate(media["mp3"].read_bytes(), video=media["mp4"].read_bytes())
    req = isolate["gpu"].reqs[-1]
    assert "video_b64" in req and "image_b64" not in req


def test_worker_failure_raises(isolate, media):
    isolate["gpu"].fail = "CUDA out of memory"
    with pytest.raises(RuntimeError, match="out of memory"):
        avatar.generate(media["mp3"].read_bytes(), image=media["png"].read_bytes())


def test_estimate_and_config_override(isolate):
    e = avatar.estimate(30, "", "480", True)
    assert e["model"] == "infinitetalk" and e["render_min"] >= 1 and e["usd_cold"] > e["usd_warm"]
    isolate["cfg"].write_text(json.dumps({"module": "avatar", "default_model": "longcat",
                                          "usd_per_hour": 2.0, "cold_min": 9,
                                          "render_s_per_s": {"longcat": {"480_fast": 60}}}))
    e = avatar.estimate(60, "", "480", True)
    assert (e["model"], e["render_min"], e["cold_min"], e["usd_per_hour"]) == ("longcat", 61, 9, 2)


def test_speech_goes_through_narrate(monkeypatch, tmp_path):
    """speech() runs narrate.py (polly, voice, lang) and returns its narration.wav."""
    monkeypatch.setattr(avatar, "speech", REAL_SPEECH)  # the real one; narrate.py is faked
    fake = tmp_path / "scripts"
    fake.mkdir()
    (fake / "narrate.py").write_text(
        "import json, subprocess, sys, os\n"
        "json.dump(sys.argv[1:], open('argv.json', 'w'))\n"
        "sc = json.load(open(sys.argv[1]))\n"
        "assert sc == [{'id': 'pitch', 'narration': sc[0]['narration']}]\n"
        "open(os.environ['AV_SEEN'], 'w').write(json.dumps({'argv': sys.argv[1:], 'text': sc[0]['narration']}))\n"
        "subprocess.run([os.environ['FFMPEG'], '-v', 'error', '-y', '-f', 'lavfi', '-i',\n"
        "                'sine=duration=1.5', '-ar', '24000', '-ac', '1', 'narration.wav'], check=True)\n")
    seen = tmp_path / "seen.json"
    monkeypatch.setenv("AV_SEEN", str(seen))
    monkeypatch.setattr(pipeline, "SCRIPTS", fake)
    wav = avatar.speech("大家好，欢迎来到直播间。", "Zhiyu")
    assert wav[:4] == b"RIFF"
    got = json.loads(seen.read_text())
    assert got["text"] == "大家好，欢迎来到直播间。"
    a = got["argv"]
    assert a[a.index("--tts") + 1] == "polly" and a[a.index("--voice") + 1] == "Zhiyu"
    assert a[a.index("--lang") + 1] == "cmn-CN"
    with pytest.raises(avatar.AvatarError, match="2000"):
        avatar.speech("字" * 2001)
    with pytest.raises(avatar.AvatarError, match="声音"):
        avatar.speech("你好你好", "Joanna")


# ---------------------------------------------------------------- API

def test_options_and_estimate_routes():
    o = client.get("/api/avatars/options").json()
    assert [m["id"] for m in o["models"] if m["default"]] == ["infinitetalk"]
    assert {s["label"] for s in o["scenes"]} == {"直播间带货", "超市货架口播", "门店促销", "产品特写讲解"}
    assert o["limits"]["script_chars"] == 2000 and o["configured"] is True
    e = client.get("/api/avatars/estimate", params={"script": "字" * 225}).json()
    assert 50 <= e["seconds"] <= 52
    assert client.get("/api/avatars/estimate", params={"size": "1080"}).status_code == 422


def test_script_only_job_end_to_end(isolate, media):
    r = post_files({"portrait": media["png"]}, script="大家好，今天给大家介绍一款保湿面霜。",
                   voice="Zhiyu", scene="shelf", size="480", fast="true", seed=9)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["status"] in ("queued", "running", "done") and s["audio_source"]["type"] == "script"
    assert s["prompt"] == avatar_api.SCENES["shelf"][1]
    s = wait(s["id"])
    assert s["status"] == "done", s
    assert isolate["speech"] == [("大家好，今天给大家介绍一款保湿面霜。", "Zhiyu")]
    assert set(s["urls"]) == {"video", "portrait", "audio", "poster"}
    for k in ("video", "portrait", "audio", "poster"):
        f = client.get(s["urls"][k])
        assert f.status_code == 200 and len(f.content) > 100
    assert client.get(s["urls"]["video"]).headers["content-type"] == "video/mp4"
    req = isolate["gpu"].reqs[-1]
    assert req["model"] == "infinitetalk" and req["seed"] == 9 and req["prompt"].startswith("a presenter")
    texts = [n["text"] for n in s["notes"]]
    assert texts[0] == "已排队" and any("Polly" in t for t in texts) and texts[-1] == "完成"
    assert s["worker_meta"]["render_s"] == 12 and s["video_seconds"] > 2
    assert any(x["id"] == s["id"] for x in client.get("/api/avatars").json())
    # task center lists it
    items = collect.collect(["avatar"])
    assert [(i["kind"], i["id"], i["status"], i["url"]) for i in items] == \
        [("avatar", s["id"], "done", "/avatar")]
    assert any(i["kind"] == "avatar" for i in client.get("/api/tasks").json())
    assert client.get("/api/tasks?kind=avatar").status_code == 200
    # delete
    assert client.delete(f"/api/avatars/{s['id']}").json() == {"deleted": s["id"]}
    assert client.get(f"/api/avatars/{s['id']}").status_code == 404


def test_audio_upload_and_json_with_asset_ids(isolate, media):
    from app.assets import store
    pa = store.ingest(media["png"], "face.png", name="脸")
    aa = store.ingest(media["mp3"], "voice.mp3", name="声音")
    r = client.post("/api/avatars", json={"portrait_asset_id": pa["id"],
                                          "audio_asset_id": aa["id"], "model": "longcat"})
    assert r.status_code == 201, r.text
    s = wait(r.json()["id"])
    assert s["status"] == "done" and isolate["speech"] == []
    assert s["portrait_source"] == {"type": "portrait_asset_id", "id": pa["id"]}
    assert isolate["gpu"].reqs[-1]["model"] == "longcat"
    # asset kind mismatch
    r = client.post("/api/avatars", json={"portrait_asset_id": aa["id"], "script": "你好你好"})
    assert r.status_code == 422 and "image" in r.json()["detail"]
    # multipart audio upload
    r = post_files({"portrait": media["jpg"], "audio": media["mp3"]})
    assert r.status_code == 201
    assert wait(r.json()["id"])["audio_seconds"] == pytest.approx(3, abs=0.3)


def test_portrait_from_image_generation(isolate, media):
    img_id = "20261104-101010-abcdef"
    d = images.ROOT / img_id
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(media["png"], d / "1.png")
    r = client.post("/api/avatars", json={"image_id": img_id, "image_index": 1,
                                          "script": "你好，欢迎光临。"})
    assert r.status_code == 201, r.text
    assert wait(r.json()["id"])["status"] == "done"
    r = client.post("/api/avatars", json={"image_id": img_id, "image_index": 0, "script": "你好"})
    assert r.status_code == 422
    shutil.rmtree(d)


def test_reference_video_job(isolate, media):
    r = post_files({"ref_video": media["mp4"]}, script="欢迎来到门店，今天全场八折。", scene="store")
    assert r.status_code == 201, r.text
    s = wait(r.json()["id"])
    assert s["status"] == "done" and "ref_video" in s["urls"]
    assert "video_b64" in isolate["gpu"].reqs[-1]


@pytest.mark.parametrize("body,msg", [
    ({"script": "你好你好"}, "人像"),
    ({"portrait_asset_id": "asset_bad", "script": "你好"}, "格式"),
    ({"image_id": "20261104-101010-abcdef", "portrait_asset_id":
      "asset_20261104-101010-abcdef", "script": "你好"}, "只能选一个"),
    ({"image_id": "20261104-101010-abcdef"}, None),
])
def test_validation_json(body, msg):
    r = client.post("/api/avatars", json=body)
    assert r.status_code == 422
    if msg:
        assert msg in r.json()["detail"]


def test_validation_fields(media):
    for fields, msg in (({"script": "字" * 2001}, "2000"), ({"script": "你好", "voice": "Joanna"}, "声音"),
                        ({"script": "你好", "model": "sora"}, "模型"),
                        ({"script": "你好", "size": "1080"}, "size"),
                        ({"script": "你好", "seed": "x"}, "seed"),
                        ({"script": "你好", "scene": "beach"}, "场景"),
                        ({}, "声音需要")):
        r = post_files({"portrait": media["png"]}, **fields)
        assert r.status_code == 422 and msg in r.json()["detail"], (fields, r.text)
    r = post_files({"portrait": media["tiny"]}, script="你好")
    assert r.status_code == 422 and "太小" in r.json()["detail"]
    r = post_files({"portrait": media["png"], "audio": media["mp3"]}, script="你好")
    assert r.status_code == 422 and "只能选一个" in r.json()["detail"]
    assert not list(avatar_api.ROOT.glob("*/state.json")) if avatar_api.ROOT.exists() else True


def test_failed_job_and_ids(isolate, media):
    isolate["gpu"].fail = "no face found"
    r = post_files({"portrait": media["png"]}, script="你好，欢迎。")
    s = wait(r.json()["id"])
    assert s["status"] == "failed" and "no face found" in s["error"]
    assert s["notes"][-1]["text"].startswith("失败")
    assert collect.collect(["avatar"])[0]["status"] == "failed"
    # ids are checked against ID_RE (a "../" path is normalized by the client and, once
    # web/dist is built, answered by the SPA fallback, so it says nothing about this route)
    assert client.get("/api/avatars/..etc").status_code == 404
    assert client.get("/api/avatars/20261104-000000-zzzzzz/video").status_code == 404
    assert client.get("/api/avatars/20261104-000000-zzzzzz").status_code == 404
    assert client.get(f"/api/avatars/{s['id']}/secret").status_code == 404
    assert client.get(f"/api/avatars/{s['id']}/video").status_code == 404  # not made


def test_cannot_delete_running(isolate, media, monkeypatch):
    item = {"id": "20261104-111111-aaaaaa"}
    d = avatar_api.ROOT / item["id"]
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps(dict(item, status="running", created=1)))
    assert client.delete(f"/api/avatars/{item['id']}").status_code == 409


# ---------------------------------------------------------------- nodes + template

def test_nodes_registered_and_consistent():
    check_types()
    d = NODES["avatar.infinitetalk"]
    assert d["runner"] == "gpu:avatar" and d["outputs"] == {"video": "video"}
    assert d["inputs"] == {"audio": "audio", "portrait": "image", "ref_video": "video"}
    assert set(d["optional"]) == {"portrait", "ref_video"}
    assert d["params"]["model"]["enum"] == ["", "infinitetalk", "longcat"]
    assert d["params"]["size"]["enum"] == ["480", "720"]
    assert NODES["tts.polly"]["outputs"] == {"audio": "audio"}
    nodes = {"tts.polly": {"type": "tts.polly", "existing": True}}
    assert avatar_node.register(nodes) == ["avatar.infinitetalk"]  # an existing TTS is kept
    assert nodes["tts.polly"]["existing"]


def test_template_validates():
    wf = wf_store.template("wf_product_pitch")
    assert wf and validate(wf, MODS) == []
    errs = validate(wf, {"image", "video", "music"})
    assert [e["code"] for e in errs] == ["module_unavailable"]
    instr = next(n for n in wf["nodes"] if n["type"] == "agent.write")["params"]["instruction"]
    assert "最" in instr and "第一" in instr and "30–60" in instr
    bad = copy.deepcopy(wf)
    next(n for n in bad["nodes"] if n["type"] == "avatar.infinitetalk")["params"]["size"] = "1080"
    assert any(e["code"] == "bad_param" for e in validate(bad, MODS))
    bad["edges"] = [e for e in bad["edges"] if e["to"] != "n6.audio"]
    assert any(e["code"] == "missing_input" and e["port"] == "audio" for e in validate(bad, MODS))


class Ctx:
    def __init__(self, d):
        self.dir, self.meta, self.notes = d, {}, []

    def on_wait(self, sec):
        self.notes.append(sec)


def test_node_runs(isolate, media, tmp_path):
    ctx = Ctx(tmp_path)
    out = NODES["tts.polly"]["run"]({"text": "你好，欢迎来到直播间。"}, {"voice": "Zhiyu", "text": ""}, ctx)
    assert Path(out["audio"]).is_file() and isolate["speech"][-1][1] == "Zhiyu"
    p = {"model": "", "prompt": "a host", "size": "480", "fast": True, "seed": 3}
    out = NODES["avatar.infinitetalk"]["run"]({"audio": out["audio"],
                                               "portrait": str(media["png"])}, p, ctx)
    assert Path(out["video"]).read_bytes() == media["mp4"].read_bytes()
    assert ctx.meta["model"] == "infinitetalk" and isolate["gpu"].reqs[-1]["seed"] == 3
    with pytest.raises(ValueError, match="人像"):
        NODES["avatar.infinitetalk"]["run"]({"audio": str(media["mp3"])}, p, ctx)
    with pytest.raises(ValueError, match="只连一个"):
        NODES["avatar.infinitetalk"]["run"]({"audio": str(media["mp3"]), "portrait": str(media["png"]),
                                             "ref_video": str(media["mp4"])}, p, ctx)


def test_merged_module_config_drives_status_and_agent_cost():
    """The real infra/gpu_ec2/modules/avatar.json (feat/avatar-infra) feeds status(), the
    page's cold-start figure and the workflow agent's GPU cost table."""
    from app.workflows import agent
    real = json.loads((avatar.MODULES / "avatar.json").read_text())
    assert real["default_model"] == "infinitetalk" and real["cold_min"] == 13
    assert agent.AVATAR_COLD_MIN == 13
    assert agent.AVATAR_MIN == round(30 * real["render_s_per_s"]["infinitetalk"]["480_fast"] / 60 + 1)
    assert "gpu:avatar" in agent.COST
    wf = {"nodes": [{"id": "n1", "type": "avatar.infinitetalk", "params": {}},
                    {"id": "n2", "type": "tts.polly", "params": {}}]}
    assert agent.gpu_cost(wf, ["n1", "n2"]) == agent.AVATAR_MIN > 0


def test_busy_heartbeat_stays_fresh_through_long_jobs():
    """A busy heartbeat is written once per job; a 25-min avatar render must not read as
    'booting' after heartbeat_stale_s."""
    import datetime
    from app import gpu_ec2
    g = gpu_ec2.GpuEC2.__new__(gpu_ec2.GpuEC2)
    g.cfg = {"heartbeat_stale_s": 180, "invoke_timeout_s": 5400}
    inst = {"InstanceId": "i-1", "InstanceType": "g7e.4xlarge", "State": {"Name": "running"},
            "Placement": {"AvailabilityZone": "us-east-1a"},
            "LaunchTime": datetime.datetime(2026, 11, 4)}
    g.instances = lambda *a: [inst]
    hb = {"instance_id": "i-1", "state": "busy", "ts": time.time() - 1500}
    g.heartbeat = lambda: hb
    assert g.status()["state"] == "busy"
    hb.update(state="ready")
    assert g.status()["state"] == "booting"
    hb.update(state="busy", ts=time.time() - 6000)
    assert g.status()["state"] == "booting"
