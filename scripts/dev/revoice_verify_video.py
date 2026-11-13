#!/usr/bin/env python3
"""Check a rendered version against the take it was saved with (run from server/).
Usage: EVP_DATA=... python ../scripts/dev/revoice_verify_video.py JOB_ID VERSION TAKE OUT_DIR
Audio cross-correlation vs the take's voice_bgm.wav, Whisper on S4/S6/S7, a frame mid-S7.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from app import pipeline  # noqa: E402
from app.motion import revoice  # noqa: E402

jid, v, n, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])
out.mkdir(parents=True, exist_ok=True)
d = pipeline.job_dir(jid)
mp4 = d / "versions" / f"v{v}.mp4"
take = revoice.load_take(d, n)
FF = pipeline.FFMPEG


def pcm(src, ss=None, t=None):
    a = [FF, "-v", "error"] + (["-ss", str(ss)] if ss is not None else []) + ["-i", str(src)]
    a += (["-t", str(t)] if t else []) + ["-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(a, capture_output=True, check=True).stdout, "<i2").astype(float)


dur = float(subprocess.check_output([pipeline.FFPROBE, "-v", "error", "-show_entries",
                                     "format=duration", "-of", "csv=p=0", str(mp4)]).decode())
print(f"v{v}.mp4 duration {dur:.3f}s, take total {take['timeline']['scenes'][-1]['from'] + take['timeline']['scenes'][-1]['duration']} frames = {sum(take['durations']) / 30:.3f}s")
a, b = pcm(mp4), pcm(take["dir"] / "voice_bgm.wav")
m = min(len(a), len(b))
a, b = a[:m] - a[:m].mean(), b[:m] - b[:m].mean()
L = 1 << int(np.ceil(np.log2(2 * m)))
xc = np.fft.irfft(np.fft.rfft(a, L) * np.conj(np.fft.rfft(b, L)), L)
lag = int(np.argmax(xc))
lag = lag - L if lag > L // 2 else lag
peak = xc.max() / (np.linalg.norm(a) * np.linalg.norm(b))
print(f"cross-correlation vs take voice_bgm.wav: offset {lag / 8000 * 1000:+.1f} ms, normalized peak {peak:.3f}")
t0 = revoice.load_take(d, 0)
b0 = pcm(t0["dir"] / "voice_bgm.wav")
k = min(len(a), len(b0))
b0 = b0[:k] - b0[:k].mean()
print(f"  same measure vs the ORIGINAL voice_bgm.wav: peak {np.dot(a[:k], b0) / (np.linalg.norm(a[:k]) * np.linalg.norm(b0)):.3f}")
db = lambda x: 20 * np.log10(np.sqrt(np.mean(x ** 2)) / 32768 + 1e-12)
print(f"  RMS v{v} {db(a):.2f} dB, take mix {db(b):.2f} dB")

model = revoice.whisper()
tl = {s["id"]: s for s in take["timeline"]["scenes"]}
for sid in ("token_engine", "migration_engine", "recap"):
    s = tl[sid]
    wav = out / f"{sid}.wav"
    subprocess.run([FF, "-y", "-v", "error", "-ss", f"{s['from'] / 30:.3f}", "-t",
                    f"{s['duration'] / 30:.3f}", "-i", str(mp4), "-ac", "1", "-ar", "16000", str(wav)],
                   check=True)
    segs, _ = model.transcribe(str(wav), language="zh")
    print(f"whisper v{v} {sid}: {''.join(x.text for x in segs).strip()}")
s = tl["recap"]
cap = next(c for c in take["captions"] if c["scene"] == "recap" and "三十" in c["text"])
f = cap["from"] + cap["duration"] // 2
png = out / f"v{v}_recap_f{f}.png"
subprocess.run([FF, "-y", "-v", "error", "-ss", f"{f / 30:.3f}", "-i", str(mp4), "-frames:v", "1",
                str(png)], check=True)
print(f"frame {f} (S7 {s['from']}..{s['from'] + s['duration']}, caption '{cap['text']}' {cap['from']}+{cap['duration']}) -> {png}")
