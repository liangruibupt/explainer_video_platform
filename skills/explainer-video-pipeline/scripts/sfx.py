#!/usr/bin/env python3
"""Timed sound effects on the narration timeline.
Usage: sfx.py scenes.json OUTDIR [--fps 30]
Each scene may carry "sfx": [{"at": FRAME_IN_SCENE, "kind": KIND}]; KIND is one of
whoosh, hiss, pop, rise, thud (synthesized, royalty-free) or a path to a sound file.
Reads OUTDIR/durations.json and OUTDIR/narration.wav, writes OUTDIR/sfx.wav (same length).
"""
import argparse, json, os, shutil, subprocess

ap = argparse.ArgumentParser(); ap.add_argument('scenes'); ap.add_argument('out')
ap.add_argument('--fps', type=int, default=30); a = ap.parse_args()
FF = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
FP = os.environ.get('FFPROBE') or shutil.which('ffprobe')
J = lambda n: os.path.join(a.out, n)
T = float(subprocess.check_output([FP, '-v', 'error', '-show_entries', 'format=duration',
                                   '-of', 'csv=p=0', J('narration.wav')]).decode())
SFX = {
 'whoosh': "anoisesrc=d=0.7:c=pink:a=0.6,bandpass=f=1200:w=900,afade=t=in:d=0.35,afade=t=out:st=0.35:d=0.35",
 'hiss': "anoisesrc=d=1.2:c=white:a=0.35,highpass=f=3000,afade=t=in:d=0.08,afade=t=out:st=0.6:d=0.6",
 'pop': "aevalsrc='0.8*sin(2*PI*(900-5600*t)*t)*exp(-t*30)':d=0.25:s=24000",
 'rise': "aevalsrc='0.4*sin(2*PI*(200+900*t)*t)':d=1.2:s=24000,afade=t=out:st=0.9:d=0.3",
 'thud': "aevalsrc='0.9*sin(2*PI*60*t)*exp(-t*9)':d=0.6:s=24000"}
scenes = json.load(open(a.scenes)); frames = json.load(open(J('durations.json')))
events, start = [], 0
for s, n in zip(scenes, frames):
    events += [((start + e['at']) / a.fps, e['kind']) for e in s.get('sfx', [])]
    start += n
if not events:
    raise SystemExit('no sfx in scenes.json')
ins, fc = [], []
for i, (t, k) in enumerate(events):
    ins += ['-f', 'lavfi', '-i', SFX[k]] if k in SFX else ['-i', k]
    ms = int(t * 1000)
    fc.append(f'[{i}]aresample=24000,aformat=channel_layouts=mono,adelay={ms}[e{i}]')
n = len(events)
fc.append(''.join(f'[e{i}]' for i in range(n)) + f'[{n}]amix=inputs={n + 1}:normalize=0[o]')
subprocess.run([FF, '-y', '-v', 'error', *ins, '-f', 'lavfi', '-i',
                f'anullsrc=r=24000:cl=mono:d={T}', '-filter_complex', ';'.join(fc),
                '-map', '[o]', '-t', str(T), J('sfx.wav')], check=True)
print('sfx:', ', '.join(f'{t:.2f}s {k}' for t, k in events))
