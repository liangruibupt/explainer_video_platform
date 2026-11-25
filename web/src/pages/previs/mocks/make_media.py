"""Dev-only: render the mock media for the previs UI (stills, plan view, clips, thumbnail,
turntable) from the four_stroke fixture, by screenshotting capture.html on a running vite.

shortcut: the camera here is a rough stand-in for part B's compiler (keys interpolated
linearly, no easing, no catmull); replace with B's real shots once /api/jobs/{id}/previs lands.

Usage (from web/, vite running):  $PY src/pages/previs/mocks/make_media.py http://127.0.0.1:5199
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
EX = ROOT / "server/app/previs/examples/four_stroke"
FFMPEG = os.path.expanduser("~/.cache/explainer-video/ffmpeg")
FPS = 30

plan = json.loads((EX / "plan.json").read_text())
words = json.loads((EX / "words.json").read_text())
durations = json.loads((EX / "durations.json").read_text())
meta = json.loads((HERE / "four_stroke.meta.json").read_text())
objs = {o["id"]: o for o in plan["sets"][0]["objects"]}
marks = plan["sets"][0].get("marks", {})


def point(ref):
    if isinstance(ref, list):
        return ref
    if ref in marks:
        return point(marks[ref])
    obj, _, anchor = ref.partition(".")
    base = objs[obj].get("at", [0, 0, 0])
    a = meta["anchors"][anchor] if anchor else [0, 0, 0]
    return [base[i] + a[i] for i in range(3)]


def anchor_frame(an, si, frames):
    if "mark" in an:
        return 0 if an["mark"] == "start" else frames - 1
    if "t" in an:
        return round(an["t"] * FPS)
    if "frame" in an:
        return an["frame"]
    start = sum(durations[:si]) / FPS
    ws = [w for w in words if w["scene"] == plan["shots"][si]["id"]]
    text = "".join(w["text"] for w in ws)
    i = -1
    for _ in range(an.get("occurrence", 1)):
        i = text.index(an["word"], i + 1)
    w = ws[i] if an.get("edge", "start") == "start" else ws[i + len(an["word"]) - 1]
    t = (w["start"] if an.get("edge", "start") == "start" else w["end"]) - start + an.get("offset", 0)
    return max(0, min(frames - 1, round(t * FPS)))


def key_pos(k):
    if "pos" in k:
        return point(k["pos"])
    o = k["orbit"]
    c = point(o["around"])
    az, el = math.radians(o["az"]), math.radians(o["el"])
    return [c[0] + o["r"] * math.cos(el) * math.sin(az), c[1] + o["r"] * math.sin(el), c[2] + o["r"] * math.cos(el) * math.cos(az)]


def shot_tracks(si):
    shot = plan["shots"][si]
    frames = durations[si]
    keys = [(anchor_frame(k["at"], si, frames), k) for k in shot["camera"]["keys"]]
    vfov = lambda lens: math.degrees(2 * math.atan(12 / lens))  # noqa: E731

    def cam(f):
        prev = keys[0]
        for nxt in keys[1:]:
            if f < nxt[0]:
                if nxt[1].get("cut"):
                    break
                u = (f - prev[0]) / max(1, nxt[0] - prev[0])
                a, b = prev[1], nxt[1]
                lerp = lambda x, y: [x[i] + (y[i] - x[i]) * u for i in range(3)]  # noqa: E731
                if "orbit" in a and "orbit" in b:  # orbit keys: interpolate r/az/el, so the path is an arc
                    oa, ob = a["orbit"], b["orbit"]
                    pos = key_pos({"orbit": {"around": oa["around"], **{k: oa[k] + (ob[k] - oa[k]) * u for k in ("r", "az", "el")}}})
                else:
                    pos = lerp(key_pos(a), key_pos(b))
                return pos + lerp(point(a["look"]), point(b["look"])) + [
                    vfov(a.get("lens_mm", 35) + (b.get("lens_mm", 35) - a.get("lens_mm", 35)) * u)]
            prev = nxt
        k = prev[1]
        return key_pos(k) + point(k["look"]) + [vfov(k.get("lens_mm", 35))]

    def crank(f):
        v = objs["eng"].get("pose", {}).get("crank", 0)
        for a in shot.get("actions", []):
            if a.get("dof") != "crank":
                continue
            f0 = anchor_frame(a["at"], si, frames)
            if a["op"] == "spin":
                v = a["deg_per_s"] * max(0, f - f0) / FPS
            elif a["op"] == "pose":
                f1 = anchor_frame(a["until"], si, frames) if "until" in a else f0
                if f >= f1:
                    v = a["to"]
                elif f > f0:
                    v = v + (a["to"] - v) * (f - f0) / max(1, f1 - f0)
        return v

    return frames, keys, cam, crank


def plan_svg(si, frames, keys, cam):
    pts = [cam(f) for f in range(0, frames, 3)]
    xs = [p[0] for p in pts] + [-0.2, 0.2]
    zs = [p[2] for p in pts] + [-0.2, 0.2]
    x0, x1, z0, z1 = min(xs) - 0.1, max(xs) + 0.1, min(zs) - 0.1, max(zs) + 0.1
    s = 340 / max(x1 - x0, z1 - z0)
    X = lambda x: 10 + (x - x0) * s  # noqa: E731
    Z = lambda z: 10 + (z - z0) * s  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="360" height="360" viewBox="0 0 360 360" font-family="Noto Sans CJK SC,sans-serif">',
           '<rect width="360" height="360" fill="#f6f1ea"/>']
    g = 0.1  # 10 cm grid
    for i in range(math.floor(x0 / g), math.ceil(x1 / g) + 1):
        out.append(f'<line x1="{X(i*g):.1f}" y1="0" x2="{X(i*g):.1f}" y2="360" stroke="#e3d9cc" stroke-width=".5"/>')
    for i in range(math.floor(z0 / g), math.ceil(z1 / g) + 1):
        out.append(f'<line x1="0" y1="{Z(i*g):.1f}" x2="360" y2="{Z(i*g):.1f}" stroke="#e3d9cc" stroke-width=".5"/>')
    for p in meta["parts"]:
        (bx0, _, bz0), (bx1, _, bz1) = p["bbox"]["min"], p["bbox"]["max"]
        if bx1 > bx0:
            out.append(f'<rect x="{X(bx0):.1f}" y="{Z(bz0):.1f}" width="{(bx1-bx0)*s:.1f}" height="{(bz1-bz0)*s:.1f}" fill="#c9c4bb" fill-opacity=".35" stroke="#8a7f74" stroke-width=".6"/>')
    for i in range(len(pts) - 1):
        hue = 210 - 190 * i / max(1, len(pts) - 2)
        out.append(f'<line x1="{X(pts[i][0]):.1f}" y1="{Z(pts[i][2]):.1f}" x2="{X(pts[i+1][0]):.1f}" y2="{Z(pts[i+1][2]):.1f}" stroke="hsl({hue:.0f},65%,48%)" stroke-width="2.5" stroke-linecap="round"/>')
    for n, (f, k) in enumerate(keys):
        c = cam(min(f, frames - 1))
        out.append(f'<circle cx="{X(c[0]):.1f}" cy="{Z(c[2]):.1f}" r="8" fill="#2b2420"/><text x="{X(c[0]):.1f}" y="{Z(c[2])+4:.1f}" font-size="10" fill="#fff" text-anchor="middle">{n+1}</text>')
        lk = point(k["look"])
        out.append(f'<circle cx="{X(lk[0]):.1f}" cy="{Z(lk[2]):.1f}" r="3" fill="#c8452c"/>')
    out.append(f'<text x="12" y="30" font-size="22" fill="#6d625a">{plan["shots"][si]["id"]} 俯视 · 格子 10 cm</text></svg>')
    (HERE / "shots" / f"{plan['shots'][si]['id']}.plan.svg").write_text("\n".join(out))


def main(base):
    (HERE / "shots").mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = b.new_page(viewport={"width": 640, "height": 360})

        def shoot(path, cam, pose, w=640, h=360, quality=72):
            page.set_viewport_size({"width": w, "height": h})
            q = f"w={w}&h={h}&cam={','.join(f'{x:.4f}' for x in cam)}&pose={json.dumps(pose)}"
            page.goto(f"{base}/src/pages/previs/mocks/capture.html?{q}")
            page.wait_for_function("window.ready === true", timeout=60000)
            page.screenshot(path=str(path), type="jpeg", quality=quality)

        for si, shot in enumerate(plan["shots"]):
            frames, keys, cam, crank = shot_tracks(si)
            plan_svg(si, frames, keys, cam)
            for n in range(5):
                f = round(n * (frames - 1) / 4)
                shoot(HERE / "shots" / f"{shot['id']}_{n}.jpg", cam(f), {"crank": crank(f)}, 480, 270, 70)
            with tempfile.TemporaryDirectory() as td:
                n = 24
                for i in range(n):
                    f = round(i * (frames - 1) / (n - 1))
                    shoot(Path(td) / f"{i:03d}.jpg", cam(f), {"crank": crank(f)}, 480, 270, 80)
                subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-framerate", f"{n / (frames / FPS):.4f}", "-i", f"{td}/%03d.jpg",
                                "-vf", "fps=15", "-c:v", "libx264", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                str(HERE / "shots" / f"{shot['id']}.mp4")], check=True)
        # card thumbnail + 4 s turntable
        with tempfile.TemporaryDirectory() as td:
            for i in range(32):
                az = math.radians(35 + 360 * i / 32)
                c = [1.05 * math.cos(math.radians(18)) * math.sin(az), 0.26 + 1.05 * math.sin(math.radians(18)), 1.05 * math.cos(math.radians(18)) * math.cos(az), 0, 0.26, 0, 30]
                shoot(Path(td) / f"{i:03d}.jpg", c, {"crank": 0}, 480, 300, 80)
                if i == 0:
                    shoot(HERE / "four_stroke.thumb.jpg", c, {"crank": 0}, 480, 300, 72)
            subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-framerate", "8", "-i", f"{td}/%03d.jpg", "-vf", "fps=15",
                            "-c:v", "libx264", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                            str(HERE / "four_stroke.turntable.mp4")], check=True)
        b.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5199")
