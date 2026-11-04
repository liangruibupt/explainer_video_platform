#!/usr/bin/env python3
"""Per-second identity / color trace for one clip (long-video stability check).

  trace.py CLIP.mp4 PORTRAIT.png OUT.json [OUT_sheet.jpg]

identity: ArcFace cosine to the portrait every second; color: CIE Lab mean of each second
and its delta E against the first second; sheet: 6 frames at 0/20/40/60/80/99 %.
"""
import json
import sys

import cv2
import numpy as np
from insightface.app import FaceAnalysis

clip, portrait, out = sys.argv[1:4]
sheet = sys.argv[4] if len(sys.argv) > 4 else None
app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                   allowed_modules=["detection", "recognition"])
app.prepare(ctx_id=-1, det_size=(640, 640))


def emb(img):
    f = app.get(img)
    return max(f, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1])).normed_embedding if f else None


ref = emb(cv2.imread(portrait))
cap = cv2.VideoCapture(clip)
fps = cap.get(cv2.CAP_PROP_FPS) or 25
frames = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    frames.append(f)
n, step = len(frames), int(round(fps))
lab = lambda fs: np.mean([cv2.cvtColor(x, cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(0) for x in fs], 0) * [100 / 255, 1, 1] - [0, 128, 128]
l0 = lab(frames[:step])
rows = []
for s in range(0, n, step):
    e = emb(frames[s])
    rows.append({"t": round(s / fps, 1), "id_cos": None if e is None else round(float(np.dot(e, ref)), 3),
                 "dE": round(float(np.linalg.norm(lab(frames[s:s + step]) - l0)), 2)})
ids = [r["id_cos"] for r in rows if r["id_cos"] is not None]
q = max(1, len(ids) // 4)
summary = {"frames": n, "seconds": round(n / fps, 2), "id_first_quarter": round(float(np.mean(ids[:q])), 3),
           "id_last_quarter": round(float(np.mean(ids[-q:])), 3), "id_min": min(ids),
           "id_min_t": next(r["t"] for r in rows if r["id_cos"] == min(ids)),
           "dE_end": rows[-1]["dE"], "dE_max": max(r["dE"] for r in rows), "trace": rows}
json.dump(summary, open(out, "w"), indent=1)
print({k: v for k, v in summary.items() if k != "trace"})
if sheet:
    idx = [min(n - 1, int(n * p)) for p in (0, .2, .4, .6, .8, .99)]
    h = 240
    tiles = [cv2.resize(frames[i], (int(frames[i].shape[1] * h / frames[i].shape[0]), h)) for i in idx]
    for t, i in zip(tiles, idx):
        cv2.putText(t, f"{i / fps:.0f}s", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    grid = np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:])])
    cv2.imwrite(sheet, grid, [cv2.IMWRITE_JPEG_QUALITY, 80])
