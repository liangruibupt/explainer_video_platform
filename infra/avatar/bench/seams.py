#!/usr/bin/env python3
"""Frames around each seam of an auto-segmented clip, for judging the jump.

  seams.py CLIP.mp4 OUT_sheet.jpg SEAM_S [SEAM_S ...]

One row per seam: frames at seam-1 s, seam-0.2 s, seam+0.15 (mid crossfade), seam+0.5 s,
seam+1 s, each labelled with its time. Also prints the mean absolute pixel difference between
consecutive frames around each seam next to the clip's typical frame-to-frame difference.
"""
import sys

import cv2
import numpy as np

clip, out, seams = sys.argv[1], sys.argv[2], [float(x) for x in sys.argv[3:]]
cap = cv2.VideoCapture(clip)
fps = cap.get(cv2.CAP_PROP_FPS) or 25
frames = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    frames.append(f)
n = len(frames)
diff = [float(np.mean(cv2.absdiff(frames[i], frames[i - 1]))) for i in range(1, n)]
typical = float(np.median(diff))
rows, h = [], 260
for s in seams:
    k = int(round(s * fps))
    win = diff[max(0, k - 13):k + 13]
    print(f"seam {s:.2f}s: max frame diff around seam {max(win):.2f} "
          f"(typical {typical:.2f}, p95 {float(np.percentile(diff, 95)):.2f})")
    tiles = []
    for dt in (-1.0, -0.2, 0.15, 0.5, 1.0):
        i = min(n - 1, max(0, int(round((s + dt) * fps))))
        t = cv2.resize(frames[i], (int(frames[i].shape[1] * h / frames[i].shape[0]), h))
        cv2.putText(t, f"{i / fps:.2f}s", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        tiles.append(t)
    rows.append(np.hstack(tiles))
cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
