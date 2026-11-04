#!/usr/bin/env python3
"""Objective checks for the avatar clips (runs on the control host, CPU).

  analyze.py RUNSDIR [FFMPEG]     -> RUNSDIR/metrics.json, frames under RUNSDIR/frames/

Uses insightface buffalo_l (SCRFD detector, 106-point landmarks, ArcFace embeddings).
Per clip:
- lip sync: mouth opening (inner-lip gap / face height from the 106 landmarks, every
  2nd frame) vs speech RMS energy. Pearson correlation at the best lag in +-320 ms and
  that lag. A SyncNet confidence would need its weights; this is the proxy the brief allows.
  Higher corr = the mouth follows the speech; lag near 0 = in sync.
- identity: ArcFace cosine to the input portrait's face, first second and last second,
  and the minimum over 1 s samples (1.0 = same person; ~0.5+ is usually the same identity).
- color shift: delta E of the CIE Lab frame means, first second vs last second.
Start / middle / end frames are saved as JPEG for eyeballing and the contact sheet.
"""
import json, os, subprocess, sys
from pathlib import Path

import cv2
import numpy as np

FF = sys.argv[2] if len(sys.argv) > 2 else "ffmpeg"
FPS, STEP = 25, 2
# 106-point layout (insightface 2d106det), checked on the test portrait: inner upper lip
# 66/62/70, inner lower lip 54/60/57 (outer lips 63/71/67 and 56/53/59).
INNER_UP, INNER_LO = [66, 62, 70], [54, 60, 57]


def frames(mp4):
    cap = cv2.VideoCapture(str(mp4))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    return out


def audio_rms(mp4, n):
    raw = subprocess.run([FF, "-loglevel", "error", "-i", str(mp4), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = 16000 // FPS
    return np.array([np.sqrt(np.mean(a[i * hop:(i + 1) * hop] ** 2) + 1e-9) for i in range(n)])


def face(app, img):
    fs = app.get(img)
    return max(fs, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])) if fs else None


def mouth_open(f):
    lm = f.landmark_2d_106
    up, lo = lm[INNER_UP, 1].mean(), lm[INNER_LO, 1].mean()
    h = lm[:, 1].max() - lm[:, 1].min() + 1e-6
    return abs(lo - up) / h


def best_lag(mouth, rms, maxlag=8):
    best = (-2.0, 0)
    for lag in range(-maxlag, maxlag + 1):
        a = mouth[max(0, lag):len(mouth) + min(0, lag)]
        b = rms[max(0, -lag):len(rms) + min(0, -lag)]
        k = ~np.isnan(a)
        if k.sum() < 20:
            continue
        c = np.corrcoef(a[k], b[k])[0, 1]
        if c > best[0]:
            best = (float(c), lag)
    return best


def lab_mean(fs):
    return np.mean([cv2.cvtColor(f, cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(0) for f in fs], 0) * [100 / 255, 1, 1]


def main(d):
    d = Path(d)
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", root=os.environ.get("INSIGHT_ROOT", str(Path.home() / ".insightface")),
                       providers=["CPUExecutionProvider"], allowed_modules=["detection", "recognition", "landmark_2d_106"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    refs = {}
    for p in (d.parent / "img").glob("p*.png"):
        refs[p.stem] = face(app, cv2.imread(str(p))).normed_embedding
    (d / "frames").mkdir(exist_ok=True)
    out = json.loads((d / "metrics.json").read_text()) if (d / "metrics.json").exists() else {}
    for mp4 in sorted(d.glob("*.mp4")):
        if mp4.stem in out:
            continue
        fr = frames(mp4)
        n = len(fr)
        mouth = np.full(n, np.nan)
        emb = {}
        for i in range(0, n, STEP):
            f = face(app, fr[i])
            if f is None:
                continue
            mouth[i] = mouth_open(f)
            if i % FPS < STEP:  # one identity sample per second
                emb[i] = f.normed_embedding
        # interpolate the skipped frames so the lag search works at 25 fps
        k = ~np.isnan(mouth)
        mouth = np.interp(np.arange(n), np.flatnonzero(k), mouth[k]) if k.sum() > 2 else mouth
        rms = audio_rms(mp4, n)
        corr, lag = best_lag(mouth, rms)
        ref = refs["p916" if "916" in mp4.stem else "p169"]
        sims = [float(np.dot(e, ref)) for _, e in sorted(emb.items())]
        dE = float(np.linalg.norm(lab_mean(fr[:FPS]) - lab_mean(fr[-FPS:])))
        for tag, i in (("start", 0), ("mid", n // 2), ("end", n - 1)):
            cv2.imwrite(str(d / "frames" / f"{mp4.stem}_{tag}.jpg"), fr[i], [cv2.IMWRITE_JPEG_QUALITY, 85])
        out[mp4.stem] = {"frames": n, "video_s": round(n / FPS, 2), "face_found": round(len(emb) / max(1, len(range(0, n, FPS))), 3),
                         "lipsync_corr": round(corr, 3), "lipsync_lag_ms": lag * 1000 // FPS,
                         "id_cos_start": round(sims[0], 3) if sims else None,
                         "id_cos_end": round(sims[-1], 3) if sims else None,
                         "id_cos_min": round(min(sims), 3) if sims else None,
                         "color_dE_first_last": round(dE, 2), "size": list(fr[0].shape[1::-1])}
        print(mp4.stem, out[mp4.stem], flush=True)
        (d / "metrics.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
