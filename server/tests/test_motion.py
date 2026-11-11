"""Motion editor (docs/REMOTION_CONTRACT.md §7, part C): versions, voice lock, drift warnings,
workbench lifecycle guards, HTTP API and the /motion/ reverse proxy. No dev server, no render."""
import http.server
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, motion_api, pipeline
from app.motion import versions, workbench

JOB = "20261111-000000-abcdef"
TL = {"fps": 30, "width": 1920, "height": 1080, "background": "#101828", "revision": "t1",
      "scenes": [{"id": "s01", "label": "开场", "from": 0, "duration": 300, "card": "x/y"},
                 {"id": "s02", "label": "中段", "from": 300, "duration": 300, "card": "x/y"}]}
AUDIO = {"voice": {"id": "voice", "from": 0, "duration": 600, "src": "audio/voice_bgm.wav",
                   "volume": 1}, "sfx": []}


def _clip(card, start, dur, **kw):
    return {"id": f"c{card}{start}", "cardId": card, "start": start, "duration": dur,
            "inOffset": 0, "speed": 1, "opacity": 1, "scale": 1, "x": 0, "y": 0,
            "props": kw.pop("props", {}), **kw}


def _project():
    return {"name": "t", "fps": 30, "width": 1920, "height": 1080, "tracks": [
        {"id": "tc", "name": "Captions", "clips": [
            _clip("proj:caption:caption", 6, 200, props={"text": "原字幕"})]},
        {"id": "ts", "name": "Shots", "clips": [
            _clip("proj:shot:scene-s01", 0, 300), _clip("proj:shot:scene-s02", 300, 300)]},
        {"id": "tm", "name": "Music", "clips": [
            _clip("audio-clip", 0, 600, props={"file": "audio/voice_bgm.wav", "volume": 1},
                  label="配音（已锁定）")]},
    ]}


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA", tmp_path)
    d = tmp_path / "jobs" / JOB
    data = d / "remotion" / "src" / "data"
    data.mkdir(parents=True)
    (d / "remotion" / "src" / "workbench.ts").write_text("export const WORKBENCH = {};\n")
    (data / "timeline.json").write_text(json.dumps(TL))
    (data / "audio.json").write_text(json.dumps(AUDIO))
    (d / "remotion" / "public" / "audio").mkdir(parents=True)
    (d / "remotion" / "public" / "audio" / "voice_bgm.wav").write_bytes(b"RIFF")
    (d / "final.mp4").write_bytes(b"v1")
    (d / "job.json").write_text(json.dumps({"id": JOB, "status": "done", "renderer": "remotion"}))
    return d


@pytest.fixture
def fake_render(monkeypatch):
    calls = []

    def render(proj, out, log=None, **kw):
        calls.append({"out": Path(out), "env": os.environ.get("WB_PROJ"), **kw})
        Path(out).write_bytes(b"mp4")
    monkeypatch.setattr(versions.rrender, "render", render)
    monkeypatch.setattr(workbench, "render_link", lambda job_id, proj: f".evp-proj/{job_id}")
    monkeypatch.setattr(workbench, "repo_public_entries", lambda: [])
    return calls


def _wait(d, n, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rec = next(r for r in versions.list_versions(d) if r["n"] == n)
        if rec["status"] in ("done", "failed"):
            return rec
        time.sleep(0.05)
    raise AssertionError("version did not finish")


# ---------------------------------------------------------------- pure logic

def test_validate_accepts_import_and_rejects_mismatch():
    versions.validate(_project(), TL)
    p = _project()
    p["fps"] = 25
    with pytest.raises(versions.VersionError, match="fps"):
        versions.validate(p, TL)
    p = _project()
    p["tracks"][1]["clips"][0]["duration"] = 10 ** 7
    with pytest.raises(versions.VersionError):
        versions.validate(p, TL)
    with pytest.raises(versions.VersionError):
        versions.validate({"fps": 30, "width": 1920, "height": 1080, "tracks": []}, TL)


def test_voice_lock_untouched_is_noop():
    p, w = versions.apply_voice_lock(_project(), TL, AUDIO)
    assert w == [] and p == _project()


def test_voice_lock_restores_moved_voice():
    p = _project()
    v = p["tracks"][2]["clips"][0]
    v.update(start=60, duration=500, speed=1.5)
    v["props"]["volume"] = 0.2
    out, w = versions.apply_voice_lock(p, TL, AUDIO)
    voices = [c for t in out["tracks"] for c in t["clips"] if c["cardId"] == "audio-clip"]
    assert len(voices) == 1
    assert (voices[0]["start"], voices[0]["duration"], voices[0]["speed"],
            voices[0]["props"]["volume"]) == (0, 600, 1, 1)
    assert w and "配音" in w[0]
    assert p["tracks"][2]["clips"][0]["start"] == 60  # input not mutated


def test_voice_lock_readds_deleted_split_or_hidden_voice():
    p = _project()
    p["tracks"][2]["clips"] = []
    out, w = versions.apply_voice_lock(p, TL, AUDIO)
    assert [c["start"] for t in out["tracks"] for c in t["clips"] if c["cardId"] == "audio-clip"] == [0]
    assert w
    p = _project()  # split in two
    v = p["tracks"][2]["clips"][0]
    p["tracks"][2]["clips"] = [dict(v, duration=300), dict(v, id="b", start=300, duration=300)]
    out, _ = versions.apply_voice_lock(p, TL, AUDIO)
    assert [(c["start"], c["duration"]) for t in out["tracks"] for c in t["clips"]
            if c["cardId"] == "audio-clip"] == [(0, 600)]
    p = _project()
    p["tracks"][2]["hidden"] = True
    out, w = versions.apply_voice_lock(p, TL, AUDIO)
    live = [c for t in out["tracks"] if not t.get("hidden") for c in t["clips"]
            if c["cardId"] == "audio-clip"]
    assert len(live) == 1 and w


def test_voice_lock_clamps_to_film_length():
    audio = {"voice": dict(AUDIO["voice"], duration=900), "sfx": []}
    assert versions.voice_clip(TL, audio["voice"])["duration"] == 600


def test_drift_warnings_threshold():
    p = _project()
    assert versions.drift_warnings(p, TL) == []
    p["tracks"][1]["clips"][1]["duration"] += 15  # exactly the tolerance: fine
    assert versions.drift_warnings(p, TL) == []
    p["tracks"][1]["clips"][1]["duration"] += 15
    p["tracks"][1]["clips"][0]["start"] += 20
    w = versions.drift_warnings(p, TL)
    assert len(w) == 2 and any("s02" in x and "+30 帧" in x for x in w)
    assert any("s01" in x and "起点" in x for x in w)
    p = _project()
    p["tracks"][1]["clips"].pop(0)
    assert any("s01" in x and "移除" in x for x in versions.drift_warnings(p, TL))


# ---------------------------------------------------------------- versions store + render

def test_submit_renders_version_and_records(job, fake_render):
    p = _project()
    p["tracks"][2]["clips"][0]["start"] = 60
    p["tracks"][1]["clips"][1]["duration"] += 30
    rec = versions.submit(JOB, job, p)
    assert rec["n"] == 2 and rec["status"] == "queued" and len(rec["warnings"]) == 2
    done = _wait(job, 2)
    assert done["status"] == "done", done
    assert (job / "versions" / "v2.mp4").read_bytes() == b"mp4"
    call = fake_render[0]
    assert call["env"] == {"WB_PROJ": f".evp-proj/{JOB}"} and os.environ.get("WB_PROJ") is None
    assert call["entry"] == "src/remotion/index.ts" and call["comp"] == "Main"
    assert call["props"]["renderExact"] is True
    assert call["props"]["project"]["tracks"][2]["clips"][0]["start"] == 0  # voice locked
    assert (Path(call["public_dir"]) / "audio" / "voice_bgm.wav").is_file()
    assert not any(p.name.startswith(".v2.tmp") for p in (job / "versions").iterdir())
    assert versions.video_path(job, 2) == job / "versions" / "v2.mp4"
    assert versions.video_path(job, 1) == job / "final.mp4"
    assert json.loads((job / "job.json").read_text()) == {"id": JOB, "status": "done",
                                                         "renderer": "remotion"}
    rec3 = versions.submit(JOB, job, _project())
    assert rec3["n"] == 3 and rec3["warnings"] == []
    _wait(job, 3)


def test_render_failure_is_recorded(job, monkeypatch, fake_render):
    def boom(proj, out, log=None, **kw):
        Path(log).write_text("Error: something exploded\n")
        raise RuntimeError("remotion render failed (exit 1), see log")
    monkeypatch.setattr(versions.rrender, "render", boom)
    versions.submit(JOB, job, _project())
    rec = _wait(job, 2)
    assert rec["status"] == "failed" and "exploded" in rec["error"]
    assert versions.video_path(job, 2) is None


def test_recover_marks_stale_records_failed(job):
    vd = job / "versions"
    vd.mkdir()
    (vd / "index.json").write_text(json.dumps([
        {"n": 2, "file": "v2.mp4", "status": "rendering", "created": 1, "warnings": [], "error": None},
        {"n": 3, "file": "v3.mp4", "status": "done", "created": 2, "warnings": [], "error": None}]))
    assert versions.recover(pipeline.DATA) == 1
    recs = {r["n"]: r for r in versions.list_versions(job)}
    assert recs[2]["status"] == "failed" and recs[3]["status"] == "done"


def test_recover_runs_only_at_startup_not_import(job):
    vd = job / "versions"
    vd.mkdir()
    (vd / "index.json").write_text(json.dumps([
        {"n": 2, "file": "v2.mp4", "status": "queued", "created": 1, "warnings": [], "error": None}]))
    import importlib
    importlib.reload(versions)  # importing must not touch records
    assert versions.list_versions(job)[1]["status"] == "queued"
    with TestClient(main.app):
        pass
    assert versions.list_versions(job)[1]["status"] == "failed"


# ---------------------------------------------------------------- workbench guards

def test_is_our_vite_rejects_foreign_pid():
    assert workbench.is_our_vite(os.getpid()) is False


def test_status_without_pidfile(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "WB", tmp_path)
    assert workbench.status() == {"running": False, "job_id": None, "port": workbench.PORT}
    assert workbench.stop()["stopped"] is False


def test_stop_ignores_reused_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "WB", tmp_path)
    (tmp_path / ".dev.pid").write_text(str(os.getpid()))  # alive, but not a vite
    assert workbench.stop()["stopped"] is False
    assert (tmp_path / ".dev.pid").exists()


def test_check_ready_requires_patches(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "WB", tmp_path)
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "vite.config.ts").write_text("export default {}")
    with pytest.raises(workbench.MotionError, match="setup.sh"):
        workbench.check_ready()


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def client():
    return TestClient(main.app)


def test_editor_rejects_non_remotion_job(job, client):
    (job / "job.json").write_text(json.dumps({"id": JOB, "status": "done", "renderer": "html"}))
    assert client.post(f"/api/jobs/{JOB}/editor").status_code == 409
    (job / "job.json").write_text(json.dumps({"id": JOB, "status": "running", "renderer": "remotion"}))
    assert client.post(f"/api/jobs/{JOB}/editor").status_code == 409
    assert client.post("/api/jobs/20261111-000000-000000/editor").status_code == 404


def test_editor_starts_workbench(job, client, monkeypatch):
    seen = []
    monkeypatch.setattr(workbench, "start", lambda jid, proj: seen.append((jid, proj)) or {})
    r = client.post(f"/api/jobs/{JOB}/editor")
    assert r.status_code == 200
    assert r.json() == {"job_id": JOB, "url": "/motion/?import=project"}
    assert seen == [(JOB, job / "remotion")]

    def fail(jid, proj):
        raise workbench.MotionError("工作台启动失败：x")
    monkeypatch.setattr(workbench, "start", fail)
    r = client.post(f"/api/jobs/{JOB}/editor")
    assert r.status_code == 503 and "工作台" in r.json()["detail"]


def test_versions_api(job, client, fake_render):
    r = client.get(f"/api/jobs/{JOB}/versions")
    assert [v["n"] for v in r.json()] == [1]
    bad = _project()
    bad["width"] = 1080
    r = client.post(f"/api/jobs/{JOB}/versions", json={"project": bad})
    assert r.status_code == 422 and "width" in r.json()["detail"]
    assert client.post(f"/api/jobs/{JOB}/versions", content=b"not json").status_code == 422
    assert client.post(f"/api/jobs/{JOB}/versions", json={"x": 1}).status_code == 422
    r = client.post(f"/api/jobs/{JOB}/versions", json={"project": _project()})
    assert r.status_code == 202 and r.json()["n"] == 2
    _wait(job, 2)
    assert [v["status"] for v in client.get(f"/api/jobs/{JOB}/versions").json()] == ["done", "done"]
    r = client.get(f"/api/jobs/{JOB}/versions/2/video")
    assert r.status_code == 200 and r.content == b"mp4"
    assert client.get(f"/api/jobs/{JOB}/versions/9/video").status_code == 404
    assert client.get(f"/api/jobs/{JOB}/versions/1/video").content == b"v1"


def test_versions_post_size_cap(job, client, monkeypatch):
    monkeypatch.setattr(versions, "MAX_PROJECT_BYTES", 100)
    r = client.post(f"/api/jobs/{JOB}/versions", json={"project": _project()})
    assert r.status_code == 413


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_proxy_down_is_503(client, monkeypatch):
    monkeypatch.setattr(workbench, "PORT", _free_port())
    r = client.get("/motion/")
    assert r.status_code == 503
    r = client.get("/motion?import=project", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/motion/?import=project"


def test_proxy_forwards_path_query_body_and_range(client, monkeypatch):
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def _reply(self, body=b""):
            seen.update(path=self.path, host=self.headers["Host"], range=self.headers.get("Range"),
                        method=self.command, body=body)
            self.send_response(206 if self.headers.get("Range") else 200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("X-Up", "1")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_GET(self):
            self._reply()

        def do_POST(self):
            self._reply(self.rfile.read(int(self.headers["Content-Length"])))

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(workbench, "PORT", srv.server_address[1])
        r = client.get("/motion/src/%E4%B8%AD.tsx?import=project&t=1", headers={"Range": "bytes=0-1"})
        assert r.status_code == 206 and r.text == "ok" and r.headers["x-up"] == "1"
        assert seen["path"] == "/motion/src/%E4%B8%AD.tsx?import=project&t=1"
        assert seen["host"] == f"127.0.0.1:{srv.server_address[1]}" and seen["range"] == "bytes=0-1"
        r = client.post("/motion/api/export", content=b'{"project":{}}')
        assert r.status_code == 200 and seen["method"] == "POST" and seen["body"] == b'{"project":{}}'
    finally:
        srv.shutdown()


def test_motion_status_api(client, tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "WB", tmp_path)
    assert client.get("/api/motion/status").json() == {"running": False, "job_id": None,
                                                        "port": workbench.PORT}
    assert motion_api.router is not None
