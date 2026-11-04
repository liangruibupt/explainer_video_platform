#!/usr/bin/env python3
"""Synthesize the test 口播 clips with Amazon Polly neural Zhiyu.

  make_audio.py OUTDIR          writes OUTDIR/<name>.mp3 and .wav (16 kHz mono) for each
                                entry of scripts.json, and prints the measured seconds.
Needs ffmpeg on PATH (or FFMPEG=/path/to/ffmpeg) for the wav conversion.
"""
import json, os, subprocess, sys
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def main(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    polly = boto3.client("polly", region_name="us-east-1")
    scripts = json.loads((HERE / "scripts.json").read_text())
    for name, text in scripts.items():
        if name.startswith("_"):
            continue
        r = polly.synthesize_speech(Text=text, VoiceId="Zhiyu", Engine="neural",
                                    OutputFormat="mp3", SampleRate="24000")
        mp3 = out / f"{name}.mp3"
        mp3.write_bytes(r["AudioStream"].read())
        wav = out / f"{name}.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1",
                        "-ar", "16000", str(wav)], check=True)
        sec = os.path.getsize(wav) / 32000
        print(f"{name}: {sec:.1f} s ({len(text)} chars)")


if __name__ == "__main__":
    main(sys.argv[1])
