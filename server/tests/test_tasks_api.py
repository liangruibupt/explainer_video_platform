"""Task center (phase 4 part D): unified list, review proxy, notifications, watcher."""
import json
import os
import shutil
import time

import pytest

os.environ["EVP_TASK_WATCHER"] = "0"  # tests drive watcher.scan() themselves

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.pipeline import DATA  # noqa: E402
from app.tasks import api as tasks_api  # noqa: E402
from app.tasks import collect, notify, watcher  # noqa: E402
from app.workflows import engine  # noqa: E402

RUN = "20261104-090000-abcdef"
JOB = "20261104-080000-123456"
IMG = "20261104-070000-aaaaaa"


@pytest.fixture(autouse=True)
def clean():
    for sub in ("jobs", "images", "videos", "music", "wf_runs", "tasks", "settings"):
        shutil.rmtree(DATA / sub, ignore_errors=True)
    yield


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(tasks_api.router)
    return TestClient(app)


def put_job(status, steps=None, created=1.0):
    d = DATA / "jobs" / JOB
    d.mkdir(parents=True, exist_ok=True)
    steps = steps or [{"key": "storyboard", "label": "分镜", "status": "done"},
                      {"key": "illustrate", "label": "插图", "status": "running",
                       "note": "等待 GPU（3 分钟）"}]
    (d / "job.json").write_text(json.dumps({"id": JOB, "topic": "光合作用", "status": status,
                                            "created": created, "updated": created,
                                            "steps": steps, "error": None}))


def put_image(status, waited=None, created=2.0):
    d = DATA / "images" / IMG
    d.mkdir(parents=True, exist_ok=True)
    s = {"id": IMG, "status": status, "prompt": "一只猫", "created": created, "updated": created}
    if waited is not None:
        s["waited"] = waited
    (d / "state.json").write_text(json.dumps(s))


def put_run(status, nodes, created=3.0):
    d = DATA / "wf_runs" / RUN
    d.mkdir(parents=True, exist_ok=True)
    wf = {"nodes": [{"id": nid, "type": "review.human" if nid.startswith("rev") else "llm.text"}
                    for nid in nodes], "edges": []}
    (d / "run.json").write_text(json.dumps({"id": RUN, "name": "MV 工作流", "status": status,
                                            "created": created, "updated": created,
                                            "workflow": wf, "nodes": nodes}))


def test_collect_unified_list_and_filters(client):
    put_job("running")
    put_image("running", waited=150)
    img = DATA / "wf_runs" / RUN / "nodes" / "a"
    img.mkdir(parents=True)
    (img / "x.png").write_bytes(b"png")
    put_run("waiting_review", {"a": {"status": "done"},
                               "rev1": {"status": "waiting_review",
                                        "review_input": str(img / "x.png")}})
    items = client.get("/api/tasks").json()
    assert [i["kind"] for i in items] == ["workflow", "image", "job"]  # newest first
    job = items[2]
    assert job["progress"] == {"done": 1, "total": 2} and job["gpu_wait"] == 3
    assert job["url"] == f"/jobs/{JOB}"
    assert items[1]["gpu_wait"] == 2
    wf = items[0]
    assert wf["status"] == "waiting_review" and wf["progress"] == {"done": 1, "total": 2}
    assert wf["reviews"] == [{"run_id": RUN, "node_id": "rev1", "node_type": "review.human",
                              "preview_url": f"/api/wf_runs/{RUN}/file?node=rev1&port=input"}]
    assert [i["kind"] for i in client.get("/api/tasks?status=running").json()] == ["image", "job"]
    assert [i["kind"] for i in client.get("/api/tasks?kind=job,image").json()] == ["image", "job"]
    assert client.get("/api/tasks?kind=nope").status_code == 422
    revs = client.get("/api/tasks/reviews").json()
    assert len(revs) == 1 and revs[0]["preview_kind"] == "image"


def test_transitions_notify_once_and_restart_does_not_repeat():
    put_job("done")  # already finished before the watcher ever ran: recorded silently
    assert watcher.scan() == []
    put_image("running")
    assert watcher.scan() == []
    put_image("done")
    sent = watcher.scan()
    assert len(sent) == 1 and sent[0]["url"] == "/image" and sent[0]["level"] == "success"
    assert watcher.scan() == []  # unchanged: nothing new
    put_run("running", {"a": {"status": "running"}})
    assert watcher.scan() == []
    put_run("waiting_review", {"a": {"status": "done"},
                               "rev1": {"status": "waiting_review", "review_input": "文本"}})
    sent = watcher.scan()
    assert len(sent) == 1 and "待审核" in sent[0]["title"] and sent[0]["url"] == "/tasks?tab=review"
    put_run("failed", {"a": {"status": "failed", "error": "boom"}})
    sent = watcher.scan()
    assert len(sent) == 1 and sent[0]["body"] == "boom" and sent[0]["level"] == "error"
    # "restart": a fresh process reads seen.json and sends nothing again
    watcher._thread = None
    assert watcher.scan() == []
    assert len(notify.list_all()) == 3


def test_second_review_node_in_same_run_notifies_again():
    put_run("running", {"rev1": {"status": "pending"}})
    watcher.scan()
    put_run("waiting_review", {"rev1": {"status": "waiting_review", "review_input": "a"}})
    assert len(watcher.scan()) == 1
    put_run("waiting_review", {"rev1": {"status": "done"},
                               "rev2": {"status": "waiting_review", "review_input": "b"}})
    assert len(watcher.scan()) == 1


def test_notifications_endpoints(client):
    a = notify.notify("一", "b", "/x")
    notify.notify("二", "b", "/y", level="error")
    r = client.get("/api/tasks/notifications?unread=1").json()
    assert r["unread"] == 2 and [n["title"] for n in r["items"]] == ["二", "一"]
    assert r["settings"] == {"in_app": True, "browser": False}
    assert client.post("/api/tasks/notifications/read", json={"ids": [a["id"]]}).json() == {"marked": 1}
    assert [n["title"] for n in client.get("/api/tasks/notifications?unread=1").json()["items"]] == ["二"]
    assert client.post("/api/tasks/notifications/read", json={"all": True}).json() == {"marked": 1}
    assert client.get("/api/tasks/notifications").json()["unread"] == 0
    (DATA / "settings").mkdir(parents=True)
    (DATA / "settings" / "notifications.json").write_text('{"browser": true}')
    assert notify.settings() == {"in_app": True, "browser": True}


def test_review_proxy_calls_engine(client, monkeypatch):
    put_run("waiting_review", {"rev1": {"status": "waiting_review", "review_input": "a"}})
    calls = []

    def fake_review(run_id, nid, decision, note=""):
        calls.append((run_id, nid, decision, note))
        return {"id": run_id, "status": "running", "nodes": {nid: {"status": "done"}}}
    monkeypatch.setattr(engine, "review", fake_review)
    r = client.post(f"/api/tasks/reviews/{RUN}/rev1", json={"decision": "approve", "note": "好"})
    assert r.status_code == 200 and r.json()["node"]["status"] == "done"
    assert calls == [(RUN, "rev1", "approve", "好")]
    # this engine has no choice parameter: refuse rather than silently drop it
    r = client.post(f"/api/tasks/reviews/{RUN}/rev1", json={"decision": "approve", "choice": 1})
    assert r.status_code == 422 and len(calls) == 1

    def fake_review2(run_id, nid, decision, note="", choice=None):
        calls.append((run_id, nid, decision, note, choice))
        return {"id": run_id, "status": "running", "nodes": {}}
    monkeypatch.setattr(engine, "review", fake_review2)
    assert client.post(f"/api/tasks/reviews/{RUN}/rev1",
                       json={"decision": "reject", "choice": 1}).status_code == 200
    assert calls[-1] == (RUN, "rev1", "reject", "", 1)
    assert client.post(f"/api/tasks/reviews/{RUN}/nope", json={"decision": "approve"}).status_code == 404
    assert client.post("/api/tasks/reviews/bad/rev1", json={"decision": "approve"}).status_code == 404


def test_review_proxy_real_engine_reject(client):
    put_run("waiting_review", {"rev1": {"status": "waiting_review", "review_input": "a",
                                        "started": time.time()}})
    r = client.post(f"/api/tasks/reviews/{RUN}/rev1", json={"decision": "reject", "note": "不行"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert engine.read_run(RUN)["nodes"]["rev1"]["review"]["note"] == "不行"
    r = client.post(f"/api/tasks/reviews/{RUN}/rev1", json={"decision": "approve"})
    assert r.status_code == 409


def test_candidates_listed_and_served(client):
    d = DATA / "wf_runs" / RUN / "nodes" / "img"
    d.mkdir(parents=True)
    for i in range(2):
        (d / f"{i}.png").write_bytes(b"png%d" % i)
    put_run("waiting_review", {"rev1": {"status": "waiting_review",
                                        "review_input": str(d / "0.png"),
                                        "candidates": [str(d / "0.png"),
                                                       {"path": str(d / "1.png"), "label": "B"}]}})
    rv = collect.reviews()[0]
    assert [c["label"] for c in rv["candidates"]] == ["候选 1", "B"]
    r = client.get(rv["candidates"][1]["url"])
    assert r.status_code == 200 and r.content == b"png1"
    assert client.get(f"/api/tasks/reviews/{RUN}/rev1/candidates/5").status_code == 404


def test_watcher_start_is_idempotent(monkeypatch):
    monkeypatch.setenv("EVP_TASK_WATCHER", "1")
    monkeypatch.setattr(watcher, "INTERVAL", 0.05)
    try:
        assert watcher.start() is True
        assert watcher.start() is False
    finally:
        watcher.stop()
