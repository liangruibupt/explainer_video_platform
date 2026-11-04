"""Asset library, projects and the input.asset node (phase 4 part C). No GPU, no network."""
import io
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import images, media, pipeline
from app.assets import node as asset_node
from app.assets import projects, store
from app.main import app
from app.workflows import engine
from app.workflows.nodes import NODES

client = TestClient(app)


def ff(*args, out):
    subprocess.run([pipeline.FFMPEG, "-v", "error", "-y", *args, str(out)], check=True,
                   timeout=120)
    return out


@pytest.fixture(scope="module")
def media_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    f = {}
    f["png"] = ff("-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1",
                  out=d / "a.png")
    f["jpg"] = ff("-f", "lavfi", "-i", "color=c=blue:s=200x100", "-frames:v", "1",
                  out=d / "a.jpg")
    f["webp"] = ff("-f", "lavfi", "-i", "color=c=green:s=100x100", "-frames:v", "1",
                   out=d / "a.webp")
    tone = ("-f", "lavfi", "-i", "sine=frequency=440:duration=1")
    f["wav"] = ff(*tone, out=d / "a.wav")
    f["mp3"] = ff(*tone, out=d / "a.mp3")
    f["flac"] = ff(*tone, out=d / "a.flac")
    f["m4a"] = ff(*tone, "-c:a", "aac", out=d / "a.m4a")
    vid = ("-f", "lavfi", "-i", "testsrc=s=320x240:r=10:d=2")
    f["mp4"] = ff(*vid, "-pix_fmt", "yuv420p", out=d / "a.mp4")
    f["mov"] = ff(*vid, "-pix_fmt", "yuv420p", out=d / "a.mov")
    f["webm"] = ff(*vid, "-c:v", "libvpx", "-b:v", "200k", out=d / "a.webm")
    return f


def upload(path, filename=None, **form):
    with open(path, "rb") as fh:
        return client.post("/api/assets", files={"file": (filename or path.name, fh)},
                           data=form)


KINDS = {"png": "image", "jpg": "image", "webp": "image", "wav": "audio", "mp3": "audio",
         "flac": "audio", "m4a": "audio", "mp4": "video", "mov": "video", "webm": "video"}


@pytest.mark.parametrize("fmt", list(KINDS))
def test_upload_each_kind(media_files, fmt):
    r = upload(media_files[fmt], name=f"测试 {fmt}")
    assert r.status_code == 201, r.text
    m = r.json()
    assert store.ASSET_ID.fullmatch(m["id"])
    assert m["kind"] == KINDS[fmt] and m["format"] == fmt and m["name"] == f"测试 {fmt}"
    assert len(m["sha256"]) == 64 and m["size"] == media_files[fmt].stat().st_size
    meta = json.loads((store.ROOT / m["id"] / "meta.json").read_text())
    assert meta["sha256"] == m["sha256"]
    body = client.get(m["url"])
    assert body.status_code == 200 and body.content == media_files[fmt].read_bytes()
    thumb = client.get(f"/api/assets/{m['id']}/thumb")
    if m["kind"] == "audio":
        assert thumb.status_code == 404 and m["duration"] > 0.5
    else:
        assert thumb.status_code == 200 and thumb.content[:3] == b"\xff\xd8\xff"
        assert m["width"] and m["height"]
    if m["kind"] == "image":
        from PIL import Image
        assert Image.open(io.BytesIO(thumb.content)).width <= 320
    if m["kind"] == "video":
        assert m["duration"] > 1.5
    assert store.asset_path(m["id"]).is_file()


def test_list_filter_get_patch_delete(media_files):
    a = upload(media_files["png"]).json()
    b = upload(media_files["wav"]).json()
    ids = {x["id"] for x in client.get("/api/assets?kind=image").json()}
    assert a["id"] in ids and b["id"] not in ids
    assert client.get("/api/assets?kind=bogus").status_code == 422
    assert client.get(f"/api/assets/{a['id']}").json()["name"] == "a"

    p = client.post("/api/projects", json={"name": "项目一"}).json()
    r = client.patch(f"/api/assets/{a['id']}", json={"name": "新名字", "project_id": p["id"]})
    assert r.status_code == 200 and r.json()["name"] == "新名字"
    assert [x["id"] for x in client.get(f"/api/assets?project_id={p['id']}").json()] == [a["id"]]
    # unknown project, malformed project
    assert client.patch(f"/api/assets/{a['id']}", json={
        "project_id": "proj_20990101-000000-abcdef"}).status_code == 422
    assert client.patch(f"/api/assets/{a['id']}", json={"project_id": "../x"}).status_code == 422
    # clearing the project
    r = client.patch(f"/api/assets/{a['id']}", json={"project_id": ""})
    assert r.json()["project_id"] is None

    assert client.delete(f"/api/assets/{a['id']}").status_code == 200
    assert client.get(f"/api/assets/{a['id']}").status_code == 404
    assert client.delete(f"/api/assets/{a['id']}").status_code == 404
    assert not (store.ROOT / a["id"]).exists()


def test_magic_byte_rejection(tmp_path, media_files):
    fake = tmp_path / "evil.png"
    fake.write_bytes(b"<html><script>alert(1)</script></html>" * 10)
    assert upload(fake).status_code == 415
    # real png content with a mismatched extension
    assert upload(media_files["png"], filename="x.mp4").status_code == 415
    # wav with an mp3 name
    assert upload(media_files["wav"], filename="x.mp3").status_code == 415
    # truncated png: magic ok, image does not parse
    bad = tmp_path / "bad.png"
    bad.write_bytes(media_files["png"].read_bytes()[:40])
    assert upload(bad).status_code == 415
    # mp4 magic but garbage after
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200)
    assert upload(junk).status_code == 415
    empty = tmp_path / "e.png"
    empty.write_bytes(b"")
    assert upload(empty).status_code == 422
    # nothing left behind
    assert not [p for p in store.ROOT.iterdir() if p.name.startswith(".")]


def test_size_limit(monkeypatch, media_files):
    monkeypatch.setattr(store, "MAX_BYTES", 1000)
    assert upload(media_files["mp4"]).status_code == 413


def test_name_and_filename_are_not_paths(media_files):
    r = upload(media_files["png"], filename="../../../etc/passwd.png", name="../../x")
    assert r.status_code == 201
    m = r.json()
    assert m["file"] == "file.png" and (store.ROOT / m["id"] / "file.png").is_file()


@pytest.mark.parametrize("bad", [
    "..", "..%2F..%2Fetc%2Fpasswd", "asset_20260101-000000-abcdeg",
    "asset_20260101-000000-abcdef.json", "%2e%2e", "asset_..", "x" * 300,
])
def test_path_traversal_ids(bad):
    for url in (f"/api/assets/{bad}", f"/api/assets/{bad}/file", f"/api/assets/{bad}/thumb",
                f"/api/projects/{bad}", f"/api/projects/{bad}/items"):
        r = client.get(url)
        # ".." segments are normalised away by the client; what is left may hit the SPA
        # catch-all (index.html) when web/dist exists, never a file or a record
        if r.status_code == 200:
            assert r.headers["content-type"].startswith("text/html"), url
            assert b"root:" not in r.content and b'"sha256"' not in r.content, url
        else:
            assert r.status_code == 404, url
    assert client.delete(f"/api/assets/{bad}").status_code in (404, 405)
    assert client.patch(f"/api/projects/{bad}", json={"name": "x"}).status_code in (404, 405)
    with pytest.raises(KeyError):
        store.asset_path(bad)
    assert store.get(bad) == {} and projects.get(bad) == {}


def test_upload_rejects_bad_project(media_files):
    assert upload(media_files["png"], project_id="../../etc").status_code == 422
    assert upload(media_files["png"],
                  project_id="proj_20990101-000000-abcdef").status_code == 422


def _fake_work(root, wid, rec, files):
    d = root / wid
    d.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(src.read_bytes())
    return d


def test_import_from_works_and_project_items(media_files):
    p = client.post("/api/projects", json={"name": "项目二", "note": "备注"}).json()
    pid = p["id"]
    # an image generation, a video, a music, an explainer job and a workflow run
    _fake_work(images.ROOT, "20261104-010101-aaaaaa", {}, {"0.png": media_files["png"]})
    (images.ROOT / "20261104-010101-aaaaaa" / "state.json").write_text(json.dumps(
        {"id": "20261104-010101-aaaaaa", "status": "done", "files": ["0.png"],
         "prompt": "红色方块", "created": time.time(), "project_id": pid}))
    _fake_work(media.videos.root, "20261104-010101-bbbbbb", {}, {"0.mp4": media_files["mp4"]})
    (media.videos.root / "20261104-010101-bbbbbb" / "state.json").write_text(json.dumps(
        {"id": "20261104-010101-bbbbbb", "status": "done", "files": ["0.mp4"],
         "prompt": "测试", "created": time.time()}))
    _fake_work(media.musics.root, "20261104-010101-cccccc", {}, {"0.flac": media_files["flac"]})
    (media.musics.root / "20261104-010101-cccccc" / "state.json").write_text(json.dumps(
        {"id": "20261104-010101-cccccc", "status": "done", "files": ["0.flac"],
         "style": "piano", "created": time.time(), "project_id": pid}))
    _fake_work(pipeline.DATA / "jobs", "20261104-010101-dddddd", {},
               {"final.mp4": media_files["mp4"], "secret.txt": media_files["wav"]})
    (pipeline.job_dir("20261104-010101-dddddd") / "job.json").write_text(json.dumps(
        {"id": "20261104-010101-dddddd", "status": "done", "topic": "光合作用",
         "created": time.time(), "project_id": pid}))
    _fake_work(engine.RUNS, "20261104-010101-eeeeee", {},
               {"outputs/o.mp4": media_files["mp4"]})
    (engine.run_dir("20261104-010101-eeeeee") / "run.json").write_text(json.dumps(
        {"id": "20261104-010101-eeeeee", "status": "done", "name": "流程",
         "created": time.time(), "project_id": pid, "works": [{"node": "o", "file": "o.mp4"}]}))

    cases = [("image", "20261104-010101-aaaaaa", "", "image"),
             ("video", "20261104-010101-bbbbbb", "0.mp4", "video"),
             ("music", "20261104-010101-cccccc", "", "audio"),
             ("job", "20261104-010101-dddddd", "", "video"),
             ("wf_run", "20261104-010101-eeeeee", "o.mp4", "video")]
    for source, wid, file, kind in cases:
        r = client.post("/api/assets/import", json={"source": source, "id": wid, "file": file,
                                                    "project_id": pid})
        assert r.status_code == 201, (source, r.text)
        m = r.json()
        assert m["kind"] == kind and m["source"]["type"] == source and m["project_id"] == pid
    assert client.get(f"/api/assets?project_id={pid}").json()[-1]["name"] == "红色方块"

    # rejected imports: files not in the record, traversal, unknown ids / sources
    for body, code in [
        ({"source": "job", "id": "20261104-010101-dddddd", "file": "secret.txt"}, 404),
        ({"source": "job", "id": "20261104-010101-dddddd", "file": "../job.json"}, 422),
        ({"source": "job", "id": "../../etc", "file": ""}, 422),
        ({"source": "image", "id": "20261104-010101-ffffff"}, 404),
        ({"source": "disk", "id": "20261104-010101-dddddd"}, 422),
        ({"source": "image", "id": "20261104-010101-aaaaaa", "file": "1.png"}, 404),
    ]:
        assert client.post("/api/assets/import", json=body).status_code == code, body

    items = client.get(f"/api/projects/{pid}/items").json()
    assert len(items["assets"]) == 5
    assert [x["id"] for x in items["images"]] == ["20261104-010101-aaaaaa"]
    assert items["videos"] == []  # the video record has no project_id
    assert [x["title"] for x in items["musics"]] == ["piano"]
    assert [x["title"] for x in items["jobs"]] == ["光合作用"]
    assert items["wf_runs"][0]["files"] == ["o.mp4"]


def test_projects_crud():
    r = client.post("/api/projects", json={"name": "  旅行  ", "note": "n"})
    assert r.status_code == 201
    p = r.json()
    assert projects.valid_id(p["id"]) and p["name"] == "旅行"
    assert (projects.ROOT / f"{p['id']}.json").exists()
    assert any(x["id"] == p["id"] for x in client.get("/api/projects").json())
    assert client.patch(f"/api/projects/{p['id']}", json={"name": "旅行 2"}).json()["name"] \
        == "旅行 2"
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert client.delete(f"/api/projects/{p['id']}").status_code == 404


def test_deleting_project_unassigns_assets(media_files):
    pid = client.post("/api/projects", json={"name": "临时"}).json()["id"]
    a = upload(media_files["jpg"], project_id=pid).json()
    assert a["project_id"] == pid
    client.delete(f"/api/projects/{pid}")
    assert client.get(f"/api/assets/{a['id']}").json()["project_id"] is None


# ---- the input.asset node ---------------------------------------------------------

class FakeCtx:
    def __init__(self, tmp_path):
        self.run_id, self.node_id = "20261104-000000-000000", "n1"
        self.dir = tmp_path
        self.meta = {}


def test_node_registers_into_a_copy_and_runs(media_files, tmp_path):
    table = {k: v for k, v in NODES.items() if k != "input.asset"}
    decl = asset_node.register(table)
    # Registered into the copy; integration also registers it in the real NODES (nodes.py).
    assert "input.asset" in table and "input.asset" in NODES
    assert set(decl["outputs"]) == {"image", "audio", "video"}
    assert set(decl) >= set(next(iter(NODES.values())))  # same keys as library nodes
    for fmt in ("png", "wav", "mp4"):
        m = upload(media_files[fmt]).json()
        out = decl["run"]({}, {"asset_id": m["id"]}, FakeCtx(tmp_path))
        assert out == {m["kind"]: str(store.asset_path(m["id"]))}
    with pytest.raises(ValueError):
        decl["run"]({}, {"asset_id": "../../etc/passwd"}, FakeCtx(tmp_path))
    with pytest.raises(ValueError):
        decl["run"]({}, {"asset_id": "asset_20990101-000000-abcdef"}, FakeCtx(tmp_path))


def test_node_in_an_engine_run(media_files, monkeypatch):
    """Registered into the live table only for this test, then removed."""
    asset_node.register(NODES)
    try:
        from app.workflows.validate import check_types, validate
        check_types()
        img = upload(media_files["png"]).json()
        wf = {"id": "wf_asset", "name": "asset", "nodes": [
            {"id": "a", "type": "input.asset", "params": {"asset_id": img["id"]}},
            {"id": "o", "type": "output.save", "params": {"title": "x"}}],
            "edges": [{"from": "a.image", "to": "o.work"}]}
        assert validate(wf, modules=set()) == []
        monkeypatch.setattr(engine, "validate", lambda w: [])
        run = engine.start(wf)
        t0 = time.time()
        while engine.read_run(run["id"])["status"] not in ("done", "failed"):
            assert time.time() - t0 < 20
            time.sleep(0.1)
        r = engine.read_run(run["id"])
        assert r["status"] == "done", r
        assert (engine.run_dir(r["id"]) / "outputs" / "o.png").read_bytes() \
            == media_files["png"].read_bytes()
        # wiring the wrong port fails clearly
        wf["edges"] = [{"from": "a.video", "to": "o.work"}]
        run = engine.start(wf)
        t0 = time.time()
        while engine.read_run(run["id"])["status"] not in ("done", "failed"):
            assert time.time() - t0 < 20
            time.sleep(0.1)
        r = engine.read_run(run["id"])
        assert r["status"] == "failed" and "image" in r["nodes"]["a"]["error"]
    finally:
        NODES.pop("input.asset", None)
