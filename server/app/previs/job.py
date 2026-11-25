"""The previs3d steps of an explainer job (docs/PREVIS_CONTRACT.md section 2), called by
pipeline._run when job["renderer"] == "previs3d":

  plan_step(job, d)            storyboard -> previs/plan.json (C; or the job's fixed plan)
  assets_step(job, d, plan)    build every plan asset (A parametric, E image-to-3D, library)
                               -> previs/assets.json (set-object asset string -> {dir, id})
  shots_step(job, d)           compile + check every shot (B); a shot with check errors goes
                               back to Opus with the report (max 2 rounds), then becomes an
                               automatic orbit shot (section 8) -> previs/shot_fix.json
  project_step(job, d)         the Remotion project (B) + captions / voice (remotion media)
  render_shots_step(job, d)    shots/sNN.mp4 per shot (B)
  render_main_step(job, d)     final.mp4 (Main: every shot + captions + voice + sfx)

Every model call and builder is the parts' own default; tests stub them on the modules.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from . import plan as pplan
from . import shots
from .api import _prev

SHOT_ROUNDS = 2  # Opus rewrites of a shot with check errors (contract section 8)
SHOT_TOKENS = 8000
FIX_NOTE = """You wrote the PrevisPlan below. The shot compiler checked every frame of shot __SID__
and found errors, so the shot cannot be rendered. Return ONLY the corrected ShotSpec JSON for
__SID__ (same id and set; keep the word anchors that still work; change what the errors need).
Check reports use the codes of contract section 8: camera_inside = the camera is inside a part
box (move it farther out); look_out_of_frame = the look point projects outside the frame;
camera_jump = a non-cut camera move over 15 % of the scene size or 25 degrees in one frame (add
"cut": true or smooth the keys); anchor_not_found = the word is not in this scene's narration."""


def _read(p: Path, default=None):
    return json.loads(Path(p).read_text()) if Path(p).exists() else default


def _write(p: Path, v) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=1))


def library_models() -> list:
    from .plan_node import _library
    return _library()


# ---------------------------------------------------------------- plan (C)

def plan_step(job: dict, d: Path, note: Callable[[str], None] = lambda m: None) -> dict:
    """previs/plan.json. A job created with a fixed plan (previs_plan) keeps it after the
    static validation; otherwise Opus writes it (up to 3 rounds)."""
    d = Path(d)
    scenes = job["scenes"]
    pf = d / "previs" / "plan.json"
    lib = library_models()
    if pf.exists():  # fixed plan given at job creation
        plan = _read(pf)
        issues = pplan.validate(plan, scenes, lib)
        errs = [i for i in issues if i["severity"] == "error"]
        if errs:
            raise ValueError("给定的 previs 规划没有通过校验：" + "; ".join(i["message"] for i in errs[:5]))
        note(f"previs: fixed plan, {len(issues)} warning(s)")
        return {"plan": plan, "rounds": 0, "issues": issues, "fixed": True}
    meta: dict = {}
    plan = pplan.previs_plan(scenes, job["topic"], job.get("ratio", "16:9"), lib, meta=meta)
    _write(pf, plan)
    _write(d / "previs" / "plan_meta.json", meta)
    note(f"previs: plan in {meta.get('rounds')} round(s), {len(meta.get('issues') or [])} warning(s)")
    return {"plan": plan, "rounds": meta.get("rounds"), "issues": meta.get("issues"), "fixed": False}


# ---------------------------------------------------------------- assets (A / E)

def _image3d(d: Path, a: dict, note) -> dict:
    """E's flow (concept image -> mesh -> A's normalize) for one plan asset; library record."""
    from .. import mesh_api
    req, png = mesh_api.prepare({"prompt": a.get("prompt") or "", "image_asset": a.get("image_asset") or "",
                                 "target": a["target"], "name": a["id"]})
    return mesh_api.run_image3d(req, Path(d) / "previs" / "mesh" / a["id"], png=png, note=note)["asset"]


def assets_step(job: dict, d: Path, plan: dict, note: Callable[[str], None] = lambda m: None,
                shape: bool = True) -> dict:
    """Build every asset of the plan into the library and write previs/assets.json.
    Parametric: C's build_assets (A's builder, rewrite loop, shape check unless the plan is
    fixed) -> plan_check.json.
    image3d: E's run_image3d. library: the given id. Returns {"@id": library id}."""
    from ..assets import store
    d = Path(d)
    # a fixed plan (job created with previs_plan) is authoritative: no Opus shape rewrite, the
    # camera keys were written for exactly these dimensions
    shape = shape and not job.get("fixed_plan")
    report = pplan.build_assets(plan, job["scenes"], library_models(), shape=shape,
                                check_path=d / "previs" / "plan_check.json")
    ids = dict(report["asset_ids"])
    failed = [k for k, v in report["assets"].items() if not v.get("ok")]
    if failed:
        raise RuntimeError(f"3D 资产构建失败：{', '.join(failed)}（见 previs/plan_check.json）")
    for a in plan.get("assets", []):
        if a["kind"] == "image3d":
            note(f"previs: image-to-3D asset {a['id']}")
            ids[f"@{a['id']}"] = _image3d(d, a, note)["id"]
        elif a["kind"] == "library":
            if not store.get(a["asset"]):
                raise RuntimeError(f"素材库里没有 {a['asset']}")
            ids[f"@{a['id']}"] = a["asset"]
    _write(d / "previs" / "plan.json", plan)  # rewritten specs (C's loops) replace the old ones
    amap = {}
    for st in plan.get("sets", []):
        for o in st.get("objects", []):
            ref = o["asset"]
            aid = ids.get(ref) or ref.lstrip("@")
            amap[ref] = {"dir": str(store.asset_path(aid).parent), "id": aid}
    _write(d / "previs" / "assets.json", amap)
    note(f"previs: {len(ids)} asset(s): " + ", ".join(f"{k}={v}" for k, v in ids.items()))
    return ids


# ---------------------------------------------------------------- shots (B + C loop)

def _errors(report: list) -> list:
    return [i for i in report if i.get("severity") == "error"]


def rewrite_shot(plan: dict, sid: str, issues: list, scenes: list, ask=None) -> dict:
    """One Opus call: the plan + this shot's check errors -> a corrected ShotSpec."""
    ask = ask or pplan._ask
    user = (FIX_NOTE.replace("__SID__", sid)
            + "\n\nStoryboard (data):\n" + json.dumps([{k: s.get(k) for k in ("id", "narration")}
                                                       for s in scenes], ensure_ascii=False)
            + "\n\nPrevisPlan:\n" + json.dumps(plan, ensure_ascii=False)
            + "\n\nErrors of " + sid + ":\n" + pplan._issue_lines(issues))
    shot = pplan.parse(ask(pplan.system_prompt(library_models()), user, SHOT_TOKENS))
    shot["id"] = sid
    return shot


def orbit_shot(plan: dict, sid: str, d: Path) -> dict:
    """The automatic fallback (contract section 8): a slow orbit around the set's first object
    at 2.4 x its largest size, looking at its centre, no actions."""
    old = next(s for s in plan["shots"] if s["id"] == sid)
    st = next(s for s in plan["sets"] if s["id"] == old["set"])
    obj = st["objects"][0]
    amap = _read(Path(d) / "previs" / "assets.json", {}) or {}
    meta = _read(Path(amap[obj["asset"]]["dir"]) / "meta.json", {}) if obj["asset"] in amap else {}
    size = meta.get("size") or [1, 1, 1]
    scale = obj.get("scale", 1)
    r = round(2.4 * max(size) * scale, 3)
    h = size[1] * scale
    at = obj.get("at") or [0, 0, 0]
    look = [at[0], round(at[1] + h / 2, 3), at[2]]
    keys = [{"at": {"mark": "start"}, "orbit": {"around": look, "r": r, "az": 30, "el": 20},
             "look": look, "lens_mm": 35},
            {"at": {"mark": "end"}, "orbit": {"around": look, "r": r, "az": 100, "el": 20},
             "look": look, "lens_mm": 35, "ease": "linear"}]
    return {"id": sid, "set": old["set"], "camera": {"keys": keys}, "actions": [],
            "style": old.get("style") or {"look": "clay", "shadows": True}}


def shots_step(job: dict, d: Path, note: Callable[[str], None] = lambda m: None, ask=None) -> dict:
    """compile_all; shots with check errors: Opus rewrite (static validation + recompile), max
    SHOT_ROUNDS, then the orbit fallback. Writes previs/shot_fix.json; returns
    {sid: {"errors", "warnings", "rounds", "fallback"}}."""
    d = Path(d)
    scenes = job["scenes"]
    res = shots.compile_all(d)
    fix = {sid: {"rounds": 0, "fallback": False, "first_errors": _errors(r["report"])}
           for sid, r in res.items()}
    pf = d / "previs" / "plan.json"
    for sid in [s for s, r in res.items() if r["errors"]]:
        plan = _read(pf)
        r = res[sid]
        for k in range(1, SHOT_ROUNDS + 1):
            note(f"previs: {sid} has {r['errors']} check error(s), Opus rewrite {k}")
            try:
                shot = rewrite_shot(plan, sid, _errors(r["report"]), scenes, ask=ask)
            except Exception as e:  # noqa: BLE001 - a bad answer counts as a failed round
                note(f"previs: {sid} rewrite {k} failed: {e}")
                fix[sid]["rounds"] = k
                continue
            trial = dict(plan, shots=[shot if s["id"] == sid else s for s in plan["shots"]])
            # the other shots already passed, so any static error is the new shot's
            static = _errors(pplan.validate(trial, scenes, library_models()))
            fix[sid]["rounds"] = k
            if static:
                r = {"errors": len(static), "report": static}
                continue
            plan = trial
            _write(pf, plan)
            r = shots.compile_shot(d, sid, prev=_prev(plan, sid), plan=plan)
            if not r["errors"]:
                break
        if r["errors"]:
            note(f"previs: {sid} still has errors, automatic orbit shot")
            plan = dict(plan, shots=[orbit_shot(plan, sid, d) if s["id"] == sid else s
                                     for s in plan["shots"]])
            _write(pf, plan)
            r = shots.compile_shot(d, sid, prev=_prev(plan, sid), plan=plan)
            fix[sid]["fallback"] = True
        res[sid] = r
    out = {}
    for sid, r in res.items():
        out[sid] = {"errors": r["errors"], "warnings": r["warnings"], **fix[sid]}
    _write(d / "previs" / "shot_fix.json", out)
    shots._status(d, {sid: {"status": "blocked" if v["errors"] else "queued",
                            "errors": v["errors"], "warnings": v["warnings"]}
                      for sid, v in out.items()})
    bad = [sid for sid, v in out.items() if v["errors"]]
    if bad:
        raise RuntimeError(f"镜头检查仍有错误：{', '.join(bad)}（见 previs/*.report.json）")
    return out


# ---------------------------------------------------------------- project + renders (B)

def project_step(job: dict, d: Path, note: Callable[[str], None] = lambda m: None) -> Path:
    from .. import remotion
    proj = shots.write_project(d, log=note)
    remotion.prepare_media(job, Path(d), proj, note)
    return proj


def render_shots_step(job: dict, d: Path, on_shot: Callable[[str, dict], None] = lambda s, r: None) -> dict:
    out = {}
    for s in _read(Path(d) / "previs" / "plan.json")["shots"]:
        out[s["id"]] = shots.render_shot(d, s["id"])
        on_shot(s["id"], out[s["id"]])
    return out


def render_main_step(job: dict, d: Path) -> dict:
    return shots.render_main(d, log=Path(d) / "pipeline.log")

