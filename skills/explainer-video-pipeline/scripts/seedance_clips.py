#!/usr/bin/env python3
"""Generate one clip per scene with Seedance (BytePlus ModelArk video task API).
Usage: seedance_clips.py scenes.json OUTDIR [--model ID] [--ratio 16:9] [--resolution 1080p]
scenes.json items need "id" and "visual" (an English shot prompt); optional
"seconds" (4-12; default from OUTDIR/durations.json at 30 fps) and "first_frame"
(an https image URL). Needs env ARK_API_KEY. Writes OUTDIR/clips/<id>.mp4.
"""
import argparse, json, os, time, urllib.request

ap = argparse.ArgumentParser(); ap.add_argument('scenes'); ap.add_argument('out')
ap.add_argument('--model', default='seedance-1-5-pro-251215')
ap.add_argument('--ratio', default='16:9'); ap.add_argument('--resolution', default='1080p')
ap.add_argument('--base', default='https://ark.ap-southeast.bytepluses.com/api/v3')
a = ap.parse_args()
key = os.environ.get('ARK_API_KEY')
if not key:
    raise SystemExit('ARK_API_KEY is not set: create a key in the BytePlus ModelArk console')
H = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}

def call(method, path, body=None):
    req = urllib.request.Request(a.base + path, method=method, headers=H,
                                 data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

scenes = json.load(open(a.scenes))
dpath = os.path.join(a.out, 'durations.json')
frames = json.load(open(dpath)) if os.path.exists(dpath) else [None] * len(scenes)
os.makedirs(os.path.join(a.out, 'clips'), exist_ok=True)
tasks = {}
for s, fr in zip(scenes, frames):
    dst = os.path.join(a.out, 'clips', s['id'] + '.mp4')
    if os.path.exists(dst):
        continue
    sec = s.get('seconds') or (round(fr / 30) if fr else 5)
    content = [{'type': 'text', 'text': s['visual']}]
    if s.get('first_frame'):
        content.append({'type': 'image_url', 'image_url': {'url': s['first_frame']},
                        'role': 'first_frame'})
    body = {'model': a.model, 'content': content, 'ratio': a.ratio, 'resolution': a.resolution,
            'duration': max(4, min(12, sec)), 'generate_audio': False, 'watermark': False}
    tasks[s['id']] = call('POST', '/contents/generations/tasks', body)['id']
    print('submitted', s['id'], tasks[s['id']])
while tasks:
    time.sleep(15)
    for sid, tid in list(tasks.items()):
        r = call('GET', '/contents/generations/tasks/' + tid)
        st = r.get('status')
        if st == 'succeeded':
            urllib.request.urlretrieve(r['content']['video_url'],
                                       os.path.join(a.out, 'clips', sid + '.mp4'))
            print('done', sid); del tasks[sid]
        elif st in ('failed', 'expired', 'cancelled'):
            print('FAILED', sid, st, r.get('error')); del tasks[sid]
