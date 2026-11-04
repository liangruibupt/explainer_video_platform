#!/usr/bin/env python3
"""Check smoke outputs: ffprobe stream info and a faster-whisper transcript as a vocals proxy.

  check_audio.py FILE...   (run with ~/.cache/explainer-video/venv/bin/python)
"""
import json, os, subprocess, sys

FFPROBE = os.environ.get("FFPROBE", os.path.expanduser("~/.cache/explainer-video/ffprobe"))

from faster_whisper import WhisperModel

model = WhisperModel("small", device="cpu", compute_type="int8")
for f in sys.argv[1:]:
    p = json.loads(subprocess.check_output([FFPROBE, "-v", "error", "-show_entries",
        "format=duration:stream=sample_rate,channels,codec_name", "-of", "json", f]))
    s = p["streams"][0]
    segs, info = model.transcribe(f, vad_filter=True)
    words = [(round(x.start, 1), round(x.end, 1), x.text.strip(), round(x.no_speech_prob, 2))
             for x in segs]
    print(json.dumps({"file": f, "duration": round(float(p["format"]["duration"]), 2),
                      "codec": s["codec_name"], "sample_rate": s["sample_rate"],
                      "channels": s["channels"], "lang": info.language,
                      "lang_p": round(info.language_probability, 2), "segments": words},
                     ensure_ascii=False), flush=True)
