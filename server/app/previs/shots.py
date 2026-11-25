"""Shot compile, checks and rendering for 3D previs jobs (docs/PREVIS_CONTRACT.md sections 8-9).

Job dir files (jobs/<id>/):
  previs/plan.json             PrevisPlan (part C)
  previs/assets.json           optional: set-object asset string -> library dir (or {dir, id});
                               default: "asset_..." -> data/assets/<id>, "@spec" -> the plan's
                               library asset, else data/assets/<spec id>
  words.json, durations.json, scenes.json    narration timing (captions.py / narrate.py)
  previs/<sid>.json            ResolvedShot          (compile_all)
  previs/<sid>.report.json     CheckIssue[]          (compile + per-frame checks + axis)
  previs/<sid>.plan.png/.svg   top-down plan image
  previs/stills/<sid>.f<N>.png 5 layout stills       (render_shot)
  shots/<sid>.mp4              standalone shot clip  (render_shot)
  previs/status.json           per shot: status, render seconds, errors

The compiler / checker is node: js/dist/shot.mjs (built from js/shot/*.ts by js/shot/build.sh).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .. import pipeline
from ..remotion import project as rproject
from ..remotion import render
from . import project as pproject

JS = Path(__file__).parent / "js"
BUNDLE = JS / "dist" / "shot.mjs"
NODE = os.environ.get("NODE", "node")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
GL = "swangle"  # the only WebGL backend that works on this box (contract section 10)
_build_lock = threading.Lock()
_render_lock = threading.Lock()  # one previs render at a time per process


def bundle() -> Path:
    """Build js/dist/shot.mjs when missing or older than any source under js/shot or js/core."""
    with _build_lock:
        srcs = [p for d in ("shot", "core") for p in (JS / d).glob("*.ts")]
        newest = max((p.stat().st_mtime for p in srcs), default=0)
        if not BUNDLE.exists() or BUNDLE.stat().st_mtime < newest:
            subprocess.run(["bash", str(JS / "shot" / "build.sh")], check=True, capture_output=True,
                           text=True, timeout=300)
        return BUNDLE


def node(*args, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, str(bundle()), *[str(a) for a in args]], capture_output=True,
                          text=True, timeout=timeout)


def _read(p: Path, default=None):
    return json.loads(Path(p).read_text()) if Path(p).exists() else default


def asset_map(job_dir: Path, plan: dict) -> dict:
    """Set-object asset string -> {"dir", "id"} for every object of every set in the plan."""
    job_dir = Path(job_dir)
    given = _read(job_dir / "previs" / "assets.json", {}) or {}
    lib = {f"@{a['id']}": a.get("asset") for a in plan.get("assets", []) if a.get("kind") == "library"}
    root = Path(os.environ.get("EVP_ASSETS", pipeline.DATA / "assets"))
    out = {}
    for st in plan.get("sets", []):
        for o in st.get("objects", []):
            ref = o["asset"]
            if ref in out:
                continue
            if ref in given:
                v = given[ref]
                out[ref] = {"dir": v, "id": ref.lstrip("@")} if isinstance(v, str) else {"id": ref.lstrip("@"), **v}
            elif ref.startswith("@") and lib.get(ref):
                out[ref] = {"dir": str(root / lib[ref]), "id": lib[ref]}
            else:
                out[ref] = {"dir": str(root / ref.lstrip("@")), "id": ref.lstrip("@")}
    return out


def _size(job: dict, size: str | None) -> str:
    if size:
        return size
    w, h = pipeline.SIZES.get(job.get("ratio", "16:9"), (1920, 1080))
    return f"{w}x{h}"


def plan_png(drawing: dict, out: Path) -> None:
    """Rasterise the plan drawing (check.ts plan()) with Pillow."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (drawing["width"], drawing["height"]), drawing["background"])
    dr = ImageDraw.Draw(im)
    fonts: dict[int, object] = {}

    def font(size):
        if size not in fonts:
            try:
                fonts[size] = ImageFont.truetype(FONT, size, index=2)  # face 2 = Noto Sans CJK SC
            except OSError:
                fonts[size] = ImageFont.load_default()
        return fonts[size]

    for it in drawing["items"]:
        t = it["t"]
        if t == "rect":
            dr.rectangle([it["x"], it["y"], it["x"] + it["w"], it["y"] + it["h"]], fill=it["fill"], outline=it["stroke"])
        elif t == "line":
            dr.line(it["pts"], fill=it["color"], width=int(it["width"]))
        elif t == "circle":
            r = it["r"]
            dr.ellipse([it["x"] - r, it["y"] - r, it["x"] + r, it["y"] + r], fill=it["fill"], outline=it["stroke"], width=2)
        elif t == "text":
            dr.text((it["x"], it["y"] - it["size"]), it["s"], fill=it["color"], font=font(it["size"]))
    im.save(out)


def compile_shot(job_dir: Path, sid: str, *, size: str | None = None, prev: str | None = None,
                 plan: dict | None = None) -> dict:
    """Compile + check one shot. Returns {"resolved", "report", "errors", "warnings", "plan_png"}."""
    d = Path(job_dir)
    pv = d / "previs"
    plan = plan or _read(pv / "plan.json")
    job = _read(d / "job.json", {}) or {}
    amap = asset_map(d, plan)
    (pv / "assets.json").write_text(json.dumps(amap, ensure_ascii=False, indent=1))
    order = d / "scenes.json"
    if not order.exists():
        order = pv / "order.json"
        order.write_text(json.dumps([{"id": s["id"]} for s in plan["shots"]]))
    plan_file = pv / "plan.json"
    if not plan_file.exists():
        plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    res, rep = pv / f"{sid}.json", pv / f"{sid}.compile.json"
    r = node("compile-shot", "--plan", plan_file, "--shot", sid, "--assets", pv / "assets.json",
             "--words", d / "words.json", "--durations", d / "durations.json",
             "--size", _size(job, size), "--order", order, "--out", res, "--report", rep)
    if r.returncode not in (0, 2):
        raise RuntimeError(f"compile-shot {sid} failed: {(r.stderr or r.stdout)[-800:]}")
    issues = _read(rep, [])
    args = ["check-shot", res, "--assets", pv / "assets.json"]
    if prev:
        args += ["--prev", pv / f"{prev}.json"]
    c = node(*args)
    if c.returncode not in (0, 2):
        raise RuntimeError(f"check-shot {sid} failed: {(c.stderr or c.stdout)[-800:]}")
    issues += json.loads(c.stdout or "[]")
    (pv / f"{sid}.report.json").write_text(json.dumps(issues, ensure_ascii=False, indent=1))
    p = node("plan-image", res, "--assets", pv / "assets.json", "--out", pv / f"{sid}.plan.json")
    png = pv / f"{sid}.plan.png"
    if p.returncode == 0:
        plan_png(_read(pv / f"{sid}.plan.json"), png)
    return {"resolved": str(res), "report": issues, "plan_png": str(png) if png.exists() else None,
            "errors": sum(1 for i in issues if i["severity"] == "error"),
            "warnings": sum(1 for i in issues if i["severity"] == "warn")}


def compile_all(job_dir: Path, size: str | None = None) -> dict:
    """Every shot of previs/plan.json: ResolvedShot + check report + plan image.
    Returns {scene id: compile_shot() result}. Axis crossing is checked against the previous
    shot of the same set."""
    d = Path(job_dir)
    plan = _read(d / "previs" / "plan.json")
    if not plan:
        raise FileNotFoundError(f"{d}/previs/plan.json missing")
    out, last_of_set = {}, {}
    for s in plan["shots"]:
        prev = last_of_set.get(s["set"])
        out[s["id"]] = compile_shot(d, s["id"], size=size, prev=prev, plan=plan)
        last_of_set[s["set"]] = s["id"]
    _status(d, {sid: {"compiled": time.time(), "errors": r["errors"], "warnings": r["warnings"]}
                for sid, r in out.items()})
    return out


def _status(d: Path, upd: dict) -> dict:
    p = Path(d) / "previs" / "status.json"
    st = _read(p, {}) or {}
    for k, v in upd.items():
        st.setdefault(k, {}).update(v)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    return st


def glbs_for(job_dir: Path) -> dict:
    """Library asset id -> GLB path for every asset in previs/assets.json."""
    amap = _read(Path(job_dir) / "previs" / "assets.json", {}) or {}
    return {v["id"]: Path(v["dir"]) / "file.glb" for v in amap.values()}


def write_project(job_dir: Path, log: Callable[[str], None] = lambda m: None) -> Path:
    """The job's Remotion project with every compiled shot (after compile_all)."""
    d = Path(job_dir)
    job = _read(d / "job.json", {}) or {}
    plan = _read(d / "previs" / "plan.json")
    scenes = _read(d / "scenes.json") or [{"id": s["id"], "title": s["id"]} for s in plan["shots"]]
    job.setdefault("scenes", scenes)
    frames = _read(d / "durations.json")
    w, h = map(int, _size(job, None).split("x"))
    shots = {s["id"]: d / "previs" / f"{s['id']}.json" for s in plan["shots"]}
    return pproject.write_project(job, d, frames, w, h, shots=shots, glbs=glbs_for(d), log=log)


def layout_frames(n: int) -> list[int]:
    return rproject.layout_frames(n)


def render_shot(job_dir: Path, sid: str, *, stills: bool = True, log: Path | None = None) -> dict:
    """Render shots/<sid>.mp4 (composition Shot-<sid>, no captions or audio) and its 5 layout
    stills. Refuses a shot whose report has an error. Returns {"clip", "stills", "seconds"}."""
    d = Path(job_dir)
    rep = _read(d / "previs" / f"{sid}.report.json", [])
    errs = [i for i in rep if i["severity"] == "error"]
    if errs:
        _status(d, {sid: {"status": "blocked", "errors": len(errs)}})
        raise ValueError(f"shot {sid} has {len(errs)} check error(s): {errs[0]['message']}")
    proj = d / "remotion"
    out = d / "shots" / f"{sid}.mp4"
    out.parent.mkdir(exist_ok=True)
    log = log or (d / "previs" / "render.log")
    _status(d, {sid: {"status": "rendering", "started": time.time()}})
    with _render_lock:
        t0 = time.time()
        try:
            render.render(proj, out, log, comp=pproject.comp_id(sid))
        except Exception as e:  # noqa: BLE001
            _status(d, {sid: {"status": "failed", "error": str(e)}})
            raise
        secs = round(time.time() - t0, 1)
        shots = []
        if stills:
            n = _read(d / "previs" / f"{sid}.json")["frames"]
            fr = layout_frames(n)
            shots = [d / "previs" / "stills" / f"{sid}.f{f}.png" for f in fr]
            render.stills(proj, fr, shots, log, comp=pproject.comp_id(sid), gl=GL)
            shots = [p for p in shots if p.exists()]
    _status(d, {sid: {"status": "done", "seconds": secs, "clip": str(out), "ended": time.time(),
                      "stills": [str(p) for p in shots]}})
    return {"clip": str(out), "stills": [str(p) for p in shots], "seconds": secs}


def render_main(job_dir: Path, log: Path | None = None) -> dict:
    """The whole film (Main: every scene + captions + voice + sfx) -> final.mp4."""
    d = Path(job_dir)
    with _render_lock:
        t0 = time.time()
        render.render(d / "remotion", d / "final.mp4", log or (d / "previs" / "render.log"))
    return {"video": str(d / "final.mp4"), "seconds": round(time.time() - t0, 1)}
