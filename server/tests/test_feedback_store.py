"""Phase 5 part A: feedback store, API (without analyze/proposals) and skill version."""
import json
import shutil
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import feedback, pipeline
from app.feedback import api as fb_api
from app.feedback import store
from app.pipeline import DATA

JOB = "20261104-080000-123456"


@pytest.fixture(autouse=True)
def clean():
    for sub in ("feedback", "jobs", "wf_runs"):
        shutil.rmtree(DATA / sub, ignore_errors=True)
    yield


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(fb_api.router)
    return TestClient(app)


def put_job():
    d = DATA / "jobs" / JOB
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({"id": JOB, "seconds": 60, "status": "done",
                                            "captions": True, "steps": [
                                                {"key": "mix", "status": "done"},
                                                {"key": "captions", "status": "failed"}]}))
    return d


def test_ids_and_create_get_list():
    put_job()
    it = store.create({"kind": "job", "id": JOB}, ["字幕", "bogus", "字幕"], "太快" * 400)
    assert store.valid_id(it["id"], "fb")
    assert it["reasons"] == ["字幕"] and len(it["text"]) == 500
    assert it["status"] == "new" and it["analysis_id"] is None
    snap = it["snapshot"]
    assert snap["skill_version"] == "1.0.0" and snap["skill_commit"]
    assert snap["record"]["id"] == JOB
    assert snap["qc"]["failed_steps"] == ["captions"]
    assert (DATA / "feedback" / "items" / f"{it['id']}.json").is_file()
    assert store.get(it["id"])["id"] == it["id"]
    assert store.get("../../etc/passwd") is None
    assert [x["id"] for x in store.list_items()] == [it["id"]]
    assert store.list_items("analyzing") == []


def test_create_validates():
    with pytest.raises(ValueError):
        store.create({"kind": "nope", "id": "x"}, ["画面"])
    with pytest.raises(ValueError):
        store.create({"kind": "job", "id": "../x"}, ["画面"])
    with pytest.raises(ValueError):
        store.create({"kind": "job", "id": "x"}, ["bogus"])
    # a target with no files still records, with an empty QC
    it = store.create({"kind": "image", "id": "missing"}, "画面")
    assert it["snapshot"]["record"] is None and it["snapshot"]["qc"] == {}


def test_update_is_locked_and_atomic():
    it = store.create({"kind": "job", "id": JOB}, ["声音"])

    def bump(i):
        store.add_history(it["id"], "tick", str(i))
    ts = [threading.Thread(target=bump, args=(i,)) for i in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    got = store.get(it["id"])
    assert len(got["history"]) == 41
    assert not list((DATA / "feedback" / "items").glob(".*tmp"))
    store.set_status(it["id"], "analyzing")
    assert store.get(it["id"])["status"] == "analyzing"
    with pytest.raises(ValueError):
        store.set_status(it["id"], "bogus")
    with pytest.raises(KeyError):
        store.update("fb_20260101-000000-abcdef", lambda x: None)


def test_analyses_and_proposals():
    a = store.save_analysis({"feedback_id": "fb_x", "verdict": "rule",
                             "cluster": "mix:music_over_voice"})
    assert store.valid_id(a["id"], "an") and store.get_analysis(a["id"])["verdict"] == "rule"
    p = store.save_proposal({"cluster": "mix:music_over_voice", "state": "draft"})
    assert store.valid_id(p["id"], "pr")
    assert store.get_proposal(p["id"])["state"] == "draft"
    assert [x["id"] for x in store.list_proposals("draft")] == [p["id"]]
    assert store.list_proposals("open") == []
    p["state"] = "open"
    store.save_proposal(p)
    assert store.get_proposal(p["id"])["state"] == "open"


def test_api_flow_without_analyze(client, monkeypatch):
    put_job()
    real = fb_api._mod
    monkeypatch.setattr(fb_api, "_mod", lambda name, required=True:
                        real("__missing_" + name, required))
    r = client.post("/api/feedback", json={"target": {"kind": "job", "id": JOB},
                                           "reasons": ["字幕"], "text": "字幕太快"})
    assert r.status_code == 200, r.text
    fb = r.json()
    assert "analysis_started" not in fb
    assert client.post("/api/feedback", json={"target": {"kind": "x", "id": JOB},
                                              "reasons": ["字幕"]}).status_code == 422
    got = client.get(f"/api/feedback/{fb['id']}").json()
    assert got["item"]["text"] == "字幕太快" and got["analysis"] is None
    assert [x["id"] for x in client.get("/api/feedback?status=new").json()] == [fb["id"]]
    assert client.get("/api/feedback?status=zzz").status_code == 422
    assert client.get("/api/feedback/fb_20260101-000000-abcdef").status_code == 404
    for path in ("analyze", "act", "draft"):
        assert client.post(f"/api/feedback/{fb['id']}/{path}").status_code == 501
    p = store.save_proposal({"cluster": "c", "state": "draft"})
    for path in ("check", "open", "sync"):
        assert client.post(f"/api/feedback/proposals/{p['id']}/{path}").status_code == 501
    assert client.get(f"/api/feedback/proposals/{p['id']}").json()["state"] == "draft"
    assert client.get("/api/feedback/proposals/pr_bad").status_code == 404
    d = client.post(f"/api/feedback/{fb['id']}/dismiss").json()
    assert d["status"] == "dismissed"
    s = client.get("/api/feedback/skill").json()
    assert s["version"] == "1.0.0" and s["commit"] and s["history"] == []


def test_api_starts_analysis_and_clusters(client, monkeypatch):
    import types
    ran = threading.Event()

    def analyze(fb_id):
        a = store.save_analysis({"feedback_id": fb_id, "verdict": "rule",
                                 "cluster": "captions:too_fast"})
        store.update(fb_id, lambda it: it.update(analysis_id=a["id"], status="clustered"))
        ran.set()
    from app.feedback import analyze as real_analyze
    mod = types.SimpleNamespace(analyze=analyze, cluster_count=real_analyze.cluster_count,
                                RULE_MIN=real_analyze.RULE_MIN)
    monkeypatch.setattr(fb_api, "_mod", lambda name, required=True: mod)
    ids = []
    for _ in range(2):
        ran.clear()
        r = client.post("/api/feedback", json={"target": {"kind": "job", "id": JOB},
                                               "reasons": ["字幕"]})
        assert r.json()["analysis_started"] is True
        assert ran.wait(5)
        ids.append(r.json()["id"])
    cl = client.get("/api/feedback/clusters").json()
    assert len(cl) == 1 and cl[0]["cluster"] == "captions:too_fast"
    assert cl[0]["count"] == 2 and cl[0]["proposal_id"] is None and cl[0]["rule_min"] == 3
    assert set(cl[0]["feedback_ids"]) == set(ids)


def test_skill_version_on_job_and_run(monkeypatch):
    monkeypatch.setattr(pipeline, "_run", lambda job_id: None)
    job = pipeline.create_job(JOB, "t", "中文", 30, "16:9", True, "synth", "Zhiyu")
    assert job["skill_version"] == "1.0.0" and job["skill_commit"] == feedback.skill_commit()
    saved = json.loads((DATA / "jobs" / JOB / "job.json").read_text())
    assert saved["skill_version"] == "1.0.0" and saved["skill_commit"]

    from app.workflows import engine
    monkeypatch.setattr(engine, "drive", lambda run_id: None)
    wf = {"name": "t", "nodes": [{"id": "a", "type": "input.text", "params": {"text": "x"}}],
          "edges": []}
    run = engine.start(wf)
    rj = engine.read_run(run["id"])
    assert rj["skill_version"] == "1.0.0" and rj["skill_commit"] == feedback.skill_commit()


def test_skill_commit_unknown_outside_git(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback, "ROOT", tmp_path)
    monkeypatch.setattr(feedback, "_commit", None)
    assert feedback.skill_commit() == "unknown"
    monkeypatch.setattr(feedback, "VERSION_FILE", tmp_path / "nope")
    assert feedback.skill_version() == "unknown"
