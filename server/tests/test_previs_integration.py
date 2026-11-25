"""3D previs integration: routes and nodes wired into the studio, renderer=previs3d jobs, the
previs job steps (assets.json, shot check rewrite loop + orbit fallback), E's normalize on
A's real builder, the task-center source and the GL flag. Uses the real Node builder and
shot compiler; Opus calls and the turntable render are stubbed."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, mesh_api, pipeline
from app.assets import store
from app.previs import assets_build, job as pjob, plan as pplan, shots
from app.remotion import render
from app.tasks import collect
from app.workflows import nodes, validate

EX = Path(__file__).resolve().parents[1] / "app" / "previs" / "examples" / "four_stroke"
PLAN = json.loads((EX / "plan.json").read_text())
SCENES = json.loads((EX / "scenes.json").read_text())
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fake_turntable(monkeypatch):
    def fake(glb, meta, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        (out_dir / "turntable.mp4").write_bytes(b"\x00\x00\x00\x18ftypisomfake")
        return {"thumb.jpg": out_dir / "thumb.jpg", "turntable.mp4": out_dir / "turntable.mp4"}
    monkeypatch.setattr(assets_build, "turntable", fake)


@pytest.fixture
def no_run(monkeypatch):
    started = []
    monkeypatch.setattr(pipeline, "_run", lambda jid: started.append(jid))
    return started


def test_nodes_registered_and_model_port():
    for t in ("asset.build", "previs.render", "agent.previs_plan", "asset.image3d"):
        assert t in nodes.NODES, t
    assert "model" in nodes.PORT_TYPES
    validate.check_types()
    assert "previs3d" in nodes.NODES["explainer.video"]["params"]["renderer"]["enum"]


def test_template_workflow_validates():
    from app.workflows import store as wf_store
    wf = wf_store.get("wf_previs_explainer")
    assert wf and validate.validate(wf) == []


def test_routes_mounted():
    paths = set(main.app.openapi()["paths"])
    for p in ("/api/assets/model/build", "/api/assets/model/upload", "/api/assets/model/plan",
              "/api/assets/model/image3d", "/api/jobs/{job_id}/previs", "/api/mesh/status",
              "/api/assets/{asset_id}/turntable"):
        assert p in paths, p
    assert client.get("/api/assets?kind=model").status_code == 200


def test_job_previs3d_fixed_scenes_and_plan(no_run):
    r = client.post("/api/jobs", json={"topic": "四冲程", "renderer": "previs3d", "music": "none",
                                       "scenes": SCENES, "previs_plan": PLAN})
    assert r.status_code == 201, r.text
    j = r.json()
    assert [s["key"] for s in j["steps"]] == [k for k, _ in pipeline.PREVIS_STEPS]
    d = pipeline.job_dir(j["id"])
    assert json.loads((d / "scenes.json").read_text())[1]["narration"] == SCENES[1]["narration"]
    assert json.loads((d / "previs" / "plan.json").read_text()) == PLAN
    assert j["fixed_scenes"] and j["fixed_plan"] and no_run == [j["id"]]


@pytest.mark.parametrize("body", [
    {"renderer": "html", "scenes": SCENES, "previs_plan": PLAN},  # plan needs previs3d
    {"renderer": "previs3d", "previs_plan": PLAN},  # plan needs fixed scenes
    {"renderer": "previs3d", "scenes": [SCENES[0], SCENES[0]]},  # duplicate ids
    {"renderer": "previs3d", "scenes": [{"id": "../x", "narration": "a"}]},
    {"renderer": "blender"},
])
def test_job_rejects(no_run, body):
    assert client.post("/api/jobs", json={"topic": "四冲程", **body}).status_code == 422
    assert no_run == []


def test_model_records_carry_turntable_url_and_tasks_have_mesh(tmp_path):
    rec = assets_build.build(PLAN["assets"][0]["spec"])
    lst = client.get("/api/assets?kind=model").json()
    got = next(a for a in lst if a["id"] == rec["id"])
    assert got["turntable_url"] == f"/api/assets/{rec['id']}/turntable"
    assert got["size"] == pytest.approx([0.3, 0.52, 0.22], rel=0.02)
    assert "mesh" in collect.KINDS and "mesh" in collect.SOURCES


def test_image3d_normalize_uses_part_a(tmp_path):
    """E's default normalize is A's real assets_build.normalize (signature checked for real)."""
    d = tmp_path / "b"
    d.mkdir()
    (d / "spec.json").write_text(json.dumps(PLAN["assets"][0]["spec"]))
    assets_build._cli(["build-asset", d / "spec.json", d / "file.glb", d / "meta.json"])
    mesh_api.set_normalize(None)
    rec = mesh_api.normalize(d / "file.glb", {"axis": "h", "metres": 0.3},
                             {"type": "image3d", "model": "hunyuan3d-2mini", "seed": 42,
                              "target": {"axis": "h", "metres": 0.3}}, "猫", None)
    assert rec["kind"] == "model" and rec["size"][1] == pytest.approx(0.3, rel=0.02)
    assert rec["source"]["type"] == "image3d"


def test_render_passes_gl(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(render, "_run", lambda args, *a, **k: seen.append(args))
    monkeypatch.setattr(render, "_fix_audio_priming", lambda *a: None)
    render.render(tmp_path, tmp_path / "o.mp4", None, gl="swangle")
    render.render(tmp_path, tmp_path / "o.mp4", None)
    assert "--gl=swangle" in seen[0] and not any(a.startswith("--gl") for a in map(str, seen[1]))


# ---------------------------------------------------------------- previs job steps

def make_job(tmp_path, plan=None) -> tuple[dict, Path]:
    d = tmp_path / "job"
    (d / "previs").mkdir(parents=True)
    (d / "previs" / "plan.json").write_text(json.dumps(plan or PLAN, ensure_ascii=False))
    for f in ("words.json", "durations.json", "scenes.json"):
        shutil.copy(EX / f, d / f)
    job = {"id": "j", "topic": "四冲程", "ratio": "16:9", "scenes": SCENES, "captions": True}
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False))
    return job, d


def test_plan_and_assets_step(tmp_path):
    job, d = make_job(tmp_path)
    r = pjob.plan_step(job, d)
    assert r["fixed"] and not [i for i in r["issues"] if i["severity"] == "error"]
    ids = pjob.assets_step(job, d, r["plan"], shape=False)
    aid = ids["@four_stroke"]
    amap = json.loads((d / "previs" / "assets.json").read_text())
    assert amap["@four_stroke"] == {"dir": str(store.asset_path(aid).parent), "id": aid}
    res = shots.compile_all(d)
    assert all(v["errors"] == 0 for v in res.values())
    assert {json.loads(Path(v["resolved"]).read_text())["objects"][0]["asset"] for v in res.values()} == {aid}


@pytest.fixture
def built_job(tmp_path):
    job, d = make_job(tmp_path)
    pjob.assets_step(job, d, copy.deepcopy(PLAN), shape=False)
    return job, d


def _break_s02(d):
    plan = json.loads((d / "previs" / "plan.json").read_text())
    good = copy.deepcopy(plan["shots"][1])
    plan["shots"][1]["camera"]["keys"][0]["pos"] = [0, 0.12, 0]  # inside the crankcase
    (d / "previs" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    return good


def test_shot_rewrite_loop_fixes_a_bad_shot(built_job):
    job, d = built_job
    good = _break_s02(d)
    calls = []

    def ask(system, user, n):
        calls.append(user)
        return json.dumps(good, ensure_ascii=False)
    out = pjob.shots_step(job, d, ask=ask)
    assert len(calls) == 1 and "camera_inside" in calls[0]
    assert out["s02"]["rounds"] == 1 and not out["s02"]["fallback"] and out["s02"]["errors"] == 0
    assert json.loads((d / "previs" / "shot_fix.json").read_text())["s02"]["first_errors"]


def test_shot_rewrite_gives_up_then_orbit(built_job):
    job, d = built_job
    _break_s02(d)
    calls = []

    def ask(system, user, n):
        calls.append(user)
        plan = json.loads((d / "previs" / "plan.json").read_text())
        return json.dumps(plan["shots"][1], ensure_ascii=False)  # the same broken shot
    out = pjob.shots_step(job, d, ask=ask)
    assert len(calls) == pjob.SHOT_ROUNDS
    assert out["s02"]["fallback"] and out["s02"]["errors"] == 0
    s02 = next(s for s in json.loads((d / "previs" / "plan.json").read_text())["shots"] if s["id"] == "s02")
    assert all("orbit" in k for k in s02["camera"]["keys"]) and s02["actions"] == []
    assert json.loads((d / "previs" / "status.json").read_text())["s02"]["status"] == "queued"


def test_fixed_plan_with_errors_is_refused(tmp_path):
    bad = copy.deepcopy(PLAN)
    bad["shots"] = bad["shots"][:2]  # s03 has no shot
    job, d = make_job(tmp_path, bad)
    with pytest.raises(ValueError, match="previs 规划"):
        pjob.plan_step(job, d)


def test_plan_step_calls_opus_without_fixed_plan(tmp_path, monkeypatch):
    job, d = make_job(tmp_path)
    (d / "previs" / "plan.json").unlink()
    monkeypatch.setattr(pplan, "_ask", lambda s, u, n: json.dumps(PLAN, ensure_ascii=False))
    r = pjob.plan_step(job, d)
    assert not r["fixed"] and r["rounds"] == 1
    assert json.loads((d / "previs" / "plan.json").read_text()) == PLAN
