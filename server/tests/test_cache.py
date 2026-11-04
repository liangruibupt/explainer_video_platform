"""Cache key and engine tests with stub nodes (no GPU, no model calls)."""
import time

import pytest

from app.workflows import engine
from app.workflows.nodes import NODES, node


def test_cache_key_is_stable_and_param_sensitive():
    h = {"prompt": engine.value_hash("a lake", "text")}
    k1 = engine.cache_key("image.qwen", {"ratio": "16:9", "seed": 42}, h)
    assert k1 == engine.cache_key("image.qwen", {"seed": 42, "ratio": "16:9"}, h)
    assert len(k1) == 64
    assert k1 != engine.cache_key("image.qwen", {"ratio": "16:9", "seed": 43}, h)
    assert k1 != engine.cache_key("video.h3", {"seed": 42}, h)


def test_cache_key_uses_effective_params():
    # writing a default explicitly does not change the key; unknown params are ignored
    h = {}
    assert (engine.cache_key("image.qwen", {"prompt": "x"}, h)
            == engine.cache_key("image.qwen", {"prompt": "x", "ratio": "16:9", "seed": 42,
                                               "zzz": 1}, h))


def test_cache_key_input_hash(tmp_path):
    a, b, c = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    a.write_bytes(b"one")
    b.write_bytes(b"one")
    c.write_bytes(b"two")
    # file inputs hash by content, not by path
    assert engine.value_hash(str(a), "image") == engine.value_hash(str(b), "image")
    assert engine.value_hash(str(a), "image") != engine.value_hash(str(c), "image")
    ka = engine.cache_key("video.h3", {}, {"first_frame": engine.value_hash(str(a), "image")})
    kc = engine.cache_key("video.h3", {}, {"first_frame": engine.value_hash(str(c), "image")})
    assert ka != kc
    # text vs json values
    assert engine.value_hash("x", "text") != engine.value_hash("y", "text")
    assert engine.value_hash({"a": 1, "b": 2}, "json") == engine.value_hash({"b": 2, "a": 1},
                                                                             "json")


def test_cache_key_changes_with_node_version(monkeypatch):
    k1 = engine.cache_key("agent.write", {"instruction": "x"}, {})
    monkeypatch.setitem(NODES["agent.write"], "version", 99)
    assert engine.cache_key("agent.write", {"instruction": "x"}, {}) != k1


# ---- engine with stub nodes -------------------------------------------------------

CALLS = []
FAILS = []


@pytest.fixture(autouse=True, scope="module")
def stubs():
    @node("test.gen", "stub generator", "test", runner="gpu:image", inputs={"text": "text"},
          outputs={"image": "image"}, params={"seed": {"type": "integer", "default": 1}})
    def _gen(inputs, p, ctx):
        CALLS.append(("gen", ctx.node_id))
        out = ctx.dir / "img.png"
        out.write_bytes(f"{inputs['text']}-{p['seed']}".encode())
        return {"image": str(out)}

    @node("test.post", "stub post", "test", inputs={"image": "image"},
          outputs={"image": "image"}, params={"gain": {"type": "integer", "default": 1}})
    def _post(inputs, p, ctx):
        CALLS.append(("post", ctx.node_id))
        out = ctx.dir / "post.png"
        out.write_bytes(open(inputs["image"], "rb").read() * p["gain"])
        return {"image": str(out)}

    @node("test.fail", "stub that fails once", "test", inputs={"image": "image"},
          outputs={"image": "image"}, params={})
    def _fail(inputs, p, ctx):
        CALLS.append(("fail", ctx.node_id))
        FAILS.append(1)
        if len(FAILS) == 1:
            raise RuntimeError("boom")
        return {"image": inputs["image"]}
    yield
    for t in ("test.gen", "test.post", "test.fail"):
        NODES.pop(t)


def wf(seed=1, gain=1, mid="test.post", text="hello"):
    return {"id": "wf_t", "name": "t", "nodes": [
        {"id": "a", "type": "input.text", "params": {"text": text}},
        {"id": "b", "type": "test.gen", "params": {"seed": seed}},
        {"id": "c", "type": mid, "params": {"gain": gain} if mid == "test.post" else {}},
        {"id": "r", "type": "review.human", "params": {}},
        {"id": "o", "type": "output.save", "params": {}}],
        "edges": [{"from": "a.text", "to": "b.text"}, {"from": "b.image", "to": "c.image"},
                  {"from": "c.image", "to": "r.input"}, {"from": "r.output", "to": "o.work"}]}


def wait(run_id, states, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = engine.read_run(run_id)
        if r["status"] in states:
            return r
        time.sleep(0.1)
    raise AssertionError(f"run stuck in {engine.read_run(run_id)['status']}")


def test_run_review_cache_and_downstream_rerun(monkeypatch):
    monkeypatch.setattr(engine, "validate", lambda w: [])
    CALLS.clear()
    r = wait(engine.start(wf(text="t1"))["id"], ("waiting_review",))
    assert r["nodes"]["r"]["status"] == "waiting_review"
    assert [c[0] for c in CALLS] == ["gen", "post"]
    r = engine.review(r["id"], "r", "approve")
    r = wait(r["id"], ("done",))
    assert r["works"] and r["works"][0]["file"] == "o.png"
    out = engine.run_dir(r["id"]) / "outputs" / "o.png"
    assert out.read_bytes() == b"t1-1"

    # change a downstream param: the generator is a cache hit, only post runs
    CALLS.clear()
    r2 = wait(engine.start(wf(text="t1", gain=2))["id"], ("waiting_review",))
    assert r2["nodes"]["b"]["status"] == "cached"
    assert r2["nodes"]["c"]["status"] == "done"
    assert CALLS == [("post", "c")]

    # same everything: both cached, review asked again
    CALLS.clear()
    r3 = wait(engine.start(wf(text="t1", gain=2))["id"], ("waiting_review",))
    assert CALLS == [] and r3["nodes"]["c"]["status"] == "cached"


def test_reject_fails_the_run(monkeypatch):
    monkeypatch.setattr(engine, "validate", lambda w: [])
    r = wait(engine.start(wf(text="t2"))["id"], ("waiting_review",))
    r = engine.review(r["id"], "r", "reject", "颜色不对")
    assert r["status"] == "failed" and "颜色不对" in r["nodes"]["r"]["error"]
    assert r["nodes"]["o"]["status"] == "pending"


def test_retry_failed_node_uses_upstream(monkeypatch):
    monkeypatch.setattr(engine, "validate", lambda w: [])
    CALLS.clear()
    r = wait(engine.start(wf(text="t3", mid="test.fail"))["id"], ("failed",))
    assert r["nodes"]["c"]["status"] == "failed" and "boom" in r["nodes"]["c"]["error"]
    assert r["nodes"]["r"]["status"] == "pending"
    CALLS.clear()
    engine.retry(r["id"], "c")
    r = wait(r["id"], ("waiting_review",))
    assert CALLS == [("fail", "c")]  # b kept its result, not re-run
    assert r["nodes"]["b"]["status"] == "done" and r["nodes"]["c"]["status"] == "done"


def test_same_gpu_module_is_serialised(monkeypatch):
    monkeypatch.setattr(engine, "validate", lambda w: [])
    spans = []

    @node("test.slow", "slow gpu", "test", runner="gpu:image", inputs={"text": "text"},
          outputs={"text": "text"}, params={"k": {"type": "integer", "default": 0}})
    def _slow(inputs, p, ctx):
        t = time.time()
        time.sleep(0.4)
        spans.append((t, time.time()))
        return {"text": "x"}
    try:
        w = {"id": "wf_s", "name": "s", "nodes": [
            {"id": "a", "type": "input.text", "params": {"text": f"s{time.time()}"}},
            {"id": "b", "type": "test.slow", "params": {"k": 1}},
            {"id": "c", "type": "test.slow", "params": {"k": 2}}],
            "edges": [{"from": "a.text", "to": "b.text"}, {"from": "a.text", "to": "c.text"}]}
        wait(engine.start(w)["id"], ("done",))
        (s1, e1), (s2, e2) = sorted(spans)
        assert s2 >= e1 - 0.01  # the second started after the first ended
    finally:
        NODES.pop("test.slow")


def test_downstream_gpu_modules_are_woken_once(monkeypatch):
    """_warm_downstream calls ensure_running() for pending downstream GPU nodes' modules
    (fake client: nothing is launched)."""
    import sys
    import types
    woken = []

    class FakeGpu:
        def __init__(self, m):
            self.m = m

        def ensure_running(self):
            woken.append(self.m)
    fake = types.SimpleNamespace(gpu=FakeGpu)
    monkeypatch.setitem(sys.modules, "app.gpu_client", fake)
    import app
    monkeypatch.setattr(app, "gpu_client", fake, raising=False)
    monkeypatch.setattr(engine, "WARM_GPUS", True)
    from app.workflows import store
    wf = store.template("wf_mv_from_one_line")
    run = {"id": "warm-test", "workflow": wf,
           "nodes": {n["id"]: {"status": "pending"} for n in wf["nodes"]}}
    run["nodes"]["n3"]["status"] = "running"  # music already running: not a target
    engine._warm_downstream(run, "n4")  # n4 -> n5 image -> n7 video
    engine._warm_downstream(run, "n5")  # video already woken: no second call
    time.sleep(0.3)
    assert sorted(woken) == ["image", "video"]
