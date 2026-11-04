"""Minimal edits in chat: rerun set, one narrowing retry, cache report (llm.ask stubbed)."""
import copy
import json
from pathlib import Path

import pytest

from app import llm
from app.chat import assistant
from app.workflows import agent

MV = json.loads((Path(__file__).resolve().parents[1] / "app/workflows/templates/"
                 "mv_from_one_line.json").read_text())


def node(wf, nid):
    return next(n for n in wf["nodes"] if n["id"] == nid)


def with_params(**changes):
    """MV copy with {nid: {param: value}} applied."""
    wf = copy.deepcopy(MV)
    for nid, ps in changes.items():
        node(wf, nid)["params"].update(ps)
    return wf


class Script:
    """llm.ask returning scripted workflows in order; records the user prompts."""

    def __init__(self, *answers):
        self.answers, self.users = list(answers), []

    def __call__(self, system, user, max_tokens=8000):
        assert system.startswith(agent.SYSTEM[:60])
        self.users.append(user)
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


@pytest.fixture
def script(monkeypatch):
    def use(*answers):
        s = Script(*answers)
        monkeypatch.setattr(llm, "ask", s)
        return s
    return use


# ---------------------------------------------------------------- rerun set

def test_style_change_reruns_only_picture_branch():
    new = with_params(n4={"style": "夜景，写实摄影"})
    assert agent.rerun(MV, new) == ["n4", "n5", "n7", "n8", "n9", "n10"]
    assert assistant.rerun_nodes(MV, new) == agent.rerun(MV, new)


def test_input_change_reruns_everything():
    new = with_params(n1={"text": "雪山之巅"})
    assert agent.rerun(MV, new) == [n["id"] for n in MV["nodes"]]


def test_review_note_and_title_do_not_ripple():
    new = with_params(n9={"note": "看画面是否为夜景"}, n10={"title": "MV 夜景"})
    assert agent.rerun(MV, new) == ["n9", "n10"]
    new = with_params(n9={"note": "x"}, n6={"style": "慢镜头"})
    assert agent.rerun(MV, new) == ["n6", "n7", "n8", "n9", "n10"]


def test_default_valued_param_is_not_a_change():
    new = with_params(n2={"genre": ""}, n7={"seconds": 5})  # equal to the declared defaults
    assert agent.rerun(MV, new) == []


def test_edge_change_reruns_target_and_downstream():
    new = copy.deepcopy(MV)
    new["edges"] = [e for e in new["edges"] if e["to"] != "n7.first_frame"]
    assert agent.rerun(MV, new) == ["n7", "n8", "n9", "n10"]


# ---------------------------------------------------------------- edit + narrowing

def test_narrow_edit_needs_no_retry(script):
    s = script(with_params(n4={"style": "夜景"}))
    res = agent.edit(MV, "改成夜景")
    assert res["valid"] and not res["narrowed"] and len(s.users) == 1
    assert "closest" in s.users[0].lower() and "input.text ONLY" in s.users[0]
    assert res["diff"]["changed_params"][0].startswith("n4.style")


def test_input_edit_gets_one_retry_and_narrower_answer_is_kept(script):
    wide = with_params(n1={"text": "夏夜的海边，夜景"})
    narrow = with_params(n4={"style": "夜景，电影感"})
    s = script(wide, narrow)
    res = agent.edit(MV, "改成夜景")
    assert len(s.users) == 2 and "n1" in s.users[1] and "input node" in s.users[1]
    assert res["narrowed"] and res["workflow"] == narrow
    assert agent.rerun(MV, res["workflow"]) == ["n4", "n5", "n7", "n8", "n9", "n10"]


def test_subject_change_keeps_input_edit(script):
    snow = with_params(n1={"text": "雪山之巅"})
    s = script(snow, snow)  # the model confirms the input change is needed
    res = agent.edit(MV, "主题改成雪山")
    assert len(s.users) == 2 and not res["narrowed"] and res["workflow"] == snow


def test_invalid_or_wider_retry_is_ignored(script):
    wide = with_params(n1={"text": "x"})
    script(wide, "not json at all")
    res = agent.edit(MV, "改成夜景")
    assert res["valid"] and not res["narrowed"] and res["workflow"] == wide
    bad = copy.deepcopy(MV)
    bad["edges"] = bad["edges"][1:]  # n2.theme unconnected: does not validate
    script(wide, bad)
    assert agent.edit(MV, "改成夜景")["workflow"] == wide
    script(wide, copy.deepcopy(MV))  # retry that changes nothing is not an edit
    assert agent.edit(MV, "改成夜景")["workflow"] == wide


def test_independent_gpu_branches_trigger_retry(script):
    both = with_params(n2={"genre": "钢琴"}, n4={"style": "钢琴演奏"})
    music = with_params(n2={"genre": "钢琴"})
    s = script(both, music)
    res = agent.edit(MV, "音乐换成钢琴")
    assert "independent branches" in s.users[1] and res["narrowed"]
    assert agent.rerun(MV, res["workflow"]) == ["n2", "n3", "n8", "n9", "n10"]


def test_at_most_one_extra_call(script):
    wide = with_params(n1={"text": "x"})
    s = script(wide, wide, wide, wide)
    agent.edit(MV, "改成夜景")
    assert len(s.users) == 2


# ---------------------------------------------------------------- chat reply

def test_rerun_text_names_reruns_and_cache():
    new = with_params(n4={"style": "夜景"})
    t = assistant.rerun_text(new, agent.rerun(MV, new))
    regen, cached = t.split("；直接用缓存：")
    assert "n4" in regen and "n5" in regen and "n7" in regen and "n3" not in regen
    assert "n2" in cached and "n3" in cached and "n6" in cached
    assert "n9" not in t and "n1" not in t  # review/save/input are not cacheable work
    new = with_params(n9={"note": "x"})
    assert "无（" in assistant.rerun_text(new, agent.rerun(MV, new))


def test_chat_edit_message_reports_cache_and_narrowing(script, monkeypatch):
    from app.workflows import engine
    monkeypatch.setattr(engine, "start", lambda wf, parent="": {"id": "r2"})
    monkeypatch.setattr(engine, "read_run", lambda rid: {"status": "done"})
    script(with_params(n1={"text": "夜景的海边"}), with_params(n4={"style": "夜景"}))
    s = {"workflow": copy.deepcopy(MV), "run_id": "r1", "runs": ["r1"], "messages": []}
    assistant._do_edit(s, {"instruction": "改成夜景"})
    m = s["messages"][-1]
    assert m["kind"] == "edit" and m["narrowed"] and s["run_id"] == "r2"
    assert m["rerun"] == ["n4", "n5", "n7", "n8", "n9", "n10"]
    assert m["cached"] == ["n1", "n2", "n3", "n6"]
    assert "直接用缓存" in m["text"] and "收窄" in m["text"]
