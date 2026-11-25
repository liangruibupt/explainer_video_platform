"""图生 3D (docs/PREVIS_CONTRACT.md 4.6): mesh.py, mesh_api.py, mesh_node.py, the module
config and the container adapter's request parsing. GpuEC2 is stubbed for both the image
and the mesh module, boto3 is patched to fail and normalize is injected, so nothing here
reaches AWS or part A's builder."""
import base64
import importlib.util
import io
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import gpu_client, mesh, mesh_api, mesh_node
from app.gpu_ec2 import MODULES

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI()
app.include_router(mesh_api.router)
client = TestClient(app)
GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 64


def png(w=512, h=512, mode="RGB", color="white"):
    b = io.BytesIO()
    Image.new(mode, (w, h), color).save(b, "PNG")
    return b.getvalue()


class FakeGpu:
    """GpuEC2 stand-in for one module: records requests, answers like the worker."""

    def __init__(self, module, fail=None):
        self.module, self.fail, self.reqs = module, fail, []

    def submit(self, req, ensure=True):
        self.reqs.append(req)
        return f"{self.module}{len(self.reqs)}"

    def poll(self, jid):
        if self.fail:
            return {"ok": False, "error": self.fail}
        if self.module == "image":
            return {"ok": True, "files": {"images": ["jobs/image-ec2/out/x.0.png"]}}
        r = self.reqs[-1]
        return {"ok": True, "files": {"glb_b64": [f"jobs/mesh-ec2/out/{jid}.0.glb"]},
                "meta": {"model": r["model"], "render_s": 41.0, "faces": 52000,
                         "textured": r["texture"], "peak_vram_gb": 9.5},
                "worker_s": 43.0, "instance": {"instance_type": "g6e.2xlarge"}}

    def fetch(self, res, key):
        assert key == ("images" if self.module == "image" else "glb_b64")
        return [png(color="gray")] if self.module == "image" else [GLB]

    def status(self):
        return {"state": "none"}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    import boto3

    def no_aws(*a, **k):
        raise RuntimeError("boto3 disabled in tests")
    monkeypatch.setattr(boto3, "client", no_aws)
    cfg = tmp_path / "mesh.json"
    cfg.write_text(json.dumps({"module": "mesh", "default_model": "hunyuan3d-2mini",
                               "render_s": {"hunyuan3d-2mini": 50, "texture_extra": 200},
                               "cold_min": 7}))
    monkeypatch.setattr(mesh, "config_path", lambda: cfg)
    gpus = {"image": FakeGpu("image"), "mesh": FakeGpu("mesh")}
    for k, v in gpus.items():
        monkeypatch.setitem(gpu_client._gpus, k, v)
    monkeypatch.setattr(gpu_client.time, "sleep", lambda s: None)
    calls = []

    def fake_normalize(raw, target, source, name, project_id=None):
        assert Path(raw).read_bytes()[:4] == b"glTF"
        calls.append({"raw": str(raw), "target": target, "source": source, "name": name,
                      "project_id": project_id})
        return {"id": "asset_test0001", "kind": "model", "name": name}
    mesh_api.set_normalize(fake_normalize)
    shutil.rmtree(mesh_api.ROOT, ignore_errors=True)
    yield {"gpus": gpus, "normalize": calls, "cfg": cfg}
    mesh_api.set_normalize(None)
    shutil.rmtree(mesh_api.ROOT, ignore_errors=True)


def wait(task_id, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/mesh/tasks/{task_id}").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.05)
    raise AssertionError("mesh task stuck")


# ---------------------------------------------------------------- mesh.py

def test_status_without_config(monkeypatch, tmp_path):
    monkeypatch.setattr(mesh, "config_path", lambda: tmp_path / "missing.json")
    s = mesh.status()
    assert s["deployed"] is False and s["label"] == "未部署"
    with pytest.raises(RuntimeError, match="未部署"):
        mesh.generate(png())
    assert client.post("/api/assets/model/image3d",
                       json={"prompt": "猫", "target": {"axis": "h", "metres": 0.3}}).status_code == 503


def test_status_with_config():
    s = mesh.status()
    assert s["deployed"] is True and s["state"] == "idle" and s["default_model"] == "hunyuan3d-2mini"
    assert s["estimate"]["render_s"] == 50 and s["estimate"]["cold_min"] == 7
    assert mesh.estimate(texture=True)["render_s"] == 250
    assert client.get("/api/mesh/status").json()["deployed"] is True


def test_request_matches_contract_defaults():
    r = mesh.request(png())
    assert {k: r[k] for k in r if k != "image_b64"} == {
        "model": "hunyuan3d-2mini", "remove_bg": True, "texture": False, "octree_resolution": 256,
        "steps": 30, "guidance": 5.0, "seed": 42, "max_faces": 60000}
    assert base64.b64decode(r["image_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
    r = mesh.request(png(), model="hunyuan3d-2", texture=True, seed=7, steps=50, max_faces=20000)
    assert (r["model"], r["texture"], r["seed"], r["steps"], r["max_faces"]) == (
        "hunyuan3d-2", True, 7, 50, 20000)


def test_prepare_image_keeps_alpha_and_shrinks():
    out = Image.open(io.BytesIO(mesh.prepare_image(png(3000, 1500, "RGBA", (0, 0, 0, 0)))))
    assert out.mode == "RGBA" and max(out.size) == mesh.SEND_EDGE
    jpg = io.BytesIO()
    Image.new("RGB", (400, 400), "red").save(jpg, "JPEG")
    assert Image.open(io.BytesIO(mesh.prepare_image(jpg.getvalue()))).mode == "RGB"


@pytest.mark.parametrize("args,match", [
    ((b"",), "空"), ((b"not an image",), "无法解析"), ((png(64, 64),), "太小"),
    ((png(), "hunyuan3d-9"), "不支持的模型"), ((png(), "", False, -1), "seed"),
])
def test_request_rejects_bad_input(args, match):
    with pytest.raises(mesh.MeshError, match=match):
        mesh.request(*args)


def test_request_rejects_bad_params():
    with pytest.raises(mesh.MeshError, match="未知参数"):
        mesh.request(png(), foo=1)
    with pytest.raises(mesh.MeshError, match="超出范围"):
        mesh.request(png(), octree_resolution=4096)


def test_generate_returns_raw_glb(isolate):
    meta = {}
    glb = mesh.generate(png(), texture=True, seed=3, meta_out=meta)
    assert glb == GLB
    req = isolate["gpus"]["mesh"].reqs[-1]
    assert req["texture"] is True and req["seed"] == 3
    assert meta["faces"] == 52000 and meta["instance_type"] == "g6e.2xlarge"


def test_generate_raises_on_worker_error_or_non_glb(isolate, monkeypatch):
    isolate["gpus"]["mesh"].fail = "CUDA out of memory"
    with pytest.raises(RuntimeError, match="out of memory"):
        mesh.generate(png())
    isolate["gpus"]["mesh"].fail = None
    monkeypatch.setattr(isolate["gpus"]["mesh"], "fetch", lambda res, key: [b"PK\x03\x04"])
    with pytest.raises(RuntimeError, match="GLB"):
        mesh.generate(png())


# ---------------------------------------------------------------- mesh_api.py

def test_prompt_flow_concept_then_mesh_then_normalize(isolate):
    r = client.post("/api/assets/model/image3d",
                    json={"prompt": "一只坐着的猫", "target": {"axis": "h", "metres": 0.3},
                          "seed": 5, "project_id": "proj1"})
    assert r.status_code == 202, r.text
    s = wait(r.json()["task_id"])
    assert s["status"] == "done", s
    assert s["asset_id"] == "asset_test0001" and s["mesh_meta"]["faces"] == 52000
    img_req = isolate["gpus"]["image"].reqs[-1]
    assert img_req["prompt"] == "一只坐着的猫。" + mesh_api.CONCEPT_PROMPT
    assert img_req["ratio"] == "1:1" and img_req["seed"] == 5
    mreq = isolate["gpus"]["mesh"].reqs[-1]
    assert mreq["model"] == "hunyuan3d-2mini" and mreq["texture"] is False
    n = isolate["normalize"][-1]
    assert n["target"] == {"axis": "h", "metres": 0.3} and n["project_id"] == "proj1"
    assert n["source"] == {"type": "image3d", "model": "hunyuan3d-2mini", "seed": 5,
                           "target": {"axis": "h", "metres": 0.3}, "prompt": "一只坐着的猫"}
    assert set(s["urls"]) == {"concept", "raw"}
    assert client.get(s["urls"]["raw"]).content == GLB
    assert client.get(s["urls"]["concept"]).headers["content-type"] == "image/png"
    assert [t["task_id"] for t in client.get("/api/mesh/tasks").json()] == [s["id"]]


def test_image_asset_flow_skips_concept(isolate):
    from app.assets import store
    src = Path(mesh_api.DATA) / "cat_src.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(png(600, 600, "RGBA", (10, 10, 10, 0)))
    rec = store.ingest(src, "cat.png", name="cat")
    r = client.post("/api/assets/model/image3d",
                    json={"image_asset": rec["id"], "target": {"axis": "longest", "metres": 2},
                          "model": "hunyuan3d-2", "texture": True})
    assert r.status_code == 202, r.text
    s = wait(r.json()["task_id"])
    assert s["status"] == "done", s
    assert isolate["gpus"]["image"].reqs == []
    mreq = isolate["gpus"]["mesh"].reqs[-1]
    assert mreq["model"] == "hunyuan3d-2" and mreq["texture"] is True
    assert Image.open(io.BytesIO(base64.b64decode(mreq["image_b64"]))).mode == "RGBA"
    assert isolate["normalize"][-1]["source"]["image_asset"] == rec["id"]
    assert "prompt" not in isolate["normalize"][-1]["source"]


@pytest.mark.parametrize("body,match", [
    ({"target": {"axis": "h", "metres": 1}}, "只能给一个"),
    ({"prompt": "猫", "image_asset": "asset_x", "target": {"axis": "h", "metres": 1}}, "只能给一个"),
    ({"prompt": "猫"}, "target"),
    ({"prompt": "猫", "target": {"axis": "z", "metres": 1}}, "axis"),
    ({"prompt": "猫", "target": {"axis": "h", "metres": 0}}, "范围"),
    ({"prompt": "猫", "target": {"axis": "h", "metres": 1}, "model": "x"}, "不支持的模型"),
    ({"image_asset": "asset_nope0000", "target": {"axis": "h", "metres": 1}}, "素材"),
])
def test_create_rejects_bad_body(body, match):
    r = client.post("/api/assets/model/image3d", json=body)
    assert r.status_code == 422 and match in r.json()["detail"], r.text


def test_failure_then_retry_reuses_concept(isolate):
    isolate["gpus"]["mesh"].fail = "spot interruption"
    r = client.post("/api/assets/model/image3d",
                    json={"prompt": "陶罐", "target": {"axis": "h", "metres": 0.4}})
    s = wait(r.json()["task_id"])
    assert s["status"] == "failed" and "spot" in s["error"] and "concept.png" in s["files"]
    assert client.post(f"/api/mesh/tasks/{s['id']}/retry").status_code == 200
    isolate["gpus"]["mesh"].fail = None
    s = wait(s["id"])
    assert s["status"] == "done", s
    assert len(isolate["gpus"]["image"].reqs) == 1  # concept image not generated again
    assert client.post(f"/api/mesh/tasks/{s['id']}/retry").status_code == 409


def test_routes_reject_bad_ids():
    assert client.get("/api/mesh/tasks/../../etc").status_code == 404
    assert client.get("/api/mesh/tasks/20260101-000000-abcdef").status_code == 404
    assert client.get("/api/mesh/tasks/20260101-000000-abcdef/secret").status_code == 404


def test_default_normalize_calls_part_a(monkeypatch, tmp_path):
    """Without an injected function, normalize() goes to app.previs.assets_build.normalize."""
    import sys
    import types
    got = {}
    fake = types.ModuleType("app.previs.assets_build")
    fake.normalize = lambda raw, target, source, name, project_id=None: got.update(
        raw=raw, target=target) or {"id": "asset_a"}
    monkeypatch.setitem(sys.modules, "app.previs.assets_build", fake)
    import app.previs as previs_pkg
    monkeypatch.setattr(previs_pkg, "assets_build", fake, raising=False)
    mesh_api.set_normalize(None)
    assert mesh_api.normalize(tmp_path / "r.glb", {"axis": "h", "metres": 1}, {}, "n") == {"id": "asset_a"}
    assert got["target"] == {"axis": "h", "metres": 1}


# ---------------------------------------------------------------- mesh_node.py

class Ctx:
    def __init__(self, d):
        self.dir, self.meta, self.notes, self.waits = d, {}, [], []

    def note(self, m):
        self.notes.append(m)

    def on_wait(self, s):
        self.waits.append(s)


def _params(**kw):
    p = {k: v["default"] for k, v in mesh_node.DECLARATION["params"].items()}
    p.update(kw)
    return p


def test_node_declaration_and_register():
    nodes = {}
    d = mesh_node.register(nodes)
    assert nodes[mesh_node.TYPE] is d and d["runner"] == "gpu:mesh"
    assert d["inputs"] == {"prompt": "text", "image": "image"} and d["outputs"] == {"model": "model"}
    assert set(d["params"]) >= {"target_axis", "target_metres", "model", "texture", "seed"}
    d["params"]["seed"]["default"] = 1
    assert mesh_node.DECLARATION["params"]["seed"]["default"] == 42  # register copies


def test_node_prompt_and_image(isolate, tmp_path):
    ctx = Ctx(tmp_path / "n1")
    out = mesh_node._run({"prompt": "一只坐着的猫"}, _params(target_axis="h"), ctx)
    assert out == {"model": "asset_test0001"}
    assert ctx.meta["mesh"]["faces"] == 52000 and len(isolate["gpus"]["image"].reqs) == 1
    assert any("概念图" in n for n in ctx.notes)
    img = tmp_path / "in.png"
    img.write_bytes(png())
    ctx = Ctx(tmp_path / "n2")
    out = mesh_node._run({"image": str(img), "prompt": "x"}, _params(texture=True), ctx)
    assert out == {"model": "asset_test0001"} and len(isolate["gpus"]["image"].reqs) == 1
    assert isolate["gpus"]["mesh"].reqs[-1]["texture"] is True
    assert isolate["normalize"][-1]["target"] == {"axis": "longest", "metres": 0.3}


def test_node_errors(tmp_path):
    with pytest.raises(ValueError, match="需要提示词"):
        mesh_node._run({}, _params(), Ctx(tmp_path))
    with pytest.raises(ValueError, match="axis"):
        mesh_node._run({"prompt": "猫"}, _params(target_axis="q"), Ctx(tmp_path))


# ---------------------------------------------------------------- config + adapter

def test_module_config_shape():
    cfg = json.loads((MODULES / "mesh.json").read_text())
    img = json.loads((MODULES / "image.json").read_text())
    assert set(img) - {"_doc"} <= set(cfg)
    assert cfg["module"] == "mesh" and cfg["jobs_prefix"] == "jobs/mesh-ec2/"
    assert cfg["name"] == "evp-mesh-worker" and cfg["tags"] == {"project": "explainer-video",
                                                                "module": "mesh"}
    assert cfg["idle_shutdown_s"] == 900 and cfg["hard_cap_s"] == 14400
    assert cfg["outputs"] == {"glb_b64": "glb"} and cfg["default_model"] in mesh.MODELS
    assert cfg["weights"].startswith("s3://evp-models-747411437379-us-east-1/weights/hunyuan3d-2/")
    seen = {}
    for o in cfg["capacity"]:
        seen.setdefault((o["type"], o["market"]), set()).add(o["az"])
    assert {t for t, _ in seen} == {"g6e.2xlarge", "g7e.2xlarge", "g7.2xlarge"}
    assert all(len(v) == 2 for v in seen.values()) and len(seen) == 6
    markets = [o["market"] for o in cfg["capacity"]]
    assert markets == sorted(markets, key=lambda m: m != "on-demand")  # on-demand first


@pytest.fixture(scope="module")
def serve():
    p = ROOT / "infra" / "mesh" / "container" / "serve.py"
    spec = importlib.util.spec_from_file_location("mesh_serve", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_adapter_parse(serve):
    b64 = base64.b64encode(png()).decode()
    p = serve.parse({"image_b64": b64})
    assert {k: p[k] for k in p if k != "image"} == {
        "model": serve.DEFAULT, "remove_bg": True, "texture": False, "octree_resolution": 256,
        "steps": 30, "guidance": 5.0, "seed": 42, "max_faces": 60000}
    for bad in ({}, {"image_b64": "@@@"}, {"image_b64": b64, "model": "x"},
                {"image_b64": b64, "steps": 0}, {"image_b64": b64, "octree_resolution": "a"}):
        with pytest.raises(HTTPException) as e:
            serve.parse(bad)
        assert e.value.status_code == 400
    assert set(serve.MODELS) == set(mesh.MODELS)
