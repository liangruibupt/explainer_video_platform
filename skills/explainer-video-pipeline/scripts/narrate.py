#!/usr/bin/env python3
"""Per-scene TTS; each scene's length comes from its real audio length.
Usage: narrate.py scenes.json OUTDIR [--tts polly|gtts] [--voice Zhiyu] [--fps 30]
scenes.json: [{"id": "...", "narration": "..."}, ...]
Writes OUTDIR/durations.json (frames per scene), OUTDIR/narration.wav, OUTDIR/audio/sNN.mp3.

Importable (server/app/motion/revoice.py re-voices single scenes with the same voice):
load_lexicon, to_ssml, synth, ms, scene_frames, concat. The CLI output is unchanged.
"""
import argparse, html, json, os, re, shutil, subprocess

_lex = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pronounce.json')
DEFAULT_LEXICON = _lex if os.path.exists(_lex) else ''
LEAD, TAIL = 450, 850


def load_lexicon(path=DEFAULT_LEXICON):
    return json.load(open(path)) if path else {'pinyin': {}, 'en': {}}


def to_ssml(text, lex):
    """Pinyin for listed polyphonic words, en-US for English, ALL-CAPS acronyms letter by letter."""
    t = html.escape(text, quote=False)
    for w, py in sorted(lex['pinyin'].items(), key=lambda kv: -len(kv[0])):
        t = t.replace(w, ''.join(c if p is None else
                                 f'<phoneme alphabet="x-amazon-pinyin" ph="{p}">{c}</phoneme>'
                                 for c, p in zip(w, py)))
    def en(m):
        w = m.group(0)
        if w in lex['en']: return f'<lang xml:lang="en-US">{lex["en"][w]}</lang>'
        if re.fullmatch(r'[A-Z]{2,5}', w): return f'<say-as interpret-as="characters">{w}</say-as>'
        return f'<lang xml:lang="en-US">{w}</lang>'
    parts = re.split(r'(<[^>]+>)', t)
    return '<speak>' + ''.join(p if p.startswith('<') else re.sub(r'[A-Za-z][A-Za-z\-]*', en, p)
                               for p in parts) + '</speak>'


def synth(text, path, *, lex, tts='polly', voice='Zhiyu', lang='cmn-CN', region='us-east-1'):
    if tts == 'polly':
        import boto3
        r = boto3.client('polly', region_name=region).synthesize_speech(
            Engine='neural', VoiceId=voice, LanguageCode=lang, OutputFormat='mp3',
            Text=to_ssml(text, lex), TextType='ssml')
        open(path, 'wb').write(r['AudioStream'].read())
    elif tts == 'gtts':
        from gtts import gTTS
        gTTS(text, lang='zh-CN' if lang.startswith(('cmn', 'zh')) else lang[:2]).save(path)
    else:
        raise SystemExit('unknown --tts ' + tts)


def ms(p, fp=None):
    fp = fp or os.environ.get('FFPROBE') or shutil.which('ffprobe')
    return int(float(subprocess.check_output([fp, '-v', 'error', '-show_entries',
               'format=duration', '-of', 'csv=p=0', p]).decode()) * 1000)


def scene_frames(voice_ms, fps=30, lead=LEAD, tail=TAIL):
    """(body ms, frames) of one scene: lead + voice + tail, rounded to frames."""
    body = lead + voice_ms + tail
    return body, round(body / 1000 * fps)


def concat(clips, starts, total_ms, out, ff=None):
    """narration.wav: each clip delayed to its start (ms) over silence of total_ms."""
    ff = ff or os.environ.get('FFMPEG') or shutil.which('ffmpeg')
    inp = sum([['-i', c] for c in clips], [])
    fc = ';'.join(f'[{i}]adelay={st}|{st}[a{i}]' for i, st in enumerate(starts))
    fc += ';' + ''.join(f'[a{i}]' for i in range(len(clips)))
    fc += f'[{len(clips)}]amix=inputs={len(clips) + 1}:normalize=0[o]'
    subprocess.run([ff, '-y', '-v', 'error', *inp, '-f', 'lavfi', '-i',
                    f'anullsrc=r=24000:cl=mono:d={total_ms / 1000}', '-filter_complex', fc,
                    '-map', '[o]', '-ar', '24000', '-ac', '1', out], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lexicon', default=DEFAULT_LEXICON,
                    help='pronunciation table (pinyin for polyphonic words, English respellings)')
    ap.add_argument('scenes'); ap.add_argument('out')
    ap.add_argument('--tts', default='polly'); ap.add_argument('--voice', default='Zhiyu')
    ap.add_argument('--lang', default='cmn-CN'); ap.add_argument('--region', default='us-east-1')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--lead', type=int, default=LEAD); ap.add_argument('--tail', type=int, default=TAIL)
    a = ap.parse_args()
    aud = os.path.join(a.out, 'audio'); os.makedirs(aud, exist_ok=True)
    scenes = json.load(open(a.scenes))
    lex = load_lexicon(a.lexicon)
    clips, frames, starts, t = [], [], [], 0
    for i, s in enumerate(scenes):
        p = os.path.join(aud, f's{i:02d}.mp3')
        synth(s['narration'], p, lex=lex, tts=a.tts, voice=a.voice, lang=a.lang, region=a.region)
        d = ms(p); body, n = scene_frames(d, a.fps, a.lead, a.tail)
        clips.append(p); starts.append(t + a.lead); t += body
        frames.append(n)
        print(f"{s.get('id', i)}: voice {d}ms -> {frames[-1]} frames")
    json.dump(frames, open(os.path.join(a.out, 'durations.json'), 'w'))
    concat(clips, starts, t, os.path.join(a.out, 'narration.wav'))
    print(f'total {t / 1000:.2f}s, {sum(frames)} frames')


if __name__ == '__main__':
    main()
