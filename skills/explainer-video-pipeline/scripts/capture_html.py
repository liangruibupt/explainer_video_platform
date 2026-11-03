#!/usr/bin/env python3
"""Capture an HTML/Canvas/SVG/GSAP/p5.js animation frame by frame in headless Chromium.
Usage: capture_html.py page.html out.mp4 --frames N [--fps 30] [--size 1920x1080]
The page must define  window.seek = async (frame) => {...}  which draws exactly that
frame from the frame number alone (no requestAnimationFrame, no wall clock, no
Math.random without a seed). Frames are piped straight into ffmpeg (H.264, yuv420p).
Needs: pip install playwright && playwright install chromium; FFMPEG env or ffmpeg on PATH.
"""
import argparse, os, pathlib, shutil, subprocess
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser(); ap.add_argument('page'); ap.add_argument('out')
ap.add_argument('--frames', type=int, required=True); ap.add_argument('--fps', type=int, default=30)
ap.add_argument('--size', default='1920x1080'); a = ap.parse_args()
w, h = map(int, a.size.split('x'))
FF = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
enc = subprocess.Popen([FF, '-y', '-v', 'error', '-f', 'image2pipe', '-framerate', str(a.fps),
                        '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', a.out],
                       stdin=subprocess.PIPE)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': w, 'height': h})
    pg.goto(pathlib.Path(a.page).resolve().as_uri())
    pg.wait_for_function('typeof window.seek === "function"')
    pg.evaluate('document.fonts ? document.fonts.ready : null')
    for f in range(a.frames):
        pg.evaluate('f => window.seek(f)', f)
        enc.stdin.write(pg.screenshot(type='png'))
        if f % 150 == 0:
            print(f'frame {f}/{a.frames}')
    b.close()
enc.stdin.close(); enc.wait()
print('wrote', a.out, a.frames, 'frames')
