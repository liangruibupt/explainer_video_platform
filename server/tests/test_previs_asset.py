"""Part A of 3D previs: model assets in the library (store), assets_build, model_api, asset.build.

Runs the real Node builder (dist/previs.mjs, rebuilt on demand). The Remotion turntable is
stubbed here (about 30 s per render); the real render is exercised by hand (see the PR).
"""
import copy
import json
import struct
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.assets import api as assets_api
from app.assets import model_api, model_node, store
from app.previs import assets_build

EXAMPLE = Path(assets_build.__file__).parent / "examples" / "four_stroke" / "plan.json"
SPEC = json.loads(EXAMPLE.read_text())["assets"][0]["spec"]

app = FastAPI()
app.include_router(assets_api.router)
app.include_router(model_api.router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_turntable(monkeypatch):
    def fake(glb, meta, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        (out_dir / "turntable.mp4").write_bytes(b"\x00\x00\x00\x18ftypisomfake")
        return {"thumb.jpg": out_dir / "thumb.jpg", "turntable.mp4": out_dir / "turntable.mp4"}
    monkeypatch.setattr(assets_build, "turntable", fake)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """file.glb + meta.json of four_stroke from the real builder."""
    d = tmp_path_factory.mktemp("built")
    (d / "spec.json").write_text(json.dumps(SPEC))
    assets_build._cli(["build-asset", d / "spec.json", d / "file.glb", d / "meta.json"])
    return d / "file.glb", json.loads((d / "meta.json").read_text())


def test_sniff_glb(built):
    glb, _ = built
    assert store.sniff(glb.read_bytes()[:64], "glb") == "glb"
    assert store.FORMATS["glb"] == ("model", ("glb",), "model/gltf-binary")
    assert "model" in store.KINDS
    with pytest.raises(store.AssetError):
        store.sniff(glb.read_bytes()[:64], "png")


def test_check_glb_rejects_bad_headers(built, tmp_path):
    glb, _ = built
    raw = glb.read_bytes()
    store.check_glb(glb)
    bad_len = tmp_path / "len.glb"
    bad_len.write_bytes(raw[:8] + struct.pack("<I", len(raw) + 4) + raw[12:])
    bad_ver = tmp_path / "ver.glb"
    bad_ver.write_bytes(raw[:4] + struct.pack("<I", 1) + raw[8:])
    short = tmp_path / "short.glb"
    short.write_bytes(b"glTF\x02\x00\x00\x00")
    for p, status in ((bad_len, 422), (bad_ver, 415), (short, 422)):
        with pytest.raises(store.AssetError) as e:
            store.check_glb(p)
        assert e.value.status == status


def test_store_ingest_glb_merges_model_meta(built, tmp_path):
    glb, meta = built
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xe0x")
    mp4 = tmp_path / "turntable.mp4"
    mp4.write_bytes(b"mp4")
    m = store.ingest(glb, "engine.glb", name="发动机", model_meta=meta,
                     files={"thumb.jpg": thumb, "turntable.mp4": mp4})
    assert m["kind"] == "model" and m["format"] == "glb" and m["mime"] == "model/gltf-binary"
    assert m["file"] == "file.glb" and m["thumb"] is True and m["turntable"] is True
    assert m["bytes"] == glb.stat().st_size
    assert m["size"] == meta["size"] and len(m["parts"]) == 13 and m["tris"] == meta["tris"]
    assert m["rig"]["joints"]["crank"]["dof"] == "crank"
    assert m["source"]["type"] == "parametric"
    assert store.get(m["id"]) == m
    assert store.asset_path(m["id"]).read_bytes()[:4] == b"glTF"
    assert store.turntable_path(m["id"]).read_bytes() == b"mp4"
    assert any(x["id"] == m["id"] for x in store.list_all(kind="model"))


def test_store_rejects_glb_without_model_meta(built):
    glb, _ = built
    with pytest.raises(store.AssetError) as e:
        store.ingest(glb, "raw.glb")
    assert e.value.status == 415
    with pytest.raises(store.AssetError):
        store.ingest(glb, "x.glb", model_meta={"kind": "model"}, files={"../evil": glb})


def test_generic_upload_route_refuses_raw_glb(built):
    glb, _ = built
    with open(glb, "rb") as fh:
        r = client.post("/api/assets", files={"file": ("x.glb", fh)})
    assert r.status_code == 415


def test_api_build_ok_and_file_routes():
    r = client.post("/api/assets/model/build", json={"spec": SPEC})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["kind"] == "model" and m["name"] == SPEC["name"]
    assert abs(m["size"][1] - 0.52) < 0.52 * 0.02
    assert m["turntable_url"] == f"/api/assets/{m['id']}/turntable"
    f = client.get(m["url"])
    assert f.status_code == 200 and f.content[:4] == b"glTF"
    assert f.headers["content-type"] == "model/gltf-binary"
    assert client.get(m["thumb_url"]).status_code == 200
    t = client.get(m["turntable_url"])
    assert t.status_code == 200 and t.headers["content-type"] == "video/mp4"
    spec_file = store.asset_path(m["id"]).parent / "spec.json"
    assert json.loads(spec_file.read_text())["id"] == "four_stroke"


@pytest.mark.parametrize("code,breakit", [
    ("expr_error", lambda s: s["parts"][1].update(r_outer="bore/2 + nope")),
    ("bad_shape_param", lambda s: s["parts"][1].update(r_inner="bore/2 + 0.05")),
    ("dims_mismatch", lambda s: s["dims"].update(w=0.5)),
    ("unknown_parent", lambda s: s["parts"][6].update(parent="ghost")),
])
def test_api_build_bad_spec_422(code, breakit):
    s = copy.deepcopy(SPEC)
    breakit(s)
    before = len(store.list_all(kind="model"))
    r = client.post("/api/assets/model/build", json={"spec": s})
    assert r.status_code == 422, r.text
    issues = r.json()["issues"]
    assert any(i["code"] == code for i in issues), issues
    assert all(i["severity"] == "error" and i["message"] for i in issues)
    assert len(store.list_all(kind="model")) == before  # nothing stored


def test_api_build_rejects_non_object_spec():
    assert client.post("/api/assets/model/build", json={"spec": [1, 2]}).status_code == 422
    assert client.post("/api/assets/model/build",
                       json={"spec": SPEC, "project_id": "nope"}).status_code == 422


def test_api_upload_normalizes(built):
    glb, _ = built
    with open(glb, "rb") as fh:
        r = client.post("/api/assets/model/upload", files={"file": ("engine.glb", fh)},
                        data={"target_axis": "h", "target_metres": "1.04", "max_tris": "500"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert [p["id"] for p in m["parts"]] == ["body"]
    assert abs(m["size"][1] - 1.04) < 1.04 * 0.02
    assert abs(m["size"][0] - 0.6) < 0.6 * 0.03  # uniform scale x2
    assert abs(m["bbox"]["min"][1]) < 1e-6
    assert m["tris"] <= 500
    assert m["source"] == {"type": "upload", "filename": "engine.glb",
                           "target": {"axis": "h", "metres": 1.04}}
    assert m["name"] == "engine" and m["turntable"] is True


def test_api_upload_rejects(built, tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    with open(png, "rb") as fh:
        r = client.post("/api/assets/model/upload", files={"file": ("a.glb", fh)},
                        data={"target_axis": "h", "target_metres": "1"})
    assert r.status_code == 415
    glb, _ = built
    for data in ({"target_axis": "x", "target_metres": "1"},
                 {"target_axis": "h", "target_metres": "0"},
                 {"target_axis": "h"}):
        with open(glb, "rb") as fh:
            r = client.post("/api/assets/model/upload", files={"file": ("a.glb", fh)}, data=data)
        assert r.status_code == 422, data


def test_turntable_route_404():
    assert client.get("/api/assets/asset_20000101-000000-abcdef/turntable").status_code == 404
    assert client.get("/api/assets/../turntable").status_code == 404


class Ctx:
    def __init__(self):
        self.meta = {}


def test_asset_build_node():
    nodes = {}
    decl = model_node.register(nodes)
    assert nodes["asset.build"] is decl
    assert decl["inputs"] == {"spec": "json"} and decl["outputs"] == {"model": "model"}
    assert decl["runner"] == "cpu"
    ctx = Ctx()
    out = decl["run"]({"spec": {"id": "four_stroke", "kind": "parametric", "spec": SPEC}}, {}, ctx)
    assert store.valid_id(out["model"]) and store.get(out["model"])["kind"] == "model"
    assert ctx.meta["asset"]["id"] == out["model"]
    bad = copy.deepcopy(SPEC)
    bad["dims"]["h"] = 2
    with pytest.raises(ValueError, match="dims_mismatch"):
        decl["run"]({"spec": bad}, {}, Ctx())


def test_dist_rebuilt_when_missing(monkeypatch, tmp_path):
    dist = tmp_path / "dist" / "previs.mjs"
    monkeypatch.setattr(assets_build, "DIST", dist)
    calls = []
    real = assets_build.subprocess.run

    def run(args, **kw):
        calls.append(args)
        dist.parent.mkdir(parents=True, exist_ok=True)
        dist.write_text("// built")
        return real(["true"], **kw)
    monkeypatch.setattr(assets_build.subprocess, "run", run)
    assets_build.ensure_dist()
    assert len(calls) == 1 and calls[0][1].endswith("build.sh")
    assets_build.ensure_dist()  # fresh now: no second build
    assert len(calls) == 1
