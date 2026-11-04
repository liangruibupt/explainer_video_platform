"""Remembered candidate choices (candidates.py): stub GPU nodes, no GPU, no model calls."""
import json
import time

import pytest

from app.tasks import collect
from app.workflows import candidates as cand
from app.workflows import engine
from app.workflows.nodes import NODES, node

CALLS = []


@pytest.fixture(autouse=True, scope="module")
def stubs():
    @node("test.rgen", "stub generator", "test", runner="gpu:image", inputs={"text": "text"},
          outputs={"image": "image"},
          params={"seed": {"type": "integer", "default": 10, "min": 0},
                  "candidates": dict(cand.PARAM)})
    def _gen(inputs, p, ctx):
        CALLS.append(("gen", p["seed"]))
        out = ctx.dir / "img.png"
        out.write_bytes(f"{inputs['text']}-{p['seed']}".encode())
        return {"image": str(out)}

    @node("test.rpost", "stub downstream", "test", runner="gpu:video",
          inputs={"image": "image"}, outputs={"image": "image"},
          params={"seed": {"type": "integer", "default": 1, "min": 0}})
    def _post(inputs, p, ctx):
        CALLS.append(("post", p["seed"]))
        out = ctx.dir / "post.png"
        out.write_bytes(b"post%d:" % p["seed"] + open(inputs["image"], "rb").read())
        return {"image": str(out)}
    yield
    for t in ("test.rgen", "test.rpost"):
        NODES.pop(t)


def wf(text, post_seed=1, **gen):
    return {"id": "wf_r", "name": "r", "nodes": [
        {"id": "a", "type": "input.text", "params": {"text": text}},
        {"id": "b", "type": "test.rgen", "params": gen},
        {"id": "c", "type": "test.rpost", "params": {"seed": post_seed}},
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


def out_bytes(r):
    return (engine.run_dir(r["id"]) / "outputs" / "o.png").read_bytes()


def test_review_is_remembered_and_reused_on_all_cached_rerun():
    r = wait(engine.start(wf("m1", candidates=2, seed=50))["id"], ("waiting_review",))
    assert [c[0] for c in CALLS] == ["gen", "gen"]
    cands = r["nodes"]["b"]["candidates"]
    assert cand.remembered(cands) is None  # nothing chosen yet
    engine.review(r["id"], "b", "approve", choice=1)
    r = wait(r["id"], ("done",))
    assert out_bytes(r) == b"post1:m1-51"
    stored = engine.CACHE / "_choices" / f"{cand.set_key(cands)}.json"
    j = json.loads(stored.read_text())
    assert j["choice"] == 1 and j["seed"] == 51
    assert j["keys"] == [c["cache_key"] for c in cands]
    assert not list(stored.parent.glob("*.tmp"))

    # only the downstream param changes: no review, candidate 1 flows on, no generation
    CALLS.clear()
    r2 = wait(engine.start(wf("m1", post_seed=2, candidates=2, seed=50))["id"],
              ("done", "waiting_review", "failed"))
    assert r2["status"] == "done"
    b = r2["nodes"]["b"]
    assert b["status"] == "done" and b["choice"] == 1 and b["auto_choice"] is True
    assert b["note"] == "沿用上次的选择：候选 2（seed 51）"
    assert b["outputs"] == cands[1]["outputs"]
    assert len(b["candidates"]) == 2  # previews kept for re-picking
    assert CALLS == [("post", 2)]
    assert out_bytes(r2) == b"post2:m1-51"
    assert "review" not in b
    # the task center shows no review for it
    assert collect.node_reviews(r2) == []


def test_new_candidate_waits_again():
    r = wait(engine.start(wf("m2", candidates=2, seed=60))["id"], ("waiting_review",))
    engine.review(r["id"], "b", "approve", choice=1)
    wait(r["id"], ("done",))
    CALLS.clear()
    r2 = wait(engine.start(wf("m2", candidates=3, seed=60))["id"], ("waiting_review",))
    assert CALLS == [("gen", 62)]
    b = r2["nodes"]["b"]
    assert b["status"] == "waiting_review" and "choice" not in b
    assert [c["cached"] for c in b["candidates"]] == [True, True, False]
    assert len(collect.node_reviews(r2)) == 1
    # the three-candidate set is a different set: choosing there does not touch the old one
    engine.review(r2["id"], "b", "approve", choice=2)
    wait(r2["id"], ("done",))
    r3 = wait(engine.start(wf("m2", candidates=3, seed=60))["id"], ("done", "waiting_review"))
    assert r3["status"] == "done" and r3["nodes"]["b"]["choice"] == 2
    r4 = wait(engine.start(wf("m2", candidates=2, seed=60))["id"], ("done", "waiting_review"))
    assert r4["status"] == "done" and r4["nodes"]["b"]["choice"] == 1


def test_retry_forgets_choice_and_asks_again():
    r = wait(engine.start(wf("m3", candidates=2, seed=70))["id"], ("waiting_review",))
    engine.review(r["id"], "b", "approve", choice=0)
    wait(r["id"], ("done",))
    r2 = wait(engine.start(wf("m3", post_seed=5, candidates=2, seed=70))["id"],
              ("done", "waiting_review"))
    assert r2["status"] == "done" and r2["nodes"]["b"]["auto_choice"]
    CALLS.clear()
    engine.retry(r2["id"], "b")
    r2 = wait(r2["id"], ("waiting_review",))
    b = r2["nodes"]["b"]
    assert b["status"] == "waiting_review" and all(c["cached"] for c in b["candidates"])
    assert [c[0] for c in CALLS] == []  # everything still cached: no GPU call
    assert r2["nodes"]["c"]["status"] == "pending"
    engine.review(r2["id"], "b", "approve", choice=1)
    r2 = wait(r2["id"], ("done",))
    assert out_bytes(r2) == b"post5:m3-71"
    # the new pick is remembered
    r3 = wait(engine.start(wf("m3", candidates=2, seed=70))["id"], ("done", "waiting_review"))
    assert r3["status"] == "done" and r3["nodes"]["b"]["choice"] == 1


def test_reject_and_bad_store_do_not_remember():
    r = wait(engine.start(wf("m4", candidates=2, seed=80))["id"], ("waiting_review",))
    cands = r["nodes"]["b"]["candidates"]
    engine.review(r["id"], "b", "reject", "都不好")
    assert cand.remembered(cands) is None
    # a corrupt or mismatched file is ignored, not trusted
    d = engine.CACHE / "_choices"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cand.set_key(cands)}.json").write_text("{not json")
    assert cand.remembered(cands) is None
    (d / f"{cand.set_key(cands)}.json").write_text(json.dumps({"choice": 5, "keys": []}))
    assert cand.remembered(cands) is None
    r2 = wait(engine.start(wf("m4", candidates=2, seed=80))["id"], ("waiting_review",))
    assert r2["nodes"]["b"]["status"] == "waiting_review"
