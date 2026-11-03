#!/usr/bin/env python3
"""Per-scene TTS; each scene's length comes from its real audio length.
Usage: narrate.py scenes.json OUTDIR [--tts polly|gtts] [--voice Zhiyu] [--fps 30]
scenes.json: [{"id": "...", "narration": "..."}, ...]
Writes OUTDIR/durations.json (frames per scene), OUTDIR/narration.wav, OUTDIR/audio/sNN.mp3.
"""
import argparse, html, json, os, re, shutil, subprocess

ap = argparse.ArgumentParser()
_lex = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pronounce.json')
ap.add_argument('--lexicon', default=_lex if os.path.exists(_lex) else '',
                help='pronunciation table (pinyin for polyphonic words, English respellings)')
ap.add_argument('scenes'); ap.add_argument('out')
ap.add_argument('--tts', default='polly'); ap.add_argument('--voice', default='Zhiyu')
ap.add_argument('--lang', default='cmn-CN'); ap.add_argument('--region', default='us-east-1')
ap.add_argument('--fps', type=int, default=30)
ap.add_argument('--lead', type=int, default=450); ap.add_argument('--tail', type=int, default=850)
a = ap.parse_args()
FF = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
FP = os.environ.get('FFPROBE') or shutil.which('ffprobe')
aud = os.path.join(a.out, 'audio'); os.makedirs(aud, exist_ok=True)
scenes = json.load(open(a.scenes))

LEX = json.load(open(a.lexicon)) if a.lexicon else {'pinyin': {}, 'en': {}}

def to_ssml(text):
    """Pinyin for listed polyphonic words, en-US for English, ALL-CAPS acronyms letter by letter."""
    t = html.escape(text, quote=False)
    for w, py in sorted(LEX['pinyin'].items(), key=lambda kv: -len(kv[0])):
        t = t.replace(w, ''.join(c if p is None else
                                 f'<phoneme alphabet="x-amazon-pinyin" ph="{p}">{c}</phoneme>'
                                 for c, p in zip(w, py)))
    def en(m):
        w = m.group(0)
        if w in LEX['en']: return f'<lang xml:lang="en-US">{LEX["en"][w]}</lang>'
        if re.fullmatch(r'[A-Z]{2,5}', w): return f'<say-as interpret-as="characters">{w}</say-as>'
        return f'<lang xml:lang="en-US">{w}</lang>'
    parts = re.split(r'(<[^>]+>)', t)
    return '<speak>' + ''.join(p if p.startswith('<') else re.sub(r'[A-Za-z][A-Za-z\-]*', en, p)
                               for p in parts) + '</speak>'

def synth(text, path):
    if a.tts == 'polly':
        import boto3
        r = boto3.client('polly', region_name=a.region).synthesize_speech(
            Engine='neural', VoiceId=a.voice, LanguageCode=a.lang, OutputFormat='mp3',
            Text=to_ssml(text), TextType='ssml')
        open(path, 'wb').write(r['AudioStream'].read())
    elif a.tts == 'gtts':
        from gtts import gTTS
        gTTS(text, lang='zh-CN' if a.lang.startswith(('cmn', 'zh')) else a.lang[:2]).save(path)
    else:
        raise SystemExit('unknown --tts ' + a.tts)

def ms(p):
    return int(float(subprocess.check_output([FP, '-v', 'error', '-show_entries',
               'format=duration', '-of', 'csv=p=0', p]).decode()) * 1000)

clips, frames, starts, t = [], [], [], 0
for i, s in enumerate(scenes):
    p = os.path.join(aud, f's{i:02d}.mp3'); synth(s['narration'], p)
    d = ms(p); body = a.lead + d + a.tail
    clips.append(p); starts.append(t + a.lead); t += body
    frames.append(round(body / 1000 * a.fps))
    print(f"{s.get('id', i)}: voice {d}ms -> {frames[-1]} frames")
json.dump(frames, open(os.path.join(a.out, 'durations.json'), 'w'))
inp = sum([['-i', c] for c in clips], [])
fc = ';'.join(f'[{i}]adelay={st}|{st}[a{i}]' for i, st in enumerate(starts))
fc += ';' + ''.join(f'[a{i}]' for i in range(len(clips)))
fc += f'[{len(clips)}]amix=inputs={len(clips) + 1}:normalize=0[o]'
subprocess.run([FF, '-y', '-v', 'error', *inp, '-f', 'lavfi', '-i',
                f'anullsrc=r=24000:cl=mono:d={t / 1000}', '-filter_complex', fc,
                '-map', '[o]', '-ar', '24000', '-ac', '1',
                os.path.join(a.out, 'narration.wav')], check=True)
print(f'total {t / 1000:.2f}s, {sum(frames)} frames')
