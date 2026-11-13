#!/usr/bin/env python3
"""Word-by-word captions: Whisper timing + the exact script text.
Usage: captions.py scenes.json OUTDIR [--model small] [--size 1920x1080] [--font "Noto Sans CJK SC"]
Transcribes OUTDIR/audio/sNN.mp3 (from narrate.py) with faster-whisper word timestamps,
then maps the timings onto the script's own characters (Whisper often mishears homophones),
offset by each scene's start from durations.json. Writes OUTDIR/captions.ass (karaoke
highlight per character) and OUTDIR/words.json ([{scene, text, start, end}], seconds).
Burn in with: ffmpeg -i silent.mp4 -vf "ass=captions.ass" ...  (needs an ffmpeg with libass).

Importable: scene_words(model, audio, scene, base) times one scene (server/app/motion/revoice.py).
"""
import argparse, json, os, re

PUNCT = re.compile(r'[\s，。、！？；：,.!?;:“”"\'（）()…—-]')


def ts(t):
    return f'{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}'


def scene_words(model, audio, s, base):
    """words.json entries of one scene: Whisper's per-character times on `audio`, mapped onto
    the characters of s['narration'], plus `base` seconds (the scene's start + lead)."""
    segs, _ = model.transcribe(audio, language=s.get('lang', 'zh'), word_timestamps=True)
    heard = [(c, w.start, w.end) for seg in segs for w in seg.words
             for c in PUNCT.sub('', w.word)] or [('', 0.0, 0.5)]
    chars = [c for c in s['narration'] if not PUNCT.match(c)]
    out = []
    for k, c in enumerate(chars):  # proportional index map: script char k -> heard char j
        j = min(len(heard) - 1, k * len(heard) // max(len(chars), 1))
        out.append({'scene': s['id'], 'text': c,
                    'start': round(base + heard[j][1], 3),
                    'end': round(base + heard[j][2], 3)})
    # keep the script's punctuation on the preceding character (timing stays per char)
    k = -1
    for c in s['narration']:
        if PUNCT.match(c):
            if k >= 0 and not c.isspace():
                out[k]['text'] += c
        else:
            k += 1
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('scenes'); ap.add_argument('out')
    ap.add_argument('--model', default='small'); ap.add_argument('--size', default='1920x1080')
    ap.add_argument('--font', default='Noto Sans CJK SC'); ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--lead', type=int, default=450)
    ap.add_argument('--max-chars', dest='max_chars', type=int, default=16); a = ap.parse_args()
    from faster_whisper import WhisperModel
    J = lambda *p: os.path.join(a.out, *p)
    W, H = map(int, a.size.split('x'))
    scenes = json.load(open(a.scenes)); frames = json.load(open(J('durations.json')))
    model = WhisperModel(a.model, device='cpu', compute_type='int8')

    words, events, start = [], [], 0.0
    for i, (s, n) in enumerate(zip(scenes, frames)):
        lead = a.lead / 1000
        out = scene_words(model, J('audio', f's{i:02d}.mp3'), s, start + lead)
        words += out
        # short lines: break at punctuation or every --max-chars; show each only while spoken
        lines, cur = [], []
        for w in out:
            cur.append(w)
            if len(cur) >= a.max_chars or w['text'][-1:] in '，。！？；：,.!?;:':
                lines.append(cur); cur = []
        if cur:
            lines.append(cur)
        for ln in lines:
            txt = ''
            for w, nxt in zip(ln, ln[1:] + [None]):
                dur = (nxt['start'] if nxt else w['end']) - w['start']
                txt += '{\\k%d}%s' % (max(1, round(dur * 100)), w['text'].rstrip('，。、；：,.;:'))
            events.append(f'Dialogue: 0,{ts(ln[0]["start"])},{ts(ln[-1]["end"] + 0.25)},Cap,,0,0,0,,{txt}')
        start += n / a.fps

    json.dump(words, open(J('words.json'), 'w'), ensure_ascii=False)
    fs = round(H * 0.045)
    open(J('captions.ass'), 'w').write(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{a.font},{fs},&H0000D7FF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{max(2, fs // 16)},0,2,60,60,{round(H * 0.06)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + '\n'.join(events) + '\n')
    print(f'{len(words)} chars timed across {len(events)} lines -> captions.ass, words.json')


if __name__ == '__main__':
    main()
