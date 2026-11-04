"""Chat (phase 4 part A) tests: app.llm.ask and engine.start are stubbed (no model, no GPU)."""
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import llm, main
from app.chat import assistant, store
from app.workflows import agent, engine

COVER = {
    "id": "wf_cover", "name": "封面图", "description": "一句话生成封面",
    "nodes": [
        {"id": "n1", "type": "input.text", "params": {"text": "秋天的西湖"}},
        {"id": "n2", "type": "agent.image_prompt", "params": {}},
        {"id": "n3", "type": "image.qwen", "params": {"ratio": "9:16", "seed": 42}},
        {"id": "n4", "type": "review.human", "params": {}},
        {"id": "n5", "type": "output.save", "params": {"title": "封面"}},
    ],
    "edges": [
        {"from": "n1.text", "to": "n2.idea"},
        {"from": "n2.prompt", "to": "n3.prompt"},
        {"from": "n3.image", "to": "n4.input"},
        {"from": "n4.output", "to": "n5.work"},
    ],
}


class Stub:
    """Scripted Opus: decisions for the chat SYSTEM prompt, workflows for agent.SYSTEM."""

    def __init__(self):
        self.decisions, self.workflows, self.calls = [], [], []

    def __call__(self, system, user, max_tokens=8000):
        self.calls.append((system, user))
        if system == assistant.SYSTEM:
            return json.dumps(self.decisions.pop(0), ensure_ascii=False)
        assert system.startswith(agent.SYSTEM[:60])
        return json.dumps(self.workflows.pop(0), ensure_ascii=False)


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import pipeline
    monkeypatch.setattr(pipeline, "DATA", tmp_path)
    stub = Stub()
    monkeypatch.setattr(llm, "ask", stub)
    runs = {}

    def start(wf, parent=""):
        assert not engine.validate(wf)
        rid = f"20261104-0000{len(runs):02d}-abcdef"
        runs[rid] = {"id": rid, "name": wf["name"], "workflow": copy.deepcopy(wf),
                     "status": "running", "parent": parent or None, "works": [],
                     "nodes": {n["id"]: {"status": "pending", "type": n["type"]}
                               for n in wf["nodes"]}}
        return runs[rid]
    monkeypatch.setattr(engine, "start", start)
    monkeypatch.setattr(engine, "read_run", lambda rid: runs.get(rid, {}))
    stub.runs = runs
    return stub


@pytest.fixture
def client():
    return TestClient(main.app)


def ask(q):
    return {"intent": "image", "action": "ask", "question": q}


def build(intent="image", brief="竖版封面：秋天的西湖，暖色调", assumptions=()):
    return {"intent": intent, "action": "build", "brief": brief,
            "assumptions": list(assumptions), "reply": ""}


def test_session_crud(env, client):
    sid = client.post("/api/chat/sessions", json={"mode": "music", "allow_web": True}).json()["id"]
    assert re.fullmatch(r"chat_\d{8}-\d{6}-[0-9a-f]{6}", sid)
    s = client.get(f"/api/chat/sessions/{sid}").json()
    assert s["mode"] == "music" and s["allow_web"] is True and s["messages"] == []
    assert [x["id"] for x in client.get("/api/chat/sessions").json()] == [sid]
    assert client.get("/api/chat/sessions/chat_bad").status_code == 404
    assert client.get("/api/chat/sessions/chat_..").status_code == 404
    assert store.get("../../etc/passwd") is None
    assert client.post("/api/chat/sessions", json={"mode": "drama"}).status_code == 422


def test_intent_routing_auto_and_fixed(env, client):
    env.decisions = [{"intent": "music", "action": "ask", "question": "要什么风格？"}]
    sid = client.post("/api/chat/sessions", json={}).json()["id"]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "写首歌"}).json()
    assert s["intent"] == "music" and s["messages"][-1]["kind"] == "question"
    assert s["messages"][-1]["text"] == "要什么风格？"
    # a fixed mode wins over the model's classification
    env.decisions = [{"intent": "music", "action": "ask", "question": "比例？"}]
    sid2 = client.post("/api/chat/sessions", json={"mode": "image"}).json()["id"]
    s2 = client.post(f"/api/chat/sessions/{sid2}/messages", json={"text": "西湖"}).json()
    assert s2["intent"] == "image"
    assert '"session_mode": "image"' in env.calls[-1][1]


def test_follow_up_question_limit(env, client):
    sid = client.post("/api/chat/sessions", json={}).json()["id"]
    env.decisions = [ask("风格？"), ask("比例？"), ask("有参考图吗？"), ask("还要问")]
    env.workflows = [COVER]
    texts = ["做个封面", "写实", "竖版", "没有"]
    for t in texts[:3]:
        s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": t}).json()
        assert s["messages"][-1]["kind"] == "question"
    assert s["questions"] == 3
    assert '"questions_left": 0' not in env.calls[-1][1]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": texts[3]}).json()
    assert '"questions_left": 0' in env.calls[-2][1]  # the 4th decision call
    last = s["messages"][-1]
    assert last["kind"] == "plan" and s["run_id"] and s["questions"] == 3
    assert "其余按默认" in last["text"]
    assert "其余没说的按默认" in last["prompt"]


def test_workflow_generation_path(env, client):
    sid = client.post("/api/chat/sessions", json={"allow_web": False}).json()["id"]
    bad = copy.deepcopy(COVER)
    bad["edges"] = bad["edges"][1:]  # n2.idea unconnected -> validator error, 2nd round fixes
    env.decisions = [build(assumptions=["写实摄影风格"])]
    env.workflows = [bad, COVER]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "做个西湖封面"}).json()
    gen_user = env.calls[1][1]
    assert "竖版封面：秋天的西湖" in gen_user and "不允许联网检索" in gen_user
    assert "The validator found these errors" in env.calls[2][1]
    assert s["state"] == "running" and s["workflow"]["id"] == "wf_cover"
    assert s["run_id"] in env.runs and s["runs"] == [s["run_id"]]
    m = s["messages"][-1]
    assert m["kind"] == "plan" and "封面图" in m["text"] and "写实摄影风格" in m["text"]
    assert "审核" in m["text"]
    # progress card comes from engine.read_run
    p = s["progress"]
    assert p["status"] == "running"
    assert [n["id"] for n in p["nodes"]] == ["n1", "n2", "n3", "n4", "n5"]
    assert [n["status"] for n in p["nodes"]] == ["pending"] * 5


def test_generation_failure_is_reported(env, client):
    sid = client.post("/api/chat/sessions", json={}).json()["id"]
    bad = copy.deepcopy(COVER)
    bad["edges"] = []
    env.decisions = [build()]
    env.workflows = [bad, bad, bad]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "封面"}).json()
    assert s["run_id"] is None and s["messages"][-1]["kind"] == "error"
    assert not env.runs


def test_progress_outputs_review_and_status_message(env, client):
    sid = client.post("/api/chat/sessions", json={}).json()["id"]
    env.decisions = [build()]
    env.workflows = [COVER]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "封面"}).json()
    r = env.runs[s["run_id"]]
    r["nodes"]["n2"].update(status="done", outputs={"prompt": "west lake at dusk"})
    r["nodes"]["n3"].update(status="cached", outputs={"image": "/x/wf_cache/k/image.png"})
    r["nodes"]["n4"].update(status="waiting_review", review_input="/x/wf_cache/k/image.png")
    r["status"] = "waiting_review"
    g = client.get(f"/api/chat/sessions/{sid}").json()
    nodes = {n["id"]: n for n in g["progress"]["nodes"]}
    assert nodes["n2"]["outputs"] == [{"port": "prompt", "kind": "text",
                                       "text": "west lake at dusk"}]
    assert nodes["n3"]["outputs"][0]["url"] == \
        f"/api/wf_runs/{s['run_id']}/file?node=n3&port=image"
    assert nodes["n4"]["review"]["kind"] == "image"
    assert nodes["n4"]["review"]["url"].endswith("node=n4&port=input")
    assert g["messages"][-1]["kind"] == "status" and "审核" in g["messages"][-1]["text"]
    n = len(g["messages"])
    assert len(client.get(f"/api/chat/sessions/{sid}").json()["messages"]) == n  # once only
    r["status"] = "done"
    r["works"] = [{"node": "n5", "title": "封面", "file": "n5.png"}]
    g = client.get(f"/api/chat/sessions/{sid}").json()
    assert g["state"] == "done" and g["progress"]["works"][0]["url"].endswith("work=n5.png")
    assert "完成" in g["messages"][-1]["text"]


def test_edit_path_reruns_changed_nodes(env, client):
    sid = client.post("/api/chat/sessions", json={}).json()["id"]
    env.decisions = [build()]
    env.workflows = [COVER]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "封面"}).json()
    first = s["run_id"]
    night = copy.deepcopy(COVER)
    night["nodes"][0]["params"]["text"] = "夜景的西湖"
    env.decisions = [{"intent": "image", "action": "edit", "instruction": "封面换成夜景"}]
    env.workflows = [night]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "封面换成夜景"}).json()
    edit_user = env.calls[-1][1]
    assert "Current workflow" in edit_user and "封面换成夜景" in edit_user
    m = s["messages"][-1]
    assert m["kind"] == "edit" and m["rerun"] == ["n1", "n2", "n3", "n4", "n5"]
    assert "n1.text" in m["diff"]["summary"]
    assert "修改参数 n1.text" in m["text"] and "夜景的西湖" not in m["text"]
    assert s["run_id"] != first and s["runs"] == [first, s["run_id"]]
    assert env.runs[s["run_id"]]["parent"] == first
    assert s["workflow"]["nodes"][0]["params"]["text"] == "夜景的西湖"


def test_rerun_nodes_only_downstream_of_change():
    new = copy.deepcopy(COVER)
    new["nodes"][2]["params"]["seed"] = 7
    assert assistant.rerun_nodes(COVER, new) == ["n3", "n4", "n5"]
    assert assistant.rerun_nodes(COVER, copy.deepcopy(COVER)) == []


def test_edit_without_workflow_builds(env, client):
    sid = client.post("/api/chat/sessions", json={}).json()["id"]
    env.decisions = [{"intent": "image", "action": "edit", "instruction": "x"}]
    env.workflows = [COVER]
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "换成夜景"}).json()
    assert s["messages"][-1]["kind"] == "plan"


def test_bad_model_json_and_web_flag(env, client, monkeypatch):
    sid = client.post("/api/chat/sessions", json={"allow_web": True}).json()["id"]
    env.decisions = []
    env.workflows = [COVER]
    orig = env.__call__

    def call(system, user, max_tokens=8000):
        if system == assistant.SYSTEM:
            env.calls.append((system, user))
            return "抱歉，我不太确定"
        return orig(system, user, max_tokens)
    monkeypatch.setattr(llm, "ask", call)
    s = client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "西湖封面"}).json()
    assert s["messages"][-1]["kind"] == "plan"
    assert "允许联网检索资料" in s["messages"][-1]["prompt"]
    assert '"web_research_allowed": true' in env.calls[0][1]


def test_guide_first_message(env, client):
    env.decisions = [build(intent="explainer", brief="讲解：瑞利散射，60 秒")]
    env.workflows = [COVER]
    r = client.post("/api/chat/sessions", json={"mode": "explainer", "message": "做讲解视频"})
    s = store.get(r.json()["id"])
    assert s["intent"] == "explainer" and s["messages"][0]["text"] == "做讲解视频"
    assert "explainer.video" in s["messages"][-1]["prompt"]
