"""Phase 5 part C: skill-fix proposals (gitops + proposals).

Real git (a temporary bare repo as origin, a clone as the serving checkout, worktrees,
git apply, commit); llm.ask, git push, gh and the repro runner are stubbed.
"""
import json
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from app.pipeline import DATA


# ---------------------------------------------------------------- store stub (part A)
# Part A's app.feedback.store is used when present; otherwise this minimal stand-in with
# the contract's shapes (docs/PHASE5_CONTRACTS.md, Shared Python APIs).

def _stub_store():
    import re
    import secrets
    m = types.ModuleType("app.feedback.store")
    m.ROOT = DATA / "feedback"
    _locks, _g = {}, threading.Lock()
    rx = re.compile(r"^(fb|an|pr)_\d{8}-\d{6}-[0-9a-f]{6}$")

    def folder(prefix):
        return m.ROOT / {"fb": "items", "an": "analyses", "pr": "proposals"}[prefix]

    def path(obj_id, prefix):
        if not m.valid_id(obj_id, prefix):
            raise ValueError(obj_id)
        return folder(prefix) / f"{obj_id}.json"

    def read(p):
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def write(p, obj):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False))

    m.new_id = lambda prefix: f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    m.valid_id = lambda x, prefix="": isinstance(x, str) and bool(rx.match(x)) and (
        not prefix or x.startswith(prefix + "_"))

    def lock(obj_id):
        with _g:
            return _locks.setdefault(obj_id, threading.RLock())
    m.lock = lock

    def get(fb_id):
        try:
            return read(path(fb_id, "fb"))
        except ValueError:
            return None
    m.get = get

    def lst(prefix):
        return [x for x in (read(p) for p in sorted(folder(prefix).glob("*.json"))) if x] \
            if folder(prefix).exists() else []
    m.list_items = lambda status="", limit=200: [x for x in lst("fb")
                                                 if not status or x["status"] == status][:limit]

    def update(fb_id, fn):
        with lock(fb_id):
            it = get(fb_id)
            if it is None:
                raise KeyError(fb_id)
            fn(it)
            write(path(fb_id, "fb"), it)
            return it
    m.update = update
    m.add_history = lambda fb_id, ev, detail="": update(
        fb_id, lambda x: x.setdefault("history", []).append({"event": ev, "detail": detail}))

    def save(prefix):
        def f(obj):
            obj = dict(obj)
            obj.setdefault("id", m.new_id(prefix))
            obj.setdefault("created", time.time())
            write(path(obj["id"], prefix), obj)
            return obj
        return f
    m.save_analysis, m.save_proposal = save("an"), save("pr")
    m.get_analysis = lambda i: read(path(i, "an")) if m.valid_id(i, "an") else None
    m.get_proposal = lambda i: read(path(i, "pr")) if m.valid_id(i, "pr") else None
    m.list_proposals = lambda state="", limit=200: [x for x in lst("pr")
                                                    if not state or x["state"] == state][:limit]

    def create(target, reasons, text="", snapshot=None):
        it = {"id": m.new_id("fb"), "created": time.time(), "target": target,
              "reasons": reasons, "text": text, "snapshot": snapshot or {}, "status": "new",
              "analysis_id": None, "proposal_id": None, "resolution": None, "history": []}
        write(path(it["id"], "fb"), it)
        return it
    m.create = create
    return m


try:
    from app.feedback import store  # noqa: E402
except ImportError:  # part A not merged yet
    store = _stub_store()
    sys.modules["app.feedback.store"] = store
    import app.feedback as _pkg
    _pkg.store = store

from app import llm  # noqa: E402
from app.feedback import gitops, proposals  # noqa: E402

SKILL = "skills/explainer-video-pipeline"
CAPTIONS_PY = """import argparse
ap = argparse.ArgumentParser()
ap.add_argument('--max-chars', dest='max_chars', type=int, default=16)
ap.add_argument('--lead', type=int, default=450)
MIN_GAP = 0.25
a = ap.parse_args()
print(a.max_chars)
"""
PIPELINE_PY = """import os
AI_MUSIC_VOLUME = 0.35
def run():
    return os.getcwd()
"""


def g(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """bare origin with master; a clone as the live checkout (EVP_FEEDBACK_REPO)."""
    seed, bare, live = tmp_path / "seed", tmp_path / "origin.git", tmp_path / "live"
    (seed / SKILL / "scripts").mkdir(parents=True)
    (seed / "server" / "app").mkdir(parents=True)
    (seed / SKILL / "SKILL.md").write_text("# skill\n\nCaptions: at most 16 characters a line.\n")
    (seed / SKILL / "VERSION").write_text("1.0.0\n")
    (seed / SKILL / "scripts" / "captions.py").write_text(CAPTIONS_PY)
    (seed / "server" / "app" / "pipeline.py").write_text(PIPELINE_PY)
    (seed / "README.md").write_text("readme\n")
    g(tmp_path, "init", "-q", "-b", "master", str(seed))
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid")):
        g(seed, "config", k, v)
    g(seed, "add", "-A")
    g(seed, "commit", "-q", "-m", "init")
    g(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    g(tmp_path, "clone", "-q", str(bare), str(live))
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid")):
        g(live, "config", k, v)
    monkeypatch.setenv("EVP_FEEDBACK_REPO", str(live))
    monkeypatch.setenv("EVP_FEEDBACK_AUTOCHECK", "0")
    return types.SimpleNamespace(seed=seed, bare=bare, live=live)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    shutil.rmtree(DATA / "feedback", ignore_errors=True)
    shutil.rmtree(DATA / "tasks", ignore_errors=True)
    pushed, posted = [], []
    monkeypatch.setattr(gitops, "push", lambda pr_id, wt=None: pushed.append(pr_id)
                        or gitops.branch_name(pr_id))
    monkeypatch.setattr(gitops, "_gh", lambda args, root=None: pytest.fail(f"gh called {args}"))
    monkeypatch.setattr(llm, "ask", lambda *a, **k: pytest.fail("llm.ask not stubbed"))
    yield types.SimpleNamespace(pushed=pushed, posted=posted)
    shutil.rmtree(DATA / "feedback", ignore_errors=True)


JOB_REC = {"id": "20261104-080000-123456", "topic": "量子纠缠", "language": "中文（普通话）",
           "seconds": 30, "ratio": "16:9", "captions": True, "music": "none", "voice": "Zhiyu",
           "scenes": [{"id": "t", "title": "T", "narration": "量子纠缠是一种关联。", "visual": "",
                       "sfx": []}]}


def make_cluster(n=3, cluster="captions:caption_too_fast", text="字幕太快看不过来"):
    ids = []
    for _ in range(n):
        it = store.create({"kind": "job", "id": JOB_REC["id"]}, ["字幕"], text,
                          snapshot={"skill_version": "1.0.0", "skill_commit": "abc1234",
                                    "record": JOB_REC, "qc": {"caption_max_cps": 9.1},
                                    "files": {}})
        an = store.save_analysis({"feedback_id": it["id"], "verdict": "rule",
                                  "layer": "captions", "symptom": cluster.split(":")[1],
                                  "explanation": "字幕每秒字数过多", "evidence": ["caption_max_cps"],
                                  "action": {"type": "propose",
                                             "target_file": f"{SKILL}/scripts/captions.py",
                                             "intent": "每行字数更少"},
                                  "cluster": cluster, "cluster_count": n})
        store.update(it["id"], lambda x, a=an["id"]: x.update(analysis_id=a, status="clustered"))
        ids.append(it["id"])
    return ids


def model_reply(edits=None, target=f"{SKILL}/scripts/captions.py", **extra):
    out = {"target_file": target,
           "edits": edits or [{"old": "type=int, default=16)", "new": "type=int, default=10)"}],
           "reason": "每行字数从 16 降到 10，减轻阅读压力。", "target_metric": "caption_max_cps"}
    out.update(extra)
    return json.dumps(out, ensure_ascii=False)


def stub_llm(monkeypatch, reply):
    seen = []
    monkeypatch.setattr(llm, "ask", lambda system, user, max_tokens=0: seen.append(user) or reply)
    return seen


def stub_repro(monkeypatch, before, after):
    """run_repro/measure stubs: QC depends on which tree ran the repro."""
    def run(tree, data_dir, repro, mode):
        data_dir.mkdir(parents=True, exist_ok=True)
        side = "after" if data_dir.name == "after" else "before"
        if data_dir.name.startswith("verify"):
            side = "verify"
        (data_dir / "side").write_text(side)
        (data_dir / "tree").write_text(str(tree))
        return {"job_id": f"job-{side}", "status": "done", "target": {"kind": "job",
                                                                       "id": f"job-{side}"}}

    def meas(data_dir, target):
        side = (data_dir / "side").read_text()
        return {"before": before, "after": after, "verify": after}[side]
    monkeypatch.setattr(proposals, "run_repro", run)
    monkeypatch.setattr(proposals, "measure", meas)


# ---------------------------------------------------------------- gitops: rejection

def test_disallowed_path_rejected_before_git(repo, monkeypatch):
    bad = ("--- a/server/app/main.py\n+++ b/server/app/main.py\n@@ -1 +1 @@\n-x\n+y\n")
    calls = []
    monkeypatch.setattr(gitops, "_run", lambda *a, **k: calls.append(a) or pytest.fail("git"))
    with pytest.raises(gitops.DiffRejected, match="不允许"):
        gitops.validate_diff(bad)
    for d in ("--- a/../etc/passwd\n+++ b/../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
              "--- /dev/null\n+++ b/skills/explainer-video-pipeline/SKILL.md\n@@ -0,0 +1 @@\n+y\n",
              "--- a/skills/explainer-video-pipeline/scripts/sub/x.py\n"
              "+++ b/skills/explainer-video-pipeline/scripts/sub/x.py\n@@ -1 +1 @@\n-x\n+y\n"):
        with pytest.raises(gitops.DiffRejected):
            gitops.validate_diff(d)
    assert calls == []


def test_oversized_diff_rejected():
    big = "".join(f"-line {i}\n+line {i}!\n" for i in range(25))
    d = f"--- a/{SKILL}/SKILL.md\n+++ b/{SKILL}/SKILL.md\n@@ -1,25 +1,25 @@\n{big}"
    with pytest.raises(gitops.DiffRejected, match="超过上限"):
        gitops.validate_diff(d)
    with pytest.raises(gitops.DiffRejected, match="字节"):
        gitops.validate_diff(d + "+" + "x" * gitops.MAX_DIFF_BYTES)
    assert gitops.validate_diff(f"--- a/{SKILL}/SKILL.md\n+++ b/{SKILL}/SKILL.md\n"
                                "@@ -1 +1 @@\n-a\n+b\n") == [f"{SKILL}/SKILL.md"]


def test_branch_name_validated():
    assert gitops.branch_name("pr_20261105-021713-abc123") == "skillfix/pr_20261105-021713-abc123"
    for bad in ("pr_2026-1", "pr_20261105-021713-abc123;rm", "../pr_20261105-021713-abc123",
                "pr_20261105-021713-ABC123", "", None, "fb_20261105-021713-abc123"):
        with pytest.raises(ValueError):
            gitops.branch_name(bad)


def test_semantic_scope_of_python_edits():
    p = f"{SKILL}/scripts/captions.py"
    gitops.check_semantics(p, CAPTIONS_PY, CAPTIONS_PY.replace("default=16", "default=10"))
    gitops.check_semantics(p, CAPTIONS_PY, CAPTIONS_PY.replace("MIN_GAP = 0.25", "MIN_GAP = 0.4"))
    with pytest.raises(gitops.DiffRejected, match="超出允许范围"):
        gitops.check_semantics(p, CAPTIONS_PY, CAPTIONS_PY.replace("print(a.max_chars)",
                                                                   "print(open('/x').read())"))
    with pytest.raises(gitops.DiffRejected):
        gitops.check_semantics(p, CAPTIONS_PY, CAPTIONS_PY.replace("default=16",
                                                                   "default=__import__('os')"))
    pp = "server/app/pipeline.py"
    gitops.check_semantics(pp, PIPELINE_PY, PIPELINE_PY.replace("0.35", "0.25"))
    with pytest.raises(gitops.DiffRejected):
        gitops.check_semantics(pp, PIPELINE_PY, PIPELINE_PY.replace("os.getcwd()", "1"))
    llm_src = 'MODEL_ID = "m"\nSYSTEM = """a"""\ndef f():\n    return 1\n'
    gitops.check_semantics("server/app/llm.py", llm_src, llm_src.replace('"""a"""', '"""b"""'))
    with pytest.raises(gitops.DiffRejected):
        gitops.check_semantics("server/app/llm.py", llm_src, llm_src.replace('"m"', '"n"'))
    with pytest.raises(gitops.DiffRejected, match="JSON"):
        gitops.check_semantics(f"{SKILL}/scripts/pronounce.json", "{}", "{bad")


# ---------------------------------------------------------------- draft

def test_draft_creates_worktree_commit_and_bumps_version(repo, monkeypatch):
    fb = make_cluster()
    seen = stub_llm(monkeypatch, model_reply())
    p = proposals.draft("captions:caption_too_fast")
    assert p["state"] == "checking", p.get("error")
    assert gitops.valid_id(p["id"]) and p["branch"] == f"skillfix/{p['id']}"
    assert p["target_files"] == [f"{SKILL}/scripts/captions.py"]
    assert p["feedback_ids"] == fb or sorted(p["feedback_ids"]) == sorted(fb)
    assert p["repro"]["kind"] == "job" and p["repro"]["input"]["topic"] == "量子纠缠"
    assert p["repro"]["scenes"][0]["narration"]
    wt = Path(p["worktree"])
    assert wt == gitops.WORKTREES / p["id"] and wt.is_dir()
    assert g(wt, "rev-parse", "--abbrev-ref", "HEAD") == p["branch"]
    assert "default=10" in (wt / SKILL / "scripts" / "captions.py").read_text()
    # VERSION bump in the same commit, base = origin/master
    files = g(wt, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == sorted([f"{SKILL}/VERSION", f"{SKILL}/scripts/captions.py"])
    assert (wt / SKILL / "VERSION").read_text().strip() == "1.0.1"
    assert p["skill_version_to"] == "1.0.1"
    assert g(wt, "rev-parse", "HEAD~1") == g(repo.live, "rev-parse", "origin/master")
    assert "captions:caption_too_fast" in g(wt, "log", "-1", "--format=%s")
    # the live checkout is untouched; feedback text reached the model only as quoted JSON
    assert g(repo.live, "status", "--porcelain") == ""
    assert "default=16" in (repo.live / SKILL / "scripts" / "captions.py").read_text()
    assert json.dumps("字幕太快看不过来", ensure_ascii=False) in seen[0]
    it = store.get(fb[0])
    assert it["proposal_id"] == p["id"] and it["status"] == "proposed"
    # a second draft of the same cluster returns the active proposal
    assert proposals.draft("captions:caption_too_fast")["id"] == p["id"]


def test_draft_needs_rule_min_unless_forced(repo, monkeypatch):
    make_cluster(n=2)
    stub_llm(monkeypatch, model_reply())
    with pytest.raises(ValueError, match="需要 3 条"):
        proposals.draft("captions:caption_too_fast")
    p = proposals.draft("captions:caption_too_fast", force=True)
    assert p["state"] == "checking" and p["forced"]


def test_draft_rejects_disallowed_target_without_worktree(repo, monkeypatch):
    make_cluster()
    stub_llm(monkeypatch, model_reply(target="server/app/main.py",
                                      edits=[{"old": "x", "new": "y"}]))
    p = proposals.draft("captions:caption_too_fast")
    assert p["state"] == "failed" and "不允许" in p["error"]
    assert not (gitops.WORKTREES / p["id"]).exists()
    assert "skillfix" not in g(repo.live, "branch", "--list", "skillfix/*")


def test_draft_rejects_code_change_and_cleans_up(repo, monkeypatch):
    make_cluster()
    stub_llm(monkeypatch, model_reply(edits=[{"old": "print(a.max_chars)",
                                              "new": "print(open('/etc/hosts').read())"}]))
    p = proposals.draft("captions:caption_too_fast")
    assert p["state"] == "failed" and "超出允许范围" in p["error"]
    assert not (gitops.WORKTREES / p["id"]).exists()
    assert g(repo.live, "worktree", "list").count("\n") == 0


def test_draft_rejects_raw_oversized_diff(repo, monkeypatch):
    make_cluster()
    big = "".join(f"+extra {i}\n" for i in range(45))
    diff = f"--- a/{SKILL}/SKILL.md\n+++ b/{SKILL}/SKILL.md\n@@ -1,1 +1,46 @@\n # skill\n{big}"
    stub_llm(monkeypatch, json.dumps({"target_file": f"{SKILL}/SKILL.md", "diff": diff,
                                      "reason": "r"}))
    p = proposals.draft("captions:caption_too_fast")
    assert p["state"] == "failed" and "超过上限" in p["error"]
    assert not gitops.WORKTREES.exists() or not any(gitops.WORKTREES.iterdir())


# ---------------------------------------------------------------- check / PR

def drafted(repo, monkeypatch):
    make_cluster()
    stub_llm(monkeypatch, model_reply())
    return proposals.draft("captions:caption_too_fast")


def test_not_improved_stays_draft(repo, monkeypatch, clean):
    p = drafted(repo, monkeypatch)
    stub_repro(monkeypatch, {"caption_max_cps": 8.0}, {"caption_max_cps": 8.02})
    monkeypatch.setattr(gitops, "_gh", lambda *a, **k: pytest.fail("PR must not open"))
    p = proposals.check(p["id"])
    assert p["state"] == "draft" and p["improved"] is False
    assert p["qc_before"] == {"caption_max_cps": 8.0}
    assert clean.pushed == []
    with pytest.raises(ValueError, match="没有改善"):
        proposals.open_pr(p["id"])
    from app.tasks import notify
    assert any("草稿" in n["title"] for n in notify.list_all())


def test_check_uses_base_tree_and_proposal_tree(repo, monkeypatch):
    p = drafted(repo, monkeypatch)
    stub_repro(monkeypatch, {"caption_max_cps": 9.0}, {"caption_max_cps": 6.0})
    p = proposals.check(p["id"], auto_open=False)
    d = proposals.CHECKS / p["id"]
    before_tree = Path((d / "before" / "tree").read_text())
    after_tree = Path((d / "after" / "tree").read_text())
    assert after_tree == Path(p["worktree"])
    assert before_tree == gitops.WORKTREES / (p["id"] + ".base")
    assert g(before_tree, "rev-parse", "HEAD") == p["base_sha"]
    assert "default=16" in (before_tree / SKILL / "scripts" / "captions.py").read_text()
    assert p["state"] == "draft" and p["improved"] is True


def test_improved_opens_pr_with_body(repo, monkeypatch, clean):
    p = drafted(repo, monkeypatch)
    stub_repro(monkeypatch, {"caption_max_cps": 9.1, "caption_count": 12, "failed_steps": []},
               {"caption_max_cps": 6.4, "caption_count": 18, "failed_steps": []})
    monkeypatch.setattr(gitops, "origin_repo", lambda root=None: ("o", "r"))
    req = {}

    def gh(args, root=None):
        assert args[:3] == ["-X", "POST", "repos/o/r/pulls"]
        req.update(json.loads(Path(args[-1]).read_text()))
        return {"number": 41, "html_url": "https://github.com/o/r/pull/41"}
    monkeypatch.setattr(gitops, "_gh", gh)
    p = proposals.check(p["id"])
    assert p["state"] == "open" and p["pr_number"] == 41 and p["improved"]
    assert clean.pushed == [p["id"]]
    assert req["head"] == f"skillfix/{p['id']}" and req["base"] == "master"
    body = req["body"]
    assert proposals.REQUIRED_LINE in body
    assert "| 指标 | 修改前 | 修改后 |" in body
    assert "| **caption_max_cps**（目标） | 9.1 | 6.4 |" in body
    assert "> 字幕太快看不过来" in body
    assert "job-before" in body and "job-after" in body and JOB_REC["id"] in body
    assert "量子纠缠" in body and "default=10" in body
    assert "merge" not in json.dumps(req).lower().replace("merged", "")


def test_pr_body_quotes_feedback_as_data(repo, monkeypatch):
    make_cluster(text="@someone <script>x</script>\n第二行")
    stub_llm(monkeypatch, model_reply())
    p = proposals.draft("captions:caption_too_fast")
    p.update(qc_before={"caption_max_cps": 9}, qc_after={"caption_max_cps": 6})
    body = proposals.pr_body(p)
    assert "> @\u200bsomeone &lt;script&gt;x&lt;/script&gt;\n> 第二行" in body
    assert "<script>" not in body


# ---------------------------------------------------------------- sync / verify

def opened(repo, monkeypatch):
    p = drafted(repo, monkeypatch)
    stub_repro(monkeypatch, {"caption_max_cps": 9.1}, {"caption_max_cps": 6.4})
    p = proposals.check(p["id"], auto_open=False)
    return store.save_proposal(dict(p, state="open", pr_number=7,
                                    pr_url="https://github.com/o/r/pull/7"))


def gh_state(monkeypatch, state, sha=None):
    monkeypatch.setattr(gitops, "origin_repo", lambda root=None: ("o", "r"))
    monkeypatch.setattr(gitops, "_gh", lambda args, root=None: {
        "state": "closed" if state != "open" else "open", "merged": state == "merged",
        "merge_commit_sha": sha, "html_url": "https://github.com/o/r/pull/7"})


def test_sync_open_and_closed(repo, monkeypatch):
    p = opened(repo, monkeypatch)
    gh_state(monkeypatch, "open")
    assert proposals.sync(p["id"])["state"] == "open"
    gh_state(monkeypatch, "closed")
    p = proposals.sync(p["id"])
    assert p["state"] == "closed" and not Path(p["worktree"]).exists()
    assert not (gitops.WORKTREES / (p["id"] + ".base")).exists()


def test_sync_merged_waiting_for_update(repo, monkeypatch):
    p = opened(repo, monkeypatch)
    head0 = g(repo.live, "rev-parse", "HEAD")
    gh_state(monkeypatch, "merged", p["commit"])
    p = proposals.sync(p["id"])
    assert p["state"] == "merged"
    assert p["verify"]["status"] == "waiting_for_update" and p["verify"]["ok"] is False
    assert g(repo.live, "rev-parse", "HEAD") == head0  # never pulled or modified
    assert not Path(p["worktree"]).exists()
    it = store.get(p["feedback_ids"][0])
    assert it["resolution"]["status"] == "waiting_for_update" and it["status"] == "merged"


def test_sync_merged_verifies_on_live_checkout(repo, monkeypatch):
    p = opened(repo, monkeypatch)
    gh_state(monkeypatch, "merged", p["commit"])
    proposals.sync(p["id"])  # waiting first
    g(repo.live, "merge", "--ff-only", "-q", p["commit"])  # a person updates the checkout
    p = proposals.sync(p["id"])
    v = p["verify"]
    assert v["status"] == "done" and v["ok"] is True and v["rerun_id"] == "job-verify"
    assert v["after"] == 6.4 and v["before"] == 9.1
    tree = Path(next((proposals.CHECKS / p["id"]).glob("verify-*/tree")).read_text())
    assert tree == repo.live.resolve()
    for fid in p["feedback_ids"]:
        it = store.get(fid)
        assert it["status"] == "resolved" and it["resolution"]["rerun_id"] == "job-verify"
        assert it["resolution"]["pr_url"] == "https://github.com/o/r/pull/7"
    from app.tasks import notify
    assert any("验证通过" in n["title"] for n in notify.list_all())


# ---------------------------------------------------------------- helpers

def test_is_improved_rules():
    assert proposals.is_improved("caption_max_cps", {"caption_max_cps": 9}, {"caption_max_cps": 7})
    assert not proposals.is_improved("caption_max_cps", {"caption_max_cps": 9},
                                     {"caption_max_cps": 8.99})
    assert proposals.is_improved("voice_minus_music_db", {"voice_minus_music_db": 3},
                                 {"voice_minus_music_db": 9})
    assert proposals.is_improved("duration_ratio", {"duration_ratio": 1.4},
                                 {"duration_ratio": 0.95})
    assert proposals.is_improved("failed_steps", {"failed_steps": ["mix"]}, {"failed_steps": []})
    assert not proposals.is_improved("caption_max_cps", {}, {"caption_max_cps": 1})
    assert proposals.metric_for("music_over_voice") == "voice_minus_music_db"
    assert proposals.metric_for("caption_too_fast") == "caption_max_cps"


def test_make_diff_applies_cleanly(repo):
    diff = proposals.make_diff(f"{SKILL}/scripts/captions.py", CAPTIONS_PY,
                               [{"old": "default=450", "new": "default=300"}])
    assert gitops.validate_diff(diff) == [f"{SKILL}/scripts/captions.py"]
    with pytest.raises(gitops.DiffRejected, match="恰好 1 次"):
        proposals.make_diff("x", "aa", [{"old": "a", "new": "b"}])


def test_cheap_repro_from_workflow():
    wf = {"nodes": [{"id": "n1", "type": "input.text", "params": {"text": "天空为什么是蓝的"}},
                    {"id": "n2", "type": "explainer.video",
                     "params": {"seconds": 40, "music": "ai", "captions": True}}],
          "edges": [{"from": "n1.text", "to": "n2.topic"}]}
    r = proposals._cheap_repro({"kind": "workflow", "input": wf})
    assert r["kind"] == "job" and r["input"]["topic"] == "天空为什么是蓝的"
    assert r["input"]["seconds"] == 40
