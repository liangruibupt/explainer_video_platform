"""Phase 5 integration: parts A-D together on the real store and real qc.measure output.

A real job folder (real narration.wav from ffmpeg, a job.json 20% over its target) goes
through POST /api/feedback -> qc snapshot -> analyze (pre-check keys as qc returns them) ->
act -> proposals.draft on a real git repo -> check in storyboard mode (runner stubbed) ->
open?force / close over HTTP. llm.ask, git push and gh are stubbed.
"""
import json
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import llm, pipeline
from app.feedback import analyze, gitops, proposals, qc, store
from app.pipeline import DATA

SKILL = "skills/explainer-video-pipeline"
JOB = "20261103-031437-2ad1d8"
LLM_PY = 'MODEL_ID = "x"\nSTORYBOARD_SYSTEM = """Rules: 5-8 scenes; one idea per scene."""\n'


def g(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    bare, live = tmp_path / "origin.git", tmp_path / "live"
    g(tmp_path, "init", "-q", "--bare", "-b", "master", str(bare))
    g(tmp_path, "clone", "-q", str(bare), str(live))
    g(live, "config", "user.email", "t@example.com")
    g(live, "config", "user.name", "t")
    (live / "server" / "app").mkdir(parents=True)
    (live / "server" / "app" / "llm.py").write_text(LLM_PY)
    (live / SKILL).mkdir(parents=True)
    (live / SKILL / "SKILL.md").write_text("# skill\n")
    g(live, "add", "-A")
    g(live, "commit", "-qm", "seed")
    g(live, "push", "-q", "origin", "HEAD:refs/heads/master")
    monkeypatch.setenv("EVP_FEEDBACK_REPO", str(live))
    monkeypatch.setenv("EVP_FEEDBACK_AUTOCHECK", "0")
    monkeypatch.setattr(gitops, "push", lambda pr_id, wt=None: gitops.branch_name(pr_id))
    monkeypatch.setattr(gitops, "_gh", lambda args, root=None: pytest.fail(f"gh {args}"))
    return live


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for sub in ("feedback", "jobs", "tasks"):
        shutil.rmtree(DATA / sub, ignore_errors=True)
    monkeypatch.setattr(llm, "ask", lambda *a, **k: pytest.fail("llm.ask not stubbed"))
    yield
    for sub in ("feedback", "jobs"):
        shutil.rmtree(DATA / sub, ignore_errors=True)


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def real_job(seconds=45, dur=54.0):
    """A job folder with a real narration.wav (sine, dur seconds) and a job record."""
    d = DATA / "jobs" / JOB
    d.mkdir(parents=True)
    subprocess.run([pipeline.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    f"sine=f=220:d={dur}", "-ar", "24000", str(d / "narration.wav")],
                   check=True)
    steps = [{"key": k, "label": l, "status": "done"} for k, l in pipeline.STEPS]
    (d / "job.json").write_text(json.dumps({
        "id": JOB, "topic": "为什么天空是蓝色的", "language": "中文（普通话）", "seconds": seconds,
        "ratio": "16:9", "captions": False, "music": "none", "voice": "Zhiyu", "brief": "",
        "status": "done", "duration": dur, "steps": steps, "skill_version": "1.0.0",
        "scenes": [{"id": "t", "title": "T", "narration": "天空是蓝色的。", "visual": "",
                    "sfx": []}]}, ensure_ascii=False))
    return d


def test_qc_keys_drive_analyze_prechecks():
    real_job()
    q = qc.measure({"kind": "job", "id": JOB})
    assert q["target_s"] == 45 and q["duration_ratio"] > 1.15
    assert q["failed_steps"] == []  # qc's shape: a list of step keys
    it = store.create({"kind": "job", "id": JOB}, ["节奏"], "视频比我要的时长长了不少")
    assert it["snapshot"]["qc"]["duration_ratio"] == q["duration_ratio"]
    hits = analyze.prechecks(it)
    assert [f"{h['layer']}:{h['symptom']}" for h in hits] == ["storyboard:overrun_duration"]
    # failed_steps / node_errors exactly as qc.measure returns them
    item = {"snapshot": {"qc": {"failed_steps": ["captions"]}, "record": {}}}
    assert analyze.prechecks(item)[0]["symptom"] == "failed_captions"
    item = {"snapshot": {"qc": {"node_errors": {"n3": "GPU timeout"}},
                         "record": {"nodes": {"n3": {"type": "image.qwen"}}}}}
    assert analyze.prechecks(item)[0]["symptom"] == "failed_image_qwen"


def make_three(client, monkeypatch):
    real_job()
    monkeypatch.setattr(llm, "ask", lambda *a, **k: "not json")  # -> deterministic pre-check
    ids = []
    for txt in ("视频比我要的时长长了不少", "太长了", "超时了"):
        r = client.post("/api/feedback", json={"target": {"kind": "job", "id": JOB},
                                               "reasons": ["节奏"], "text": txt})
        assert r.status_code == 200
        ids.append(r.json()["id"])
        for _ in range(100):
            if (store.get(ids[-1]) or {}).get("analysis_id"):
                break
            time.sleep(0.05)
    return ids


def test_three_items_cluster_draft_storyboard_check_close(repo, client, monkeypatch):
    ids = make_three(client, monkeypatch)
    cl = client.get("/api/feedback/clusters").json()
    assert cl[0]["cluster"] == "storyboard:overrun_duration" and cl[0]["count"] == 3
    assert cl[0]["rule_min"] == analyze.RULE_MIN == proposals.RULE_MIN
    edit = {"target_file": "server/app/llm.py",
            "edits": [{"old": "one idea per scene.", "new": "one idea per scene. "
                       "Total narration <= 4 Chinese characters per target second."}],
            "reason": "按目标时长限制旁白字数。", "target_metric": "duration_ratio"}
    monkeypatch.setattr(llm, "ask", lambda *a, **k: json.dumps(edit, ensure_ascii=False))
    r = client.post(f"/api/feedback/{ids[2]}/act").json()
    assert r["status"] == "proposed", r
    p = store.get_proposal(r["proposal_id"])
    assert p["target_files"] == ["server/app/llm.py"] and p["state"] == "checking"
    assert proposals.default_mode(p) == "storyboard"
    assert all(store.get(i)["status"] == "proposed" for i in ids)

    seen = []

    def run(tree, data_dir, repro, mode):
        seen.append((data_dir.name, mode))
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "r").write_text("1.05" if "after" in data_dir.name else "1.2")
        return {"job_id": data_dir.name, "status": "done",
                "target": {"kind": "job", "id": data_dir.name}}
    monkeypatch.setattr(proposals, "run_repro", run)
    monkeypatch.setattr(proposals, "measure", lambda d, t: {
        "duration_ratio": float((d / "r").read_text())})
    monkeypatch.setattr(proposals, "REPEATS", 2)
    p = proposals.check(p["id"], auto_open=False)
    assert sorted(seen) == [("after", "storyboard"), ("after-2", "storyboard"),
                            ("before", "storyboard"), ("before-2", "storyboard")]
    assert p["state"] == "draft" and p["improved"] is True
    assert p["qc_before"]["duration_ratio_runs"] == [1.2, 1.2]
    assert "每边 2 次" in proposals.pr_body(p)

    # not improved -> open refuses; open?force is the explicit override (gh stubbed)
    store.save_proposal(dict(p, improved=False))
    assert client.post(f"/api/feedback/proposals/{p['id']}/open").status_code == 409
    monkeypatch.setattr(gitops, "open_pr", lambda pr_id, title, body, root=None:
                        {"number": 99, "url": "https://github.com/o/r/pull/99"})
    monkeypatch.setattr(gitops, "origin_repo", lambda root=None: ("o", "r"))
    q = client.post(f"/api/feedback/proposals/{p['id']}/open?force=true").json()
    assert q["state"] == "open" and q["pr_number"] == 99
    # an opened PR cannot be closed locally
    assert client.post(f"/api/feedback/proposals/{p['id']}/close").status_code == 409


def test_close_removes_worktrees_and_checks_and_stale_cleanup(repo, client, monkeypatch):
    ids = make_three(client, monkeypatch)
    edit = {"target_file": "server/app/llm.py",
            "edits": [{"old": "5-8 scenes", "new": "4-6 scenes"}], "reason": "少一些分镜"}
    monkeypatch.setattr(llm, "ask", lambda *a, **k: json.dumps(edit, ensure_ascii=False))
    r = client.post(f"/api/feedback/{ids[0]}/draft").json()  # 立即起草 = act(force)
    assert r["status"] == "proposed"
    pid = r["proposal_id"]
    store.save_proposal(dict(store.get_proposal(pid), state="draft"))
    (proposals.CHECKS / pid / "before").mkdir(parents=True, exist_ok=True)
    wt = gitops.WORKTREES / pid
    assert wt.exists()
    p = client.post(f"/api/feedback/proposals/{pid}/close").json()
    assert p["state"] == "closed" and not wt.exists()
    assert not (proposals.CHECKS / pid).exists()
    assert g(repo, "branch", "--list", f"skillfix/{pid}") == ""  # unpushed branch dropped
    assert store.get(ids[0])["history"][-1]["event"] == "proposal_closed"
    # stale cleanup: a 15-day-old draft is closed, a fresh one is kept
    old = store.save_proposal({"id": store.new_id("pr"), "created": time.time() - 15 * 86400,
                               "cluster": "mix:x", "feedback_ids": [], "state": "draft"})
    new = store.save_proposal({"id": store.new_id("pr"), "created": time.time(),
                               "cluster": "mix:y", "feedback_ids": [], "state": "draft"})
    assert proposals.cleanup_stale() == [old["id"]]
    assert store.get_proposal(new["id"])["state"] == "draft"


def test_draft_endpoint_refuses_non_rule(client, monkeypatch):
    real_job(dur=45.0)  # on target: no pre-check hit
    one_off = {"verdict": "one_off", "layer": "voice", "symptom": "misread_word",
               "explanation": "偶然", "evidence": [], "action": {"type": "rerun",
                                                                 "step": "narrate",
                                                                 "changes": {}}}
    monkeypatch.setattr(llm, "ask", lambda *a, **k: json.dumps(one_off, ensure_ascii=False))
    it = store.create({"kind": "job", "id": JOB}, ["声音"], "读错了")
    analyze.analyze(it["id"])
    assert client.post(f"/api/feedback/{it['id']}/draft").status_code == 409


def test_main_includes_feedback_router(client):
    assert client.get("/api/feedback/skill").status_code == 200
    assert client.get("/api/feedback/clusters").json() == []
