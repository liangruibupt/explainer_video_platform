"""HTTP routes for a job's 3D previs shots (docs/PREVIS_CONTRACT.md section 11, part B).

  GET  /api/jobs/{id}/previs                   plan + per shot: resolved_url, report, stills,
                                               plan_png, clip_url, status
  POST /api/jobs/{id}/previs/{scene}/render    recompile + re-render this one shot (background)
  PUT  /api/jobs/{id}/previs/{scene}           {"shot": ShotSpec} replaces the shot, recompiles,
                                               returns the new check report (no render)
  GET  /api/jobs/{id}/previs/files/{name}      previs/<name> (resolved json, report, plan png/svg)
  GET  /api/jobs/{id}/previs/stills/{name}     previs/stills/<name>
  GET  /api/jobs/{id}/previs/{scene}/clip      shots/<scene>.mp4

main.py includes `router` (integration).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from .. import pipeline
from . import project as pproject
from . import shots

router = APIRouter()
JOB_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
SCENE_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}")
MEDIA = {".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml", ".mp4": "video/mp4"}
_busy: set = set()
_lock = threading.Lock()


def _job(job_id: str) -> Path:
    if not JOB_RE.fullmatch(job_id or ""):
        raise HTTPException(404, "no such job")
    d = pipeline.job_dir(job_id)
    if not (d / "previs" / "plan.json").exists():
        raise HTTPException(404, "this job has no previs plan")
    return d


def _plan(d: Path) -> dict:
    return json.loads((d / "previs" / "plan.json").read_text())


def _scene(d: Path, scene: str) -> dict:
    if not SCENE_RE.fullmatch(scene or ""):
        raise HTTPException(404, "no such scene")
    s = next((s for s in _plan(d)["shots"] if s["id"] == scene), None)
    if not s:
        raise HTTPException(404, "no such shot")
    return s


def _read(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def _prev(plan: dict, scene: str) -> str | None:
    """The previous shot of the same set (axis_crossing is checked against it)."""
    prev = None
    me = next(s for s in plan["shots"] if s["id"] == scene)
    for s in plan["shots"]:
        if s["id"] == scene:
            return prev
        if s["set"] == me["set"]:
            prev = s["id"]
    return prev


@router.get("/api/jobs/{job_id}/previs")
def get_previs(job_id: str):
    d = _job(job_id)
    plan = _plan(d)
    status = _read(d / "previs" / "status.json", {}) or {}
    base = f"/api/jobs/{job_id}/previs"
    out = []
    for s in plan["shots"]:
        sid = s["id"]
        st = dict(status.get(sid, {}))
        stills = sorted((d / "previs" / "stills").glob(f"{sid}.f*.png"),
                        key=lambda p: int(p.stem.rsplit(".f", 1)[1]))
        has = lambda n: (d / "previs" / n).exists()  # noqa: E731
        if (str(d), sid) in _busy:
            st["status"] = "rendering"
        out.append({
            "scene": sid,
            "resolved_url": f"{base}/files/{sid}.json" if has(f"{sid}.json") else None,
            "report": _read(d / "previs" / f"{sid}.report.json", []),
            "stills": [f"{base}/stills/{p.name}" for p in stills],
            "plan_png": f"{base}/files/{sid}.plan.png" if has(f"{sid}.plan.png") else None,
            "clip_url": f"{base}/{sid}/clip" if (d / "shots" / f"{sid}.mp4").exists() else None,
            "status": st.get("status") or ("compiled" if has(f"{sid}.json") else "pending"),
            "seconds": st.get("seconds"),
        })
    return {"plan": plan, "shots": out}


def _rerender(d: Path, sid: str) -> None:
    try:
        plan = _plan(d)
        r = shots.compile_shot(d, sid, prev=_prev(plan, sid), plan=plan)
        if r["errors"]:
            shots._status(d, {sid: {"status": "blocked", "errors": r["errors"]}})
            return
        proj = d / "remotion"
        if not (proj / "src" / "Root.tsx").exists():
            shots.write_project(d)
        else:
            pproject.write_previs(proj, {sid: d / "previs" / f"{sid}.json"}, shots.glbs_for(d))
        shots.render_shot(d, sid)
    except Exception as e:  # noqa: BLE001 - recorded for the review page
        shots._status(d, {sid: {"status": "failed", "error": str(e)[:500]}})
    finally:
        with _lock:
            _busy.discard((str(d), sid))


@router.post("/api/jobs/{job_id}/previs/{scene}/render", status_code=202)
def render_scene(job_id: str, scene: str):
    d = _job(job_id)
    _scene(d, scene)
    with _lock:
        if (str(d), scene) in _busy:
            raise HTTPException(409, "this shot is already rendering")
        _busy.add((str(d), scene))
    shots._status(d, {scene: {"status": "queued"}})
    threading.Thread(target=_rerender, args=(d, scene), daemon=True).start()
    return {"scene": scene, "status": "queued"}


@router.put("/api/jobs/{job_id}/previs/{scene}")
def put_scene(job_id: str, scene: str, body: dict = Body(...)):
    d = _job(job_id)
    _scene(d, scene)
    shot = body.get("shot")
    if not isinstance(shot, dict):
        raise HTTPException(422, "body must be {\"shot\": ShotSpec}")
    if shot.get("id", scene) != scene:
        raise HTTPException(422, f"shot.id must be {scene}")
    shot["id"] = scene
    if not isinstance(shot.get("camera"), dict) or not isinstance(shot["camera"].get("keys"), list):
        raise HTTPException(422, "shot.camera.keys must be a list")
    plan = _plan(d)
    if not any(s["id"] == shot.get("set") for s in plan["sets"]):
        raise HTTPException(422, f"unknown set {shot.get('set')!r}")
    old = json.dumps(plan, ensure_ascii=False, indent=1)
    plan["shots"] = [shot if s["id"] == scene else s for s in plan["shots"]]
    pf = d / "previs" / "plan.json"
    (d / "previs" / "plan.prev.json").write_text(old)  # one-step undo copy
    pf.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    try:
        r = shots.compile_shot(d, scene, prev=_prev(plan, scene), plan=plan)
    except RuntimeError as e:
        raise HTTPException(422, f"compile failed: {e}") from e
    shots._status(d, {scene: {"status": "compiled", "errors": r["errors"], "warnings": r["warnings"]}})
    return {"scene": scene, "report": r["report"], "errors": r["errors"], "warnings": r["warnings"]}


def _file(p: Path) -> FileResponse:
    if not p.is_file() or p.suffix not in MEDIA:
        raise HTTPException(404, "not ready")
    return FileResponse(p, media_type=MEDIA[p.suffix])


@router.get("/api/jobs/{job_id}/previs/files/{name}")
def previs_file(job_id: str, name: str):
    if not NAME_RE.fullmatch(name) or name.startswith("."):
        raise HTTPException(404, "no such file")
    return _file(_job(job_id) / "previs" / name)


@router.get("/api/jobs/{job_id}/previs/stills/{name}")
def previs_still(job_id: str, name: str):
    if not NAME_RE.fullmatch(name) or name.startswith("."):
        raise HTTPException(404, "no such file")
    return _file(_job(job_id) / "previs" / "stills" / name)


@router.get("/api/jobs/{job_id}/previs/{scene}/clip")
def previs_clip(job_id: str, scene: str):
    d = _job(job_id)
    _scene(d, scene)
    return _file(d / "shots" / f"{scene}.mp4")
