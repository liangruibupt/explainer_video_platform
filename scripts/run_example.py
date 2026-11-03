#!/usr/bin/env python3
"""Run one example through the local studio and copy its outputs back.

Usage: scripts/run_example.py examples/<name> [--base http://127.0.0.1:8765]
Reads examples/<name>/prompt.json; if examples/<name>/places.json exists it renders a
Northern Song map with skills/.../scripts/song_map.py and sends it as the background,
plus the places' pixel coordinates as scene context.
Copies back: scenes.json, durations.json, index.html (scene code), captions.ass,
final.mp4, poster.jpg, job.json, pipeline.log.
"""
import argparse, base64, json, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument('example')
ap.add_argument('--base', default='http://127.0.0.1:8765'); a = ap.parse_args()
ex = (ROOT / a.example).resolve()
body = json.loads((ex / 'prompt.json').read_text())


def call(method, path, payload=None):
    req = urllib.request.Request(a.base + path, method=method,
                                 data=json.dumps(payload).encode() if payload else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


if (ex / 'places.json').exists():
    size = '1920x1080' if body.get('ratio', '16:9') == '16:9' else '1080x1920'
    subprocess.run([sys.executable, ROOT / 'skills/explainer-video-pipeline/scripts/song_map.py',
                    ex / 'places.json', ex, '--size', size], check=True)
    places = json.loads((ex / 'places_px.json').read_text())
    body['background_png_b64'] = base64.b64encode((ex / 'song_map.png').read_bytes()).decode()
    body['scene_context'] = (
        "Map places in canvas pixels (use these exact x, y for markers, labels and routes; "
        "the map itself is the background image):\n" + json.dumps(
            [{k: p[k] for k in ('id', 'name', 'modern', 'years', 'x', 'y')} for p in places],
            ensure_ascii=False))
    lab = ex / 'map_labels.json'
    if lab.exists():
        body['scene_context'] += (
            "\n\nText already printed on the map (boxes in canvas pixels). Do not put markers, "
            "place labels or panels on top of these boxes; offset your labels away from them:\n"
            + lab.read_text())
    body['scene_context'] += (
        "\n\nPlace-label rule: put each place's name tag on the side of its marker that faces "
        "away from the map centre and from other places, so it covers neither the route nor "
        "other markers. Panels go at the canvas edge, never over a marker.")

job = call('POST', '/api/jobs', body)
print('job', job['id'])
last = None
while True:
    time.sleep(10)
    job = call('GET', f"/api/jobs/{job['id']}")
    cur = next((s['label'] for s in job['steps'] if s['status'] == 'running'), job['status'])
    if cur != last:
        print(time.strftime('%H:%M:%S'), cur, flush=True); last = cur
    if job['status'] in ('done', 'failed'):
        break

src = Path(job.get('_dir') or ROOT / 'data' / 'jobs' / job['id'])
out = ex / 'output'; out.mkdir(exist_ok=True)
for name in ('scenes.json', 'durations.json', 'index.html', 'captions.ass', 'final.mp4',
             'poster.jpg', 'job.json', 'pipeline.log'):
    if (src / name).exists():
        shutil.copy(src / name, out / name)
print(job['status'], job.get('duration'), job.get('error') or '')
sys.exit(0 if job['status'] == 'done' else 1)
