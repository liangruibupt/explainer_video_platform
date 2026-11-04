"""Candidates (candidates.py + engine hooks) with stub GPU nodes: no GPU, no model calls."""
import time

import pytest

from app.workflows import candidates as cand
from app.workflows import engine
from app.workflows.nodes import NODES, node
from app.workflows.validate import validate

CALLS = []


@pytest.fixture(autouse=True, scope="module")
def stubs():
    @node("test.cgen", "stub generator", "test", runner="gpu:image", inputs={"text": "text"},
          outputs={"image": "image"},
          params={"seed": {"type": "integer", "default": 10, "min": 0},
                  "candidates": dict(cand.PARAM)})
    def _gen(inputs, p, ctx):
        CALLS.append(("gen", ctx.node_id, p["seed"]))
        out = ctx.dir / "img.png"
        out.write_bytes(f"{inputs['text']}-{p['seed']}".encode())
        return {"image": str(out)}

    @node("test.cpost", "stub post", "test", inputs={"image": "image"},
          outputs={"image": "image"}, params={})
    def _post(inputs, p, ctx):
        CALLS.append(("post", ctx.node_id, None))
        out = ctx.dir / "post.png"
        out.write_bytes(b"post:" + open(inputs["image"], "rb").read())
        return {"image": str(out)}
    yield
    for t in ("test.cgen", "test.cpost"):
        NODES.pop(t)


def wf(text, **gen):
    return {"id": "wf_c", "name": "c", "nodes": [
        {"id": "a", "type": "input.text", "params": {"text": text}},
        {"id": "b", "type": "test.cgen", "params": gen},
        {"id": "c", "type": "test.cpost", "params": {}},
        {"id": "o", "type": "output.save", "params": {}}],
        "edges": [{"from": "a.text", "to": "b.text"}, {"from": "b.image", "to": "c.image"},
                  {"from": "c.image", "to": "o.work"}]}


def wait(run_id, states, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = engine.read_run(run_id)
        if r["status"] in states:
            return r
        time.sleep(0.05)
    raise AssertionError(f"run stuck in {engine.read_run(run_id)['status']}")


@pytest.fixture(autouse=True)
def no_validate(monkeypatch):
    monkeypatch.setattr(engine, "validate", lambda w: [])
    CALLS.clear()


def test_gpu_generation_nodes_declare_candidates():
    for t in cand.GEN_TYPES:
        s = NODES[t]["params"]["candidates"]
        assert (s["default"], s["min"], s["max"]) == (1, 1, 4)
    assert "candidates" not in NODES["agent.write"]["params"]
    assert cand.seeds(5, 3) == [5, 6, 7] and cand.seeds(2**31 - 1, 2) == [2**31 - 1, 0]


def test_validator_checks_candidates_range():
    w = {"name": "v", "nodes": [{"id": "i", "type": "image.qwen",
                                 "params": {"prompt": "x", "candidates": 5}}], "edges": []}
    errs = validate(w, modules={"image"})
    assert any(e["code"] == "bad_param" and e["port"] == "candidates" for e in errs)
    w["nodes"][0]["params"]["candidates"] = 3
    w["nodes"][0]["ui"] = {"x": 10, "y": 20}  # editor positions are ignored
    assert validate(w, modules={"image"}) == []


def test_n_candidates_distinct_seeds_and_choice_flows_downstream():
    r = wait(engine.start(wf("p1", candidates=3, seed=10))["id"], ("waiting_review",))
    assert [c for c in CALLS if c[0] == "gen"] == [("gen", "b", 10), ("gen", "b", 11),
                                                   ("gen", "b", 12)]
    b = r["nodes"]["b"]
    assert b["status"] == "waiting_review"
    assert [c["seed"] for c in b["candidates"]] == [10, 11, 12]
    assert len({c["cache_key"] for c in b["candidates"]}) == 3
    files = [open(c["outputs"]["image"], "rb").read() for c in b["candidates"]]
    assert files == [b"p1-10", b"p1-11", b"p1-12"]
    assert set(b["outputs"]) == {"c0.image", "c1.image", "c2.image"}  # previews
    assert r["nodes"]["c"]["status"] == "pending"

    with pytest.raises(ValueError):
        engine.review(r["id"], "b", "approve", choice=7)
    with pytest.raises(ValueError):
        engine.review(r["id"], "b", "approve")  # choice required
    assert engine.read_run(r["id"])["nodes"]["b"]["status"] == "waiting_review"

    engine.review(r["id"], "b", "approve", "第二张好", choice=1)
    r = wait(r["id"], ("done",))
    assert r["nodes"]["b"]["choice"] == 1 and r["nodes"]["b"]["review"]["choice"] == 1
    assert r["nodes"]["b"]["outputs"] == b["candidates"][1]["outputs"]
    out = engine.run_dir(r["id"]) / "outputs" / "o.png"
    assert out.read_bytes() == b"post:p1-11"


def test_candidates_are_cached_separately():
    r = wait(engine.start(wf("p2", candidates=2, seed=20))["id"], ("waiting_review",))
    assert len(CALLS) == 2
    CALLS.clear()
    # same inputs, one more candidate: only the new seed is generated
    r2 = wait(engine.start(wf("p2", candidates=3, seed=20))["id"], ("waiting_review",))
    assert CALLS == [("gen", "b", 22)]
    c = r2["nodes"]["b"]["candidates"]
    assert [x["cached"] for x in c] == [True, True, False]
    assert [x["cache_key"] for x in c[:2]] == [x["cache_key"] for x in
                                              r["nodes"]["b"]["candidates"]]
    # candidate 1 approved, then a plain run with seed 21 is a cache hit on that candidate
    engine.review(r2["id"], "b", "approve", choice=1)
    wait(r2["id"], ("done",))
    CALLS.clear()
    r3 = wait(engine.start(wf("p2", seed=21))["id"], ("done",))
    assert r3["nodes"]["b"]["status"] == "cached"
    assert r3["nodes"]["b"]["cache_key"] == c[1]["cache_key"]
    assert [x[0] for x in CALLS] == []  # post is cached too: same input bytes


def test_candidates_1_is_unchanged():
    k_plain = engine.cache_key("test.cgen", {"seed": 30}, {"text": "h"})
    assert k_plain == engine.cache_key("test.cgen", {"seed": 30, "candidates": 1},
                                       {"text": "h"})
    # a declared type without the new param keeps the key it had before (no 'candidates')
    assert engine.cache_key("agent.write", {"instruction": "x"}, {}) == \
        engine.cache_key("agent.write", {"instruction": "x", "candidates": 3}, {})
    r = wait(engine.start(wf("p3", seed=30))["id"], ("done",))
    b = r["nodes"]["b"]
    assert b["status"] == "done" and "candidates" not in b and "choice" not in b
    assert CALLS[0] == ("gen", "b", 30)
    assert set(b["outputs"]) == {"image"}
    # the first candidate of a multi run shares candidate 0 with the plain run's cache
    CALLS.clear()
    r2 = wait(engine.start(wf("p3", seed=30, candidates=2))["id"], ("waiting_review",))
    assert CALLS == [("gen", "b", 31)]
    assert r2["nodes"]["b"]["candidates"][0]["cache_key"] == b["cache_key"]


def test_reject_candidates_fails_run():
    r = wait(engine.start(wf("p4", candidates=2, seed=40))["id"], ("waiting_review",))
    r = engine.review(r["id"], "b", "reject", "都不好")
    assert r["status"] == "failed" and "都不好" in r["nodes"]["b"]["error"]
