"""plan.py loops with a fake LLM: the plan retry loop feeds validator errors back, the asset
rewrite loop and the shape fix stop at their round limits, model_plan, the node and the API."""
import copy
import json
import sys
from pathlib import Path

import pytest

from app.previs import plan, plan_node

EX = Path(__file__).resolve().parents[1] / "app" / "previs" / "examples" / "four_stroke"
PLAN = json.loads((EX / "plan.json").read_text())
SCENES = json.loads((EX / "scenes.json").read_text())
SPEC = PLAN["assets"][0]["spec"]


class FakeLLM:
    """Returns queued answers in order (the last one repeats) and records every call."""

    def __init__(self, *answers):
        self.answers = [a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)
                        for a in answers]
        self.calls = []

    def __call__(self, system, user, *rest):  # ask(s, u, max_tokens) / ask_images(s, u, imgs, n)
        self.calls.append({"system": system, "user": user,
                           "images": rest[0] if len(rest) == 2 else None})
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


def broken_plan():
    p = copy.deepcopy(PLAN)
    p["shots"].pop(2)  # missing_shot s03
    p["shots"][0]["camera"]["keys"][1]["at"] = {"word": "涡轮"}  # anchor_not_found
    return p


# ------------------------------------------------------------- previs_plan


def test_plan_first_round_ok():
    llm = FakeLLM("```json\n" + json.dumps(PLAN, ensure_ascii=False) + "\n```")
    meta = {}
    out = plan.previs_plan(SCENES, "四冲程发动机", "16:9", ask=llm, meta=meta)
    assert out == PLAN and meta["rounds"] == 1 and len(llm.calls) == 1
    sys_p, user = llm.calls[0]["system"], llm.calls[0]["user"]
    assert "four_stroke" in sys_p and "单缸发动机里最重要" in sys_p  # example plan + scenes
    assert "PartSpec" in sys_p and "image3d" in sys_p and "(none)" in sys_p  # types, kinds, library
    assert "s01, s02, s03" in user and "16:9" in user


def test_plan_retry_feeds_errors_back():
    llm = FakeLLM(broken_plan(), PLAN)
    meta = {}
    out = plan.previs_plan(SCENES, "t", "16:9", ask=llm, meta=meta)
    assert out == PLAN and meta["rounds"] == 2
    retry = llm.calls[1]["user"]
    assert "[missing_shot]" in retry and "s03" in retry and "[anchor_not_found]" in retry
    assert "涡轮" in retry and "Your previous answer" in retry
    assert meta["trail"][0]["errors"] == 2 and meta["trail"][1]["errors"] == 0


def test_plan_bad_json_then_ok():
    llm = FakeLLM("I think the plan is ...", PLAN)
    assert plan.previs_plan(SCENES, "t", ask=llm) == PLAN
    assert "[format]" in llm.calls[1]["user"]


def test_plan_gives_up_after_three_rounds():
    llm = FakeLLM(broken_plan())
    with pytest.raises(plan.PlanError) as e:
        plan.previs_plan(SCENES, "t", ask=llm)
    assert len(llm.calls) == plan.MAX_ROUNDS == 3
    assert e.value.rounds == 3 and {i["code"] for i in e.value.issues} >= {"missing_shot"}


def test_warnings_do_not_trigger_a_rewrite():
    p = copy.deepcopy(PLAN)
    p["sets"][0]["objects"][0]["scale"] = 1.5
    llm, meta = FakeLLM(p), {}
    plan.previs_plan(SCENES, "t", ask=llm, meta=meta)
    assert len(llm.calls) == 1 and [i["code"] for i in meta["issues"]] == ["scale_not_1"]


def test_library_assets_reach_the_prompt():
    lib = [{"id": "asset_20261125-010203-abcdef", "name": "齿轮", "size": [0.2, 0.2, 0.05],
            "rig": {"dofs": {"turn": {"min": 0, "max": 360, "default": 0}}},
            "anchors": {"hub": [0, 0.1, 0]}, "parts": [{"id": "body"}]}]
    llm = FakeLLM(PLAN)
    plan.previs_plan(SCENES, "t", library_assets=lib, ask=llm)
    s = llm.calls[0]["system"]
    assert "asset_20261125-010203-abcdef" in s and "齿轮" in s and '"turn"' in s


# ------------------------------------------------------------- asset rewrite (A's builder)

ISSUE = {"code": "dims_mismatch", "severity": "error", "message": "高 0.52 与实际 0.60 不符",
         "where": "dims"}


class FakeBuild:
    """Fails the first `fails` calls with A-style issues, then returns a record."""

    def __init__(self, fails):
        self.fails, self.calls = fails, []

    def __call__(self, spec):
        self.calls.append(copy.deepcopy(spec))
        if len(self.calls) <= self.fails:
            e = RuntimeError("build failed")
            e.issues = [ISSUE]
            raise e
        return {"id": f"asset_20261125-000000-00000{len(self.calls)}", "size": [0.3, 0.52, 0.22]}


def fixed_spec(h=0.52):
    s = copy.deepcopy(SPEC)
    s["dims"]["h"] = h
    return s


def test_rewrite_asset_stops_when_build_passes():
    build, llm = FakeBuild(fails=0), FakeLLM(fixed_spec())
    r = plan.rewrite_asset(SPEC, [ISSUE], build=build, ask=llm)
    assert r["ok"] and r["rounds"] == 1 and len(llm.calls) == 1 and len(build.calls) == 1
    assert "[dims_mismatch]" in llm.calls[0]["user"] and "crank" in llm.calls[0]["user"]


def test_rewrite_asset_caps_at_two_rounds():
    build, llm = FakeBuild(fails=99), FakeLLM(fixed_spec())
    r = plan.rewrite_asset(SPEC, [ISSUE], build=build, ask=llm)
    assert not r["ok"] and r["rounds"] == 2 == plan.MAX_ASSET_ROUNDS
    assert len(llm.calls) == 2 and len(build.calls) == 2 and r["record"] is None


def test_rewrite_asset_skips_build_on_static_errors():
    bad = fixed_spec()
    bad["parts"][0]["parent"] = "nope"
    build, llm = FakeBuild(fails=0), FakeLLM(bad, fixed_spec())
    r = plan.rewrite_asset(SPEC, [ISSUE], build=build, ask=llm)
    assert r["ok"] and r["rounds"] == 2 and len(build.calls) == 1
    assert "[unknown_parent]" in llm.calls[1]["user"]


def test_rewrite_keeps_the_asset_id():
    other = fixed_spec()
    other["id"] = "renamed"
    r = plan.rewrite_asset(SPEC, [ISSUE], build=FakeBuild(0), ask=FakeLLM(other))
    assert r["spec"]["id"] == "four_stroke"


def test_default_build_without_part_a(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.previs.assets_build", None)  # import fails
    with pytest.raises(plan.BuildUnavailable, match="assets_build"):
        plan.default_build(SPEC)
    with pytest.raises(plan.BuildUnavailable):  # not swallowed into a build issue
        plan.build_assets(copy.deepcopy(PLAN))


def test_build_result_with_issue_list():
    rec, issues = plan.call_build(lambda s: {"id": "x", "issues": [ISSUE]}, SPEC)
    assert rec is None and issues == [ISSUE]
    rec, issues = plan.call_build(lambda s: (_ for _ in ()).throw(ValueError("boom")), SPEC)
    assert rec is None and issues[0]["code"] == "build_failed" and "boom" in issues[0]["message"]


# ------------------------------------------------------------- shape check


@pytest.fixture
def render(tmp_path):
    calls = []

    def fn(record, spec):
        calls.append(record["id"])
        out = []
        for k in range(4):
            p = tmp_path / f"{record['id']}_{k}.jpg"
            p.write_bytes(b"\xff\xd8fake")
            out.append(p)
        return out
    fn.calls = calls
    return fn


def test_shape_check_parses_reply(render):
    vis = FakeLLM({"ok": False, "problems": ["the crankcase is too tall"]})
    r = plan.shape_check("发动机", render({"id": "a"}, SPEC), size=[0.3, 0.52, 0.22],
                         ask_images=vis)
    assert r == {"ok": False, "problems": ["the crankcase is too tall"]}
    assert len(vis.calls[0]["images"]) == 4 and "0.52" in vis.calls[0]["user"]


def test_shape_fix_one_rewrite_then_recheck(render):
    vis = FakeLLM({"ok": False, "problems": ["piston sticks out"]}, {"ok": True, "problems": []})
    llm, build = FakeLLM(fixed_spec()), FakeBuild(0)
    s = plan.shape_fix(SPEC, {"id": "a0"}, render=render, build=build, ask=llm, ask_images=vis)
    assert s["rewritten"] and s["after"]["ok"] and len(llm.calls) == 1 and len(vis.calls) == 2
    assert "piston sticks out" in llm.calls[0]["user"]


def test_shape_fix_never_rewrites_twice(render):
    vis = FakeLLM({"ok": False, "problems": ["looks like a box"]})
    llm, build = FakeLLM(fixed_spec()), FakeBuild(0)
    s = plan.shape_fix(SPEC, {"id": "a0"}, render=render, build=build, ask=llm, ask_images=vis)
    assert s["rewritten"] and s["after"]["ok"] is False
    assert len(llm.calls) == 1 and len(build.calls) == 1 and len(vis.calls) == 2


def test_shape_fix_ok_needs_no_rewrite(render):
    vis, llm = FakeLLM({"ok": True, "problems": []}), FakeLLM(fixed_spec())
    s = plan.shape_fix(SPEC, {"id": "a0"}, render=render, build=FakeBuild(0), ask=llm,
                       ask_images=vis)
    assert not s["rewritten"] and not llm.calls and len(vis.calls) == 1


def test_build_assets_writes_plan_check(tmp_path, render):
    p = copy.deepcopy(PLAN)
    build = FakeBuild(fails=1)
    vis = FakeLLM({"ok": True, "problems": []})
    rep = plan.build_assets(p, SCENES, build=build, ask=FakeLLM(fixed_spec()), render=render,
                            ask_images=vis, check_path=tmp_path / "previs" / "plan_check.json")
    e = rep["assets"]["four_stroke"]
    assert e["ok"] and e["build_rounds"] == 1 and e["first_issues"] == [ISSUE]
    assert e["shape"]["check"]["ok"] and rep["plan_issues"] == []
    assert rep["asset_ids"] == {"@four_stroke": "asset_20261125-000000-000002"}
    saved = json.loads((tmp_path / "previs" / "plan_check.json").read_text())
    assert saved["assets"]["four_stroke"]["build_rounds"] == 1


def test_default_render_cuts_four_stills(tmp_path, monkeypatch):
    import subprocess as sp

    from app import pipeline
    from app.assets import store
    mp4 = tmp_path / "turntable.mp4"  # 4 s test pattern, like A's turntable
    sp.run([pipeline.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
            "-t", "4", str(mp4)], check=True)
    monkeypatch.setattr(store, "turntable_path", lambda aid: mp4, raising=False)
    paths = plan.default_render({"id": "asset_x", "turntable": True}, SPEC)
    assert len(paths) == 4 and all(p.stat().st_size > 0 for p in paths)
    monkeypatch.setattr(store, "turntable_path", lambda aid: None, raising=False)
    with pytest.raises(RuntimeError, match="turntable"):
        plan.default_render({"id": "asset_x", "turntable": False}, SPEC)


def test_build_assets_records_missing_turntable(tmp_path):
    def no_turntable(record, spec):
        raise RuntimeError("资产没有转台视频")
    rep = plan.build_assets(copy.deepcopy(PLAN), build=FakeBuild(0), render=no_turntable,
                            ask_images=FakeLLM({"ok": True}))
    assert rep["assets"]["four_stroke"]["ok"] and "转台" in rep["assets"]["four_stroke"]["shape"]["error"]


# ------------------------------------------------------------- model_plan, node, API


def test_model_plan_retries_static_errors():
    bad = copy.deepcopy(SPEC)
    bad["drivers"]["rod_ang"] = "nope(crank)"
    llm = FakeLLM(bad, SPEC)
    r = plan.model_plan("一台单缸四冲程发动机", ask=llm)
    assert r["rounds"] == 2 and r["issues"] == [] and r["spec"]["id"] == "four_stroke"
    assert "[expr_error]" in llm.calls[1]["user"] and "单缸四冲程" in llm.calls[0]["user"]
    assert "four_stroke" in llm.calls[0]["system"]  # the example spec is in the prompt


def test_model_plan_caps_at_three():
    bad = copy.deepcopy(SPEC)
    bad["parts"] = []
    llm = FakeLLM(bad)
    r = plan.model_plan("x", ask=llm)
    assert r["rounds"] == 3 and len(llm.calls) == 3 and r["issues"]


class Ctx:
    def __init__(self, d):
        self.dir, self.meta = d, {}


def test_node_declaration_and_run(tmp_path, monkeypatch):
    from app import llm as llm_mod
    nodes = {}
    d = plan_node.register(nodes)
    assert d["type"] == "agent.previs_plan" and d["inputs"] == {"scenes": "json"}
    assert d["outputs"] == {"plan": "json"} and d["runner"] == "agent" and "ratio" in d["params"]
    fake = FakeLLM(PLAN)
    monkeypatch.setattr(llm_mod, "ask", lambda s, u, max_tokens=0: fake(s, u, max_tokens))
    ctx = Ctx(tmp_path / "n1")
    out = d["run"]({"scenes": {"scenes": SCENES, "topic": "发动机"}}, {"ratio": "9:16"}, ctx)
    assert out["plan"] == PLAN and ctx.meta["rounds"] == 1
    assert json.loads((tmp_path / "n1" / "plan.json").read_text()) == PLAN
    assert "9:16" in fake.calls[0]["user"] and "发动机" in fake.calls[0]["user"]


def test_node_reads_job_scenes(tmp_path, monkeypatch):
    from app import llm as llm_mod
    from app import pipeline
    job = tmp_path / "job1"
    job.mkdir()
    (job / "scenes.json").write_text(json.dumps(SCENES, ensure_ascii=False))
    monkeypatch.setattr(pipeline, "job_dir", lambda jid: job)
    monkeypatch.setattr(pipeline, "read_job", lambda jid: {"topic": "四冲程"})
    monkeypatch.setattr(llm_mod, "ask", lambda s, u, max_tokens=0: json.dumps(PLAN))
    out = plan_node.register({})["run"]({"scenes": {"job_id": "job1"}}, {}, Ctx(tmp_path / "n"))
    assert out["plan"] == PLAN
    assert json.loads((job / "previs" / "plan.json").read_text()) == PLAN


def test_api_model_plan(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.previs import plan_api
    app = FastAPI()
    app.include_router(plan_api.router)
    monkeypatch.setattr(plan, "_ask", FakeLLM(SPEC))
    c = TestClient(app)
    r = c.post("/api/assets/model/plan", json={"description": "一台单缸发动机"})
    assert r.status_code == 200
    j = r.json()
    assert j["spec"]["id"] == "four_stroke" and j["issues"] == [] and j["rounds"] == 1
    assert c.post("/api/assets/model/plan", json={"description": ""}).status_code == 422

    def boom(*a):
        raise ValueError("model output was cut off at 8000 tokens")
    monkeypatch.setattr(plan, "_ask", boom)
    assert c.post("/api/assets/model/plan", json={"description": "x"}).status_code == 502
