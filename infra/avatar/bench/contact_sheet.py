#!/usr/bin/env python3
"""Side-by-side contact sheet: both models, same timestamps, same input.

  contact_sheet.py RUNSDIR PAIR_SUFFIX OUT.jpg [FFMPEG] [--times 0,0.5,1]

PAIR_SUFFIX e.g. 90s_480_fast -> rows infinitetalk_<suffix>, longcat_<suffix>; columns at
the given fractions of the clip (default start, 1/4, 1/2, 3/4, end). Uses ffmpeg only.
"""
import subprocess, sys, tempfile
from pathlib import Path

import cv2
import numpy as np


def grab(ff, mp4, t, h):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        subprocess.run([ff, "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1",
                        f.name], check=True)
        im = cv2.imread(f.name)
    return cv2.resize(im, (int(im.shape[1] * h / im.shape[0]), h))


def dur(ff, mp4):
    out = subprocess.run([ff, "-i", str(mp4)], capture_output=True, text=True).stderr
    hh, mm, ss = out.split("Duration: ")[1].split(",")[0].split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def main(d, suffix, out, ff="ffmpeg", fracs=(0, .25, .5, .75, .98), h=270):
    d = Path(d)
    rows = []
    for m in ("infinitetalk", "longcat"):
        mp4 = d / f"{m}_{suffix}.mp4"
        if not mp4.exists():
            continue
        T = dur(ff, mp4)
        tiles = []
        for fr in fracs:
            t = T * fr
            im = grab(ff, mp4, t, h)
            cv2.putText(im, f"{m} {t:.0f}s", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
            cv2.putText(im, f"{m} {t:.0f}s", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            tiles.append(im)
        rows.append(np.hstack(tiles))
    w = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, w - r.shape[1]), (0, 0))) for r in rows]
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], *(sys.argv[4:5] or []))
