"""HTTP API of feedback-driven skill improvement: /api/feedback/* (docs/PHASE5_CONTRACTS.md).

analyze.py (part B) and proposals.py (part C) are imported lazily: an endpoint that needs a
module that is not there yet answers 501, and POST /api/feedback then only records the item.
Long work (analysis, a proposal check) runs in a background thread; the item / proposal
record shows progress.
"""
import importlib
import logging
import threading
import traceback

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import skill_commit, skill_version, store

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
log = logging.getLogger(__name__)


def _mod(name: str, required: bool = True):
    """app.feedback.<name>, or None (501 when required) when the module is not built."""
    full = f"app.feedback.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name != full:
            raise
        if required:
            raise HTTPException(501, f"{name} 模块尚未集成（{full}）")
        return None


def _bg(label: str, fb_id: str, fn, *args) -> None:
    def run():
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 - recorded on the item
            log.warning("feedback %s failed: %s", label, traceback.format_exc()[-1500:])
            if fb_id:
                try:
                    store.add_history(fb_id, f"{label}_failed", str(e)[:300])
                except Exception:  # noqa: BLE001
                    pass
    threading.Thread(target=run, daemon=True, name=f"feedback-{label}").start()


def _item(fb_id: str) -> dict:
    if not store.valid_id(fb_id, "fb"):
        raise HTTPException(404, "feedback not found")
    it = store.get(fb_id)
    if not it:
        raise HTTPException(404, "feedback not found")
    return it


def _proposal(pr_id: str) -> dict:
    p = store.get_proposal(pr_id) if store.valid_id(pr_id, "pr") else None
    if not p:
        raise HTTPException(404, "proposal not found")
    return p


class FeedbackIn(BaseModel):
    target: dict
    reasons: list[str] = Field(default_factory=list)
    text: str = Field(default="", max_length=2000)


@router.post("")
def create(body: FeedbackIn):
    try:
        item = store.create(body.target, body.reasons, body.text)
    except ValueError as e:
        raise HTTPException(422, str(e))
    analyze = _mod("analyze", required=False)
    if analyze is not None and hasattr(analyze, "analyze"):
        _bg("analysis", item["id"], analyze.analyze, item["id"])
        item["analysis_started"] = True
    return item


@router.get("")
def list_feedback(status: str = "", limit: int = 200):
    if status and status not in store.STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(store.STATUSES)}")
    return store.list_items(status, max(1, min(limit, 1000)))


@router.get("/clusters")
def clusters():
    groups = {}
    for a in store.list_analyses():
        c = a.get("cluster")
        if not c or a.get("verdict") != "rule":
            continue
        g = groups.setdefault(c, {"cluster": c, "count": 0, "feedback_ids": [],
                                  "proposal_id": None})
        fb = a.get("feedback_id")
        it = store.get(fb) if fb else None
        if not it or it.get("status") == "dismissed" or it.get("analysis_id") != a["id"]:
            continue  # only each item's current analysis counts
        if fb not in g["feedback_ids"]:
            g["feedback_ids"].append(fb)
    for p in store.list_proposals():
        g = groups.get(p.get("cluster"))
        if g and not g["proposal_id"]:
            g["proposal_id"] = p["id"]
    analyze_mod = _mod("analyze", required=False)
    out = []
    for g in groups.values():
        g["count"] = len(g["feedback_ids"])
        if analyze_mod is not None:  # the same count analyze.act() decides on
            g["count"] = analyze_mod.cluster_count(g["cluster"])
            g["rule_min"] = analyze_mod.RULE_MIN
        if g["count"]:
            out.append(g)
    return sorted(out, key=lambda g: g["count"], reverse=True)


@router.get("/skill")
def skill():
    merged = [{"id": p["id"], "cluster": p.get("cluster"), "reason": p.get("reason"),
               "pr_url": p.get("pr_url"), "pr_number": p.get("pr_number"),
               "target_files": p.get("target_files"), "created": p.get("created"),
               "verify": p.get("verify")}
              for p in store.list_proposals("merged")]
    return {"version": skill_version(), "commit": skill_commit(), "history": merged}


@router.get("/proposals")
def proposals(state: str = ""):
    return store.list_proposals(state)


@router.get("/proposals/{pr_id}")
def proposal(pr_id: str):
    return _proposal(pr_id)


@router.post("/proposals/{pr_id}/check", status_code=202)
def proposal_check(pr_id: str):
    _proposal(pr_id)
    mod = _mod("proposals")
    _bg("check", "", mod.check, pr_id)
    return {"id": pr_id, "started": True}


@router.post("/proposals/{pr_id}/open")
def proposal_open(pr_id: str, force: bool = False):
    """Push skillfix/<id> and open the PR (never merges). force=true only from the explicit
    仍然开 PR button, for a proposal whose target metric did not improve."""
    _proposal(pr_id)
    mod = _mod("proposals")
    try:
        return mod.open_pr(pr_id, force=force)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/proposals/{pr_id}/close")
def proposal_close(pr_id: str):
    """Drop a proposal that has no PR: worktrees and its check data are removed."""
    _proposal(pr_id)
    mod = _mod("proposals")
    try:
        return mod.close(pr_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/proposals/{pr_id}/sync")
def proposal_sync(pr_id: str):
    _proposal(pr_id)
    mod = _mod("proposals")
    try:
        return mod.sync(pr_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/{fb_id}")
def get_feedback(fb_id: str):
    it = _item(fb_id)
    an = store.get_analysis(it["analysis_id"]) if it.get("analysis_id") else None
    pr = store.get_proposal(it["proposal_id"]) if it.get("proposal_id") else None
    return {"item": it, "analysis": an, "proposal": pr}


@router.post("/{fb_id}/analyze", status_code=202)
def analyze(fb_id: str):
    _item(fb_id)
    mod = _mod("analyze")
    _bg("analysis", fb_id, mod.analyze, fb_id)
    return {"id": fb_id, "started": True}


@router.post("/{fb_id}/act")
def act(fb_id: str):
    it = _item(fb_id)
    mod = _mod("analyze")
    if not it.get("analysis_id"):
        raise HTTPException(409, "这条反馈还没有分析结果")
    try:
        return mod.act(it["analysis_id"])
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/{fb_id}/draft")
def draft(fb_id: str):
    """立即起草: analyze.act(force=True), so status, history and notify happen as for act."""
    it = _item(fb_id)
    mod = _mod("analyze")
    _mod("proposals")
    an = store.get_analysis(it["analysis_id"]) if it.get("analysis_id") else None
    if not an or not an.get("cluster"):
        raise HTTPException(409, "这条反馈还没有分析出问题类别，无法起草")
    if (an.get("action") or {}).get("type") != "propose":
        raise HTTPException(409, "这条反馈的分析结论不是规则问题，不能起草提案")
    try:
        return mod.act(an["id"], force=True)
    except (ValueError, LookupError) as e:
        raise HTTPException(409, str(e))


@router.post("/{fb_id}/dismiss")
def dismiss(fb_id: str):
    _item(fb_id)
    return store.set_status(fb_id, "dismissed", "用户忽略")
