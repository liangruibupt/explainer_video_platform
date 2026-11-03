#!/usr/bin/env python3
"""Background music + ducked mix (+ sfx.wav if present) + level check.
Usage: mix_bgm.py OUTDIR [--music synth|none|PATH]
Reads OUTDIR/narration.wav (and OUTDIR/sfx.wav from sfx.py, if it exists).
Writes OUTDIR/bgm.wav and OUTDIR/narration_bgm.wav, then prints RMS dB overall and
for the first 2.5 s (music-only lead-in). "synth" needs an ffmpeg with aevalsrc,
tremolo and sidechaincompress. PATH loops/trims a music file you supply.
"""
import argparse, math, os, shutil, struct, subprocess, wave

ap = argparse.ArgumentParser(); ap.add_argument('out')
ap.add_argument('--music', default='synth')
ap.add_argument('--music-volume', type=float, default=0.72); a = ap.parse_args()
FF = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
FP = os.environ.get('FFPROBE') or shutil.which('ffprobe')
J = lambda n: os.path.join(a.out, n)
voice, bgm, fx, mix = J('narration.wav'), J('bgm.wav'), J('sfx.wav'), J('narration_bgm.wav')
T = float(subprocess.check_output([FP, '-v', 'error', '-show_entries', 'format=duration',
                                   '-of', 'csv=p=0', voice]).decode())
run = lambda *x: subprocess.run([FF, '-y', '-v', 'error', *x], check=True)

if a.music == 'synth':
    L = [(110, .22, 2, .6, 400), (220, .16, 4, .8, 1200), (329.63, .12, 4, .9, 1600),
         (440, .10, 8, .95, 2200), (1318.5, .075, 12, 1, 5000), (1760, .07, 16, 1, 7000)]
    fc = ';'.join(f"aevalsrc='{v}*sin(2*PI*{f}*t)':s=24000:d={T}:c=mono,"
                  f"tremolo=f={th}:d={td},lowpass=f={lp}[l{i}]"
                  for i, (f, v, th, td, lp) in enumerate(L))
    fc += ';' + ''.join(f'[l{i}]' for i in range(len(L)))
    fc += (f'amix=inputs={len(L)}:normalize=0,highpass=f=60,'
           f'acompressor=threshold=-18dB:ratio=3,afade=t=in:d=0.3,'
           f'afade=t=out:st={max(T - 1.5, 0)}:d=1.5[b]')
    run('-filter_complex', fc, '-map', '[b]', '-ar', '24000', '-ac', '1', bgm)
elif a.music == 'none':
    run('-f', 'lavfi', '-i', f'anullsrc=r=24000:cl=mono:d={T}', bgm)
else:
    # Loop the track with a 4 s crossfade at each seam, level it, then fade in/out.
    L = float(subprocess.check_output([FP, '-v', 'error', '-show_entries', 'format=duration',
                                       '-of', 'csv=p=0', a.music]).decode())
    n = max(1, math.ceil((T + 4) / max(L - 4, 1)))
    inp = sum([['-i', a.music] for _ in range(n)], [])
    fc = ''.join(f'[{i}:a]aformat=sample_rates=24000:channel_layouts=mono[m{i}];' for i in range(n))
    cur = 'm0'
    for i in range(1, n):
        fc += f'[{cur}][m{i}]acrossfade=d=4[x{i}];'; cur = f'x{i}'
    fc += (f'[{cur}]atrim=0:{T},loudnorm=I=-24:TP=-3,'
           f'afade=t=in:d=1.5,afade=t=out:st={max(T - 3, 0)}:d=3[b]')
    run(*inp, '-filter_complex', fc, '-map', '[b]', '-ar', '24000', '-ac', '1', bgm)
has_fx = os.path.exists(fx)
fc = (f'[1:a]volume={a.music_volume}[bg];[0:a]asplit=2[v][sc];'
      '[bg][sc]sidechaincompress=threshold=0.04:ratio=5:attack=30:release=450[d];')
fc += ('[v][d][2:a]amix=inputs=3:normalize=0[o]' if has_fx else '[v][d]amix=inputs=2:normalize=0[o]')
run('-i', voice, '-i', bgm, *(['-i', fx] if has_fx else []), '-filter_complex', fc,
    '-map', '[o]', '-ar', '24000', '-ac', '2', mix)

w = wave.open(mix); n = w.getnframes(); ch = w.getnchannels(); sr = w.getframerate()
s = struct.unpack(f'<{n * ch}h', w.readframes(n))
def db(x): return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(len(x), 1)) / 32768 + 1e-12)
print(f'mix {T:.2f}s  sfx {"yes" if has_fx else "no"}  overall {db(s):.1f} dB  '
      f'first 2.5s {db(s[:int(2.5 * sr) * ch]):.1f} dB')
