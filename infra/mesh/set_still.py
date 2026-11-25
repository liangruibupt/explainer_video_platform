#!/usr/bin/env python3
"""One-shot previs still with an image-to-3D library model placed in a set (docs/MESH.md 5).

  set_still.py ASSET_DIR OUT.png [--engine]

ASSET_DIR is a library model dir (data/assets/<id>/ with file.glb + meta.json). Builds a tiny
one-shot job: set "bench" with the model at the origin (and, with --engine, the four_stroke
example engine 0.6 m to its left for scale), a 3 s orbit shot, compiles it with part B's
compiler (shots.compile_all, checks included) and renders frame 45 of Shot-s01 with swangle.
Prints the check report summary. Run with EVP_DATA pointing at the studio's data dir.
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from app.previs import shots  # noqa: E402
from app.remotion import render  # noqa: E402

EX = ROOT / "server" / "app" / "previs" / "examples" / "four_stroke"


def main(asset_dir: Path, out: Path, engine: bool):
    meta = json.loads((asset_dir / "meta.json").read_text())
    h = meta["size"][1]
    d = Path(tempfile.mkdtemp(prefix="mesh-set-")) / "20261125-120000-ca7ca7"
    (d / "previs").mkdir(parents=True)
    objs = [{"id": "cat", "asset": "@cat", "at": [0, 0, 0]}]
    assets = {"@cat": str(asset_dir)}
    plan = {"version": 1, "assets": [], "sets": [{
        "id": "bench", "name": "工作台", "units": "m", "background": "#e9e6e1",
        "ground": {"kind": "grid", "size": 3, "color": "#dedad3"}, "lights": "studio",
        "objects": objs, "marks": {"cat": "cat"}}], "shots": [{
        "id": "s01", "set": "bench", "camera": {"path": "catmull", "keys": [
            {"at": {"mark": "start"}, "orbit": {"around": [0, h / 2, 0], "r": h * 3.2, "az": 20, "el": 12},
             "look": [0, h * 0.45, 0], "lens_mm": 40},
            {"at": {"mark": "end"}, "orbit": {"around": [0, h / 2, 0], "r": h * 3.0, "az": 40, "el": 14},
             "look": [0, h * 0.45, 0], "lens_mm": 40, "ease": "inOutCubic"}]},
        "actions": [{"op": "dimension", "from": [h * 0.75, 0, 0], "to": [h * 0.75, h, 0],
                     "text": f"{h:.2f} m", "at": {"mark": "start"}}],
        "style": {"look": "clay", "shadows": True}}]}
    if engine:
        spec = json.loads((EX / "plan.json").read_text())["assets"][0]
        plan["assets"].append(spec)
        objs.append({"id": "eng", "asset": "@four_stroke", "at": [-0.6, 0, 0], "pose": {"crank": 0}})
        ed = d.parent / "four_stroke"
        ed.mkdir()
        (ed / "spec.json").write_text(json.dumps(spec["spec"]))
        r = subprocess.run(["node", str(shots.bundle()), "build-asset", str(ed / "spec.json"),
                            str(ed / "file.glb"), str(ed / "meta.json")], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + r.stdout
        assets["@four_stroke"] = str(ed)
        for k in plan["shots"][0]["camera"]["keys"]:
            k["orbit"]["around"] = [-0.3, 0.3, 0]
            k["orbit"]["r"] = 1.9
            k["look"] = [-0.3, 0.25, 0]
    (d / "previs" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    (d / "previs" / "assets.json").write_text(json.dumps(assets))
    scenes = [{"id": "s01", "narration": "这是一只坐着的猫。"}]
    (d / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False))
    (d / "durations.json").write_text(json.dumps([90]))
    (d / "words.json").write_text("[]")
    (d / "job.json").write_text(json.dumps({"id": d.name, "ratio": "16:9", "scenes": scenes},
                                           ensure_ascii=False))
    res = shots.compile_all(d, size="1280x720")
    print(json.dumps({k: {"errors": v["errors"], "report": v["report"]} for k, v in res.items()},
                     ensure_ascii=False))
    proj = shots.write_project(d)
    tl = json.loads((proj / "src" / "data" / "timeline.json").read_text())
    tl["width"], tl["height"] = 1280, 720
    (proj / "src" / "data" / "timeline.json").write_text(json.dumps(tl))
    errs = render.stills(proj, [45], [out], None, comp="Shot-s01", gl="swangle")
    print("stills errors:", errs, "->", out)
    shutil.rmtree(d.parent, ignore_errors=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), "--engine" in sys.argv)
