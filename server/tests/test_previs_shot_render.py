"""Part B of 3D previs: a real WebGL render (swangle) of fixture stills at the word anchors.
Projects known part positions with the ResolvedShot camera and checks the pixel colours there:
the intake valve (blue) is open during 进气, the spark plug is lit (yellow) at 点火, the section
cut in s02 shows the green piston. About 15 s on the 4-core box."""
import json
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.previs import shots
from app.remotion import render

from test_previs_shot import asset_dir, make_job  # noqa: F401 - fixture reuse

pytestmark = pytest.mark.skipif(shutil.which("node") is None or not (render.BASE / "node_modules" / "three").exists(),
                                reason="needs node + the shared Remotion base with three.js")


def to_px(row, p, w, h):
    c, look = row[:3], row[3:6]
    f = [look[i] - c[i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in f))
    f = [x / n for x in f]
    r = [f[1] * 0 - f[2] * 1, f[2] * 0 - f[0] * 0, f[0] * 1 - f[1] * 0]  # f x up(0,1,0)
    n = math.sqrt(sum(x * x for x in r))
    r = [x / n for x in r]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    d = [p[i] - c[i] for i in range(3)]
    x, y, z = (sum(d[i] * v[i] for i in range(3)) for v in (r, u, f))
    t = math.tan(math.radians(row[6]) / 2)
    return int(((x / (z * t * w / h)) + 1) / 2 * w), int((1 - y / (z * t)) / 2 * h)


def patch(im, xy, k=3):
    px = [im.getpixel((xy[0] + dx, xy[1] + dy)) for dx in range(-k, k + 1) for dy in range(-k, k + 1)]
    return [sum(p[i] for p in px) / len(px) for i in range(3)]


def test_render_stills_at_anchors(asset_dir, tmp_path):  # noqa: F811
    d = make_job(asset_dir, tmp_path)
    (d / "job.json").write_text(json.dumps({"ratio": "16:9", "scenes": json.loads((d / "scenes.json").read_text())}))
    res = shots.compile_all(d, size="960x540")
    assert all(r["errors"] == 0 for r in res.values())
    proj = shots.write_project(d)
    tl = json.loads((proj / "src" / "data" / "timeline.json").read_text())
    tl["width"], tl["height"] = 960, 540  # small stills for the test
    (proj / "src" / "data" / "timeline.json").write_text(json.dumps(tl))
    s2 = json.loads((d / "previs" / "s02.json").read_text())
    s3 = json.loads((d / "previs" / "s03.json").read_text())
    f_in = next(a["frame"] for a in s2["anchors"] if a["where"] == "actions[1].at")  # 进气
    f_cmp = next(a["frame"] for a in s2["anchors"] if a["where"] == "actions[1].until")  # 压缩
    f_mid = (f_in + f_cmp) // 2
    f_fire = next(a["frame"] for a in s3["anchors"] if a["where"] == "actions[1].at")  # 点火
    out2 = tmp_path / "s02.png"
    out3 = tmp_path / "s03.png"
    assert render.stills(proj, [f_mid], [out2], None, comp="Shot-s02", gl="swangle") == {}
    assert render.stills(proj, [f_fire], [out3], None, comp="Shot-s03", gl="swangle") == {}
    w, h = 960, 540
    im2 = Image.open(out2).convert("RGB")
    im3 = Image.open(out3).convert("RGB")
    o2 = s2["objects"][0]["parts"]
    cam = s2["camera"][f_mid]
    # intake valve head: valve stem part y (slid down = open) - 0.03
    vy = o2["intake_valve"][f_mid][1]
    assert vy < 0.47 - 0.005, "intake valve open at mid-intake"
    r, g, b = patch(im2, to_px(cam, [-0.018, vy - 0.03, -0.004], w, h))
    assert b > r + 25 and b > g, f"intake valve head should be blue, got {(r, g, b)}"
    # piston cut face (section keeps z <= 0): green at the piston centre
    py = o2["piston"][f_mid][1]
    r, g, b = patch(im2, to_px(cam, [0.025, py, 0.0], w, h))  # beside the rod
    assert g > r + 15 and g > b + 15, f"piston section should be green, got {(r, g, b)}"
    # spark plug lit at 点火 (highlight #ffd24a on accent4)
    cam3 = s3["camera"][f_fire]
    r, g, b = patch(im3, to_px(cam3, [0.0, 0.51, 0.0], w, h), k=2)
    assert r > 200 and g > 170 and b / r < 0.5, f"spark plug should be yellow, got {(r, g, b)}"
    # the highlight glow: a yellow shell just outside the plug (r 6 mm, shell +4 mm); background
    # there is #e9e6e1 when not highlighted
    r, g, b = patch(im3, to_px(cam3, [0.0085, 0.51, 0.0], w, h), k=1)
    assert b < g - 40, f"spark plug glow missing at 点火, got {(r, g, b)}"
