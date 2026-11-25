#!/usr/bin/env python3
"""Write a synthetic words.json + durations.json for the four_stroke fixture (no Polly, no
Whisper): every non-punctuation character of a scene's narration gets an equal slice of a
fixed 0.21 s, after a 0.45 s lead, with a 0.85 s tail (narrate.py defaults). Same shapes as captions.py / narrate.py:
words.json = [{scene, text, start, end}] in FILM seconds; durations.json = frames per scene
(30 fps). Run: python3 make_words.py  (writes next to this file)."""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
PUNCT = re.compile(r'[\s，。、！？；：,.!?;:“”"\'（）()…—-]')
FPS, LEAD, STEP, TAIL = 30, 0.45, 0.21, 0.85  # lead/tail = narrate.py LEAD/TAIL ms

scenes = json.loads((HERE / "scenes.json").read_text())
words, durations, t0 = [], [], 0.0
for s in scenes:
    chars = [c for c in s["narration"] if not PUNCT.match(c)]
    for k, c in enumerate(chars):
        a = t0 + LEAD + k * STEP
        words.append({"scene": s["id"], "text": c, "start": round(a, 3), "end": round(a + STEP, 3)})
    frames = round((LEAD + len(chars) * STEP + TAIL) * FPS)
    durations.append(frames)
    t0 += frames / FPS
(HERE / "words.json").write_text(json.dumps(words, ensure_ascii=False))
(HERE / "durations.json").write_text(json.dumps(durations))
print(len(words), "chars,", durations, "frames")
