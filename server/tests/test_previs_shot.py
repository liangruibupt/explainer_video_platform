"""Part B of 3D previs: shot compile + checks (shots.py), project writer (project.py), HTTP
routes (api.py) and the previs.render node (node.py). Uses the four_stroke fixture and the
real asset builder (part A's build-asset, the same one jobs use); the test-only builder in
js/shot/testkit.ts is only used by the JS unit tests."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import pipeline
from app.previs import api, node, project, shots
from app.remotion import render

EX = Path(__file__).resolve().parents[1] / "app" / "previs" / "examples" / "four_stroke"


@pytest.fixture(scope="module")
def asset_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("asset_four")
    (d / "spec.json").write_text(json.dumps(json.loads((EX / "plan.json").read_text())["assets"][0]["spec"]))
    r = subprocess.run(["node", str(shots.bundle()), "build-asset", str(d / "spec.json"),
                        str(d / "file.glb"), str(d / "meta.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def make_job(asset_dir: Path, root: Path | None = None) -> Path:
    jid = "20261125-000000-" + uuid.uuid4().hex[:6]
    d = (root or pipeline.DATA / "jobs") / jid
    (d / "previs").mkdir(parents=True)
    shutil.copy(EX / "plan.json", d / "previs" / "plan.json")
    for f in ("words.json", "durations.json", "scenes.json"):
        shutil.copy(EX / f, d / f)
    (d / "previs" / "assets.json").write_text(json.dumps({"@four_stroke": str(asset_dir)}))
    (d / "job.json").write_text(json.dumps({"id": jid, "ratio": "16:9",
                                            "scenes": json.loads((EX / "scenes.json").read_text())}))
    return d


def test_compile_all_fixture(asset_dir, tmp_path):
    d = make_job(asset_dir, tmp_path)
    res = shots.compile_all(d)
    assert set(res) == {"s01", "s02", "s03"}
    for sid, r in res.items():
        assert r["errors"] == 0, r["report"]
        shot = json.loads(Path(r["resolved"]).read_text())
        assert shot["frames"] == json.loads((EX / "durations.json").read_text())[int(sid[-1]) - 1]
        assert len(shot["camera"]) == shot["frames"]
        assert shot["objects"][0]["asset"] == "four_stroke"
        assert shot["objects"][0]["glb"] == "previs/four_stroke.glb"
        png = Path(r["plan_png"])
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size > 5000
        assert (d / "previs" / f"{sid}.plan.svg").exists()
        assert json.loads((d / "previs" / f"{sid}.report.json").read_text()) == r["report"]
    st = json.loads((d / "previs" / "status.json").read_text())
    assert st["s02"]["errors"] == 0


def test_word_anchors_within_one_frame(asset_dir, tmp_path):
    d = make_job(asset_dir, tmp_path)
    shots.compile_all(d)
    words = json.loads((EX / "words.json").read_text())
    dur = json.loads((EX / "durations.json").read_text())
    starts = {"s01": 0, "s02": dur[0] / 30, "s03": (dur[0] + dur[1]) / 30}
    seen = set()
    for sid in ("s02", "s03"):
        shot = json.loads((d / "previs" / f"{sid}.json").read_text())
        ws = [w for w in words if w["scene"] == sid]
        text = "".join(w["text"] for w in ws)
        for a in shot["anchors"]:
            w = a["anchor"].get("word")
            if not w:
                continue
            t = ws[text.index(w)]["start"]
            want = (t - starts[sid] + a["anchor"].get("offset", 0)) * 30
            assert abs(a["frame"] - want) <= 1, (sid, w, a["frame"], want)
            seen.add(w)
    assert {"进气", "压缩", "点火", "做功", "排气"} <= seen


def test_asset_map(tmp_path):
    d = tmp_path / "j"
    (d / "previs").mkdir(parents=True)
    plan = {"assets": [{"id": "lib1", "kind": "library", "asset": "asset_20261125-000000-abcdef"}],
            "sets": [{"id": "s", "objects": [{"id": "a", "asset": "@lib1"}, {"id": "b", "asset": "asset_20261125-000000-123456"},
                                             {"id": "c", "asset": "@given"}]}]}
    (d / "previs" / "assets.json").write_text(json.dumps({"@given": {"dir": "/x", "id": "asset_g"}}))
    m = shots.asset_map(d, plan)
    assert m["@lib1"]["id"] == "asset_20261125-000000-abcdef"
    assert m["@lib1"]["dir"].endswith("assets/asset_20261125-000000-abcdef")
    assert m["asset_20261125-000000-123456"]["id"] == "asset_20261125-000000-123456"
    assert m["@given"] == {"dir": "/x", "id": "asset_g"}


def test_bad_shot_reports_errors(asset_dir, tmp_path):
    d = make_job(asset_dir, tmp_path)
    plan = json.loads((d / "previs" / "plan.json").read_text())
    s = plan["shots"][1]
    s["camera"]["keys"][0]["pos"] = [0, 0.12, 0]  # inside the crankcase
    s["actions"].append({"op": "pose", "target": "eng", "dof": "nope", "to": 1, "at": {"word": "没有这个词"}})
    (d / "previs" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    r = shots.compile_shot(d, "s02")
    codes = {i["code"] for i in r["report"]}
    assert {"camera_inside", "unknown_dof", "anchor_not_found"} <= codes
    assert r["errors"] >= 3
    with pytest.raises(ValueError, match="check error"):
        shots.render_shot(d, "s02")


def test_write_project(asset_dir, tmp_path):
    d = make_job(asset_dir, tmp_path)
    shots.compile_all(d)
    proj = shots.write_project(d)
    glb = proj / "public" / "previs" / "four_stroke.glb"
    assert glb.exists() and not glb.is_symlink()
    assert glb.read_bytes()[:4] == b"glTF"
    for sid in ("s01", "s02", "s03"):
        tsx = (proj / "src" / "scenes" / f"{sid}.tsx").read_text()
        assert "export const PREVIS = true" in tsx and "PrevisShot" in tsx
        assert (proj / "src" / "data" / "previs" / f"{sid}.json").exists()
    assert '"label_0": "活塞"' in (proj / "src" / "scenes" / "s01.tsx").read_text()
    assert not (proj / "src" / "scenes" / "s01.tsx").read_text().count("TitleCard")
    idx = (proj / "src" / "scenes" / "index.ts").read_text()
    assert all(f'"{s}": {s}' in idx for s in ("s01", "s02", "s03"))
    tl = json.loads((proj / "src" / "data" / "timeline.json").read_text())
    assert [s["duration"] for s in tl["scenes"]] == json.loads((EX / "durations.json").read_text())
    assert tl["scenes"][0]["card"] == "previs/3d"
    assert "Shot-" in (proj / "src" / "Root.tsx").read_text()
    # GL backend is passed per render (render.render(gl="swangle")), the template config stays
    assert (proj / "remotion.config.ts").read_text() == (render.TEMPLATE / "remotion.config.ts").read_text()
    assert (proj / "src" / "lib" / "previs" / "PrevisShot.tsx").exists()
    assert project.comp_id("s01") == "Shot-s01" and project.comp_id("a_b") == "Shot-a-b"


def test_scene_module_defaults_and_schema():
    shot = {"style": {"shadows": False}, "set": {"ground": {"kind": "plain"}, "background": "#111111"},
            "overlays": [{"kind": "label", "text": "活塞"}, {"kind": "dimension", "text": "90 mm"}]}
    dft = project.defaults(shot)
    assert dft["label_0"] == "活塞" and dft["dim_1"] == "90 mm"
    assert dft["shadows"] is False and dft["grid"] is False and dft["clay"] == "#c9c4bb"
    keys = [f["key"] for f in project.schema(shot)]
    assert keys[:2] == ["label_0", "dim_1"] and "accent4" in keys
    assert set(keys) == set(dft)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_api_get_put_render(asset_dir, client, monkeypatch):
    d = make_job(asset_dir)
    jid = d.name
    shots.compile_all(d)
    r = client.get(f"/api/jobs/{jid}/previs")
    assert r.status_code == 200
    body = r.json()
    assert [s["scene"] for s in body["shots"]] == ["s01", "s02", "s03"]
    s2 = body["shots"][1]
    assert s2["resolved_url"] and s2["plan_png"] and s2["clip_url"] is None
    assert s2["status"] == "queued" and isinstance(s2["report"]["issues"], list)
    assert client.get(s2["plan_png"]).headers["content-type"] == "image/png"
    assert client.get(f"/api/jobs/{jid}/previs/files/..%2Fjob.json").status_code == 404
    assert client.get("/api/jobs/bad/previs").status_code == 404
    assert client.get(f"/api/jobs/{jid}/previs/s09/clip").status_code == 404
    # PUT: validation, then a shot that puts the camera inside the engine
    assert client.put(f"/api/jobs/{jid}/previs/s02", json={"shot": {"id": "s03"}}).status_code == 422
    assert client.put(f"/api/jobs/{jid}/previs/s02", json={"nope": 1}).status_code == 422
    shot = json.loads((d / "previs" / "plan.json").read_text())["shots"][1]
    shot["camera"]["keys"][0]["pos"] = [0, 0.12, 0]
    r = client.put(f"/api/jobs/{jid}/previs/s02", json={"shot": shot})
    assert r.status_code == 200 and r.json()["errors"] >= 1
    assert "camera_inside" in {i["code"] for i in r.json()["report"]["issues"]}
    s2 = client.get(f"/api/jobs/{jid}/previs").json()["shots"][1]
    assert s2["status"] == "blocked"
    assert "camera_inside" in {i["code"] for i in s2["report"]["issues"]}
    saved = json.loads((d / "previs" / "plan.json").read_text())["shots"][1]
    assert saved["camera"]["keys"][0]["pos"] == [0, 0.12, 0]
    assert (d / "previs" / "plan.prev.json").exists()
    # POST render: the blocked shot is recorded, a good one goes through render_shot
    calls = []
    monkeypatch.setattr(shots, "render_shot", lambda dd, sid, **k: calls.append(sid) or {"clip": "", "stills": [], "seconds": 0})
    t = []
    monkeypatch.setattr(api.threading, "Thread", lambda target, args, daemon: t.append((target, args)) or type("T", (), {"start": lambda self: None})())
    assert client.post(f"/api/jobs/{jid}/previs/s02/render").status_code == 202
    assert client.post(f"/api/jobs/{jid}/previs/s02/render").status_code == 409
    t[0][0](*t[0][1])
    assert calls == []
    assert json.loads((d / "previs" / "status.json").read_text())["s02"]["status"] == "blocked"
    assert client.post(f"/api/jobs/{jid}/previs/s03/render").status_code == 202
    t[1][0](*t[1][1])
    assert calls == ["s03"]
    assert {s["status"] for s in client.get(f"/api/jobs/{jid}/previs").json()["shots"]} <= set(api.STATUSES)
    assert (d / "remotion" / "src" / "data" / "previs" / "s03.json").exists()


def test_node_declaration_and_run(asset_dir, tmp_path, monkeypatch):
    nodes = {}
    decl = node.register(nodes)
    assert nodes["previs.render"] is decl
    assert decl["inputs"] == {"plan": "json", "job": "json"}
    assert decl["outputs"] == {"video": "video", "shots": "json"}
    assert decl["runner"] == "cpu" and set(decl["params"]) == {"size", "shadows"}
    d = make_job(asset_dir, tmp_path)
    plan = json.loads((EX / "plan.json").read_text())
    rendered = []

    def fake_render(dd, sid, **k):
        rendered.append(sid)
        return {"clip": str(Path(dd) / "shots" / f"{sid}.mp4"), "stills": [], "seconds": 1.0}

    def fake_main(dd, log=None):
        (Path(dd) / "final.mp4").write_bytes(b"x")
        return {"video": str(Path(dd) / "final.mp4"), "seconds": 2.0}

    monkeypatch.setattr(shots, "render_shot", fake_render)
    monkeypatch.setattr(shots, "render_main", fake_main)
    ctx = type("Ctx", (), {"dir": str(tmp_path / "out"), "meta": {}})()
    Path(ctx.dir).mkdir()
    out = decl["run"]({"plan": plan, "job": {"dir": str(d)}}, {"size": "1280x720", "shadows": False}, ctx)
    assert rendered == ["s01", "s02", "s03"]
    assert Path(out["video"]).exists() and set(out["shots"]) == {"s01", "s02", "s03"}
    s1 = json.loads((d / "previs" / "s01.json").read_text())
    assert s1["width"] == 1280 and s1["style"]["shadows"] is False
    assert ctx.meta["previs"]["film_seconds"] == 2.0
    bad = json.loads(json.dumps(plan))
    bad["shots"][0]["camera"]["keys"][0] = {"at": {"mark": "start"}, "pos": [0, 0.12, 0], "look": [0, 0.3, 0]}
    with pytest.raises(RuntimeError, match="检查不通过"):
        decl["run"]({"plan": bad, "job": {"dir": str(d)}}, {}, ctx)
    with pytest.raises(ValueError):
        decl["run"]({"plan": {}, "job": {"dir": str(d)}}, {}, ctx)
