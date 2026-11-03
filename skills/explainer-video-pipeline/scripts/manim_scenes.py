#!/usr/bin/env python3
"""Render Manim scenes at their narration lengths and join them into one silent video.
Usage: manim_scenes.py scenes.py OUTDIR [--manim PATH] [--quality h] [--fps 30]
scenes.py defines one Scene class per entry in OUTDIR/scenes.json, named by the scene's
"manim" field (or its id in CamelCase). Each class reads its target length from
OUTDIR/durations.json via the env var SCENE_SECONDS and must pad with self.wait() to fill it:
    secs = float(os.environ["SCENE_SECONDS"]) ... self.wait(max(0, secs - self.renderer.time))
Writes OUTDIR/clips/<id>.mp4 and OUTDIR/silent.mp4 (each clip trimmed/padded to its exact
frame count so the picture stays locked to the voice).
"""
import argparse, json, os, shutil, subprocess

ap = argparse.ArgumentParser(); ap.add_argument('code'); ap.add_argument('out')
ap.add_argument('--manim', default=shutil.which('manim') or 'manim')
ap.add_argument('--quality', default='h'); ap.add_argument('--fps', type=int, default=30)
a = ap.parse_args()
FF = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
J = lambda *p: os.path.join(a.out, *p)
scenes = json.load(open(J('scenes.json'))); frames = json.load(open(J('durations.json')))
os.makedirs(J('clips'), exist_ok=True); media = J('manim_media')
camel = lambda s: ''.join(p.capitalize() for p in s.replace('-', '_').split('_'))
parts = []
for s, n in zip(scenes, frames):
    cls = s.get('manim') or camel(s['id'])
    env = dict(os.environ, SCENE_SECONDS=str(n / a.fps))
    subprocess.run([a.manim, 'render', f'-q{a.quality}', '--fps', str(a.fps), '--media_dir', media,
                    '-o', s['id'], a.code, cls], check=True, env=env)
    src = next(os.path.join(r, f) for r, _, fs in os.walk(media) for f in fs
               if f == s['id'] + '.mp4' and 'partial' not in r)
    dst = J('clips', s['id'] + '.mp4')
    # lock length to exactly n frames: pad with the last frame if short, cut if long
    subprocess.run([FF, '-y', '-v', 'error', '-i', src, '-vf',
                    f'fps={a.fps},tpad=stop_mode=clone:stop=-1', '-frames:v', str(n),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', dst], check=True)
    parts.append(dst); print(f"{s['id']}: {cls} -> {n} frames")
lst = J('clips', 'list.txt')
open(lst, 'w').write(''.join(f"file '{os.path.abspath(p)}'\n" for p in parts))
subprocess.run([FF, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                J('silent.mp4')], check=True)
print('wrote', J('silent.mp4'), sum(frames), 'frames')
