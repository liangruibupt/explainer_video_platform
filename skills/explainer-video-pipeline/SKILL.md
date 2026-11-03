---
name: auto/explainer-video-pipeline
description: Make a narrated explainer video (Opus storyboard, code-drawn frames, Polly voice, word captions, music, SFX) with swappable renderers.
triggers: explainer video, make a video, narrated video, remotion video, manim video, science explainer, 讲解视频, 做视频
source: auto
session_key: slack_1793576907.538289
created_at: 2026-11-02T11:40:00Z
---

# explainer-video-pipeline (auto-generated)

## When to use
The user wants a 1–3 minute narrated explainer about a topic (science, a product,
a technology), delivered as one MP4 with voice-over, captions, music and sound effects.

## Parameters and defaults
Ask only for the topic; take every other default unless the user names a value.
Say in one line which values you used.

| Parameter | Default | Other values |
|---|---|---|
| `topic` | required | — |
| `storyboard_model` | `claude-opus-5.5` writes the script, narration and storyboard | any model id from `kiro-cli chat --list-models` |
| `video_engine` | `opus-5.5`: `claude-opus-5.5` writes every scene as code that draws each frame | another code model, e.g. `claude-sonnet-5.5`; `seedance` (BytePlus ModelArk API, needs `ARK_API_KEY`); `hybrid` (Seedance clips inside code-drawn scenes) |
| `renderer` | `remotion` (React/SVG; Remotion drives headless Chromium frame by frame) | `html` (Canvas 2D / SVG / GSAP / p5.js page, captured with Playwright); `manim` (Manim Community scenes; best for geometry, graphs, vectors and step-by-step derivations) |
| `seedance_model` | `seedance-1-5-pro-251215` | any Seedance model enabled in the user's ModelArk account |
| `tts` | `polly`, neural voice `Zhiyu`, `cmn-CN`, `us-east-1` | `gtts` (free, no key, flatter voice); any other Polly voice/language |
| `captions` | on: Whisper word timing, burned in as word-by-word highlighted subtitles | off |
| `whisper_model` | `small` (CPU, int8; about 5 s for 10 s of speech) | `base` (faster), `medium` (more accurate timing) |
| `music` | `synth` (royalty-free oscillator bed made with ffmpeg) | `none`; a path to a music file the user owns |
| `sfx` | on: events listed in `scenes.json` are synthesized and placed on their frames | off: leave `sfx` out of every scene |
| `language` | Mandarin narration | any language Polly and Whisper both support |
| `size`, `fps` | 1920×1080, 30 fps | 1080×1920 for vertical |

"Made with Opus 5.5" means Opus 5.5 is the author: it writes the script and the
drawing code, and a renderer turns that code into pixels. No text model outputs
video directly; only Seedance does. Say this once in the first reply.
If the session already runs on `claude-opus-5.5`, do the writing yourself.
Otherwise spawn one task with `model="claude-opus-5.5"` and `keep=true` for the
storyboard (step 2) and continue the same run with `spawn_continue` for the scene
code (step 5), then review its output yourself.

## Setup (once per machine)
Keep tools in a stable cache dir (e.g. `~/.cache/explainer-video/`), never in
`$KIROCREW_SCRATCH`, which is reclaimed between sessions.
- **ffmpeg**: a full static build with `aevalsrc`, `anoisesrc`, `tremolo`,
  `sidechaincompress` and `ass`/libass. Remotion's bundled ffmpeg lacks them.
  Point `FFMPEG` / `FFPROBE` at it.
- **Python venv** (`boto3 gTTS playwright faster-whisper pillow numpy`), then
  `python -m playwright install chromium-headless-shell` from that venv.
  faster-whisper 1.2.x breaks on PyAV 19 (`unexpected keyword argument
  'metadata_errors'`): pin `av==16.1.0`. Set `HF_HOME` to the cache dir so the
  Whisper model is downloaded once.
- **Manim** (only for `renderer=manim`): pip cannot build it on aarch64 (no wheels
  for pycairo, manimpango, moderngl). Without root, use micromamba:
  `micromamba create -p <cache>/manim-env -c conda-forge python=3.12 manim`.
  `Text()` with a CJK font works. `MathTex`/`Tex` need a LaTeX distribution plus
  `dvisvgm`; conda-forge's `texlive-core` on aarch64 has no `latex.fmt`, so
  formulas fail there. Write formulas with `Text()` (Unicode: F = m·a, x², √, →),
  or install a system TeX Live where the user can.

## Steps
Helpers are in this skill's `scripts/` folder.

1. **Project.** Make a project folder in a git repo under the user's workplace.
   For `renderer=remotion`, scaffold `package.json`, `tsconfig.json`
   (`resolveJsonModule: true`), `src/index.ts`, `src/Root.tsx` by hand and
   `npm i remotion @remotion/cli react react-dom`.
2. **Script and storyboard** (by `storyboard_model`). Write `scenes.json`: one
   object per scene with `id`, `narration` (the spoken line), `visual` (what is on
   screen), optional `manim` (Scene class name) and optional
   `sfx`: `[{"at": FRAME_IN_SCENE, "kind": "whoosh|hiss|pop|rise|thud|PATH"}]`.
   Use 6–10 scenes, one idea each, with a title scene and an end card. Check facts
   with web search and cite sources in the README.
3. **Reference images.** When the video shows a real object, ask for or find a
   clear photo and keep it in `ref/`. Never draw a real object's shape from
   memory: run edge detection on the photo, measure key points, and generate the
   shapes from those points with a small script kept in the repo.
4. **Narration drives timing.** Run `narrate.py scenes.json . --tts polly`.
   It writes `durations.json` (frames per scene, from the real voice length plus
   padding), `narration.wav` and `audio/sNN.mp3`. Scene code reads
   `durations.json`, so each scene lasts exactly as long as its line.
5. **Scene code** (by `video_engine`).
   - `remotion`: one React component per scene, animated with `useCurrentFrame`
     and `interpolate`/`spring` relative to the scene's start.
   - `html`: one `index.html` with `window.seek = async (frame) => {...}` that draws
     exactly that frame from the frame number and the `durations.json` array. No
     `requestAnimationFrame`, no wall clock, no unseeded `Math.random`. Paste the
     `durations.json` array into the page as a constant: a page opened as
     `file://` cannot `fetch()` a local file, and the failure is silent. GSAP: paused
     timeline + `tl.seek(frame / fps)`. p5.js: `noLoop()` + `redraw()` in `seek`.
   - `manim`: one `scenes.py` with a Scene class per scene. Read the target length
     from `os.environ["SCENE_SECONDS"]` and end with
     `self.wait(max(0.04, secs - self.renderer.time))`; size long animations from
     `secs` too. Keep the bottom 15% of the frame clear for captions.
   - `seedance`: `seedance_clips.py scenes.json .` with `ARK_API_KEY` set; it
     downloads `clips/<id>.mp4` (URLs expire after 24 h).
6. **Check stills before the full render.** Render 4–6 frames, one per key scene,
   combine them into one downscaled contact sheet, and look at that single image.
   For an independent check, spawn a fresh reviewer with only the sheet and
   `scenes.json`.
7. **Sound.** If any scene has `sfx`, run `sfx.py scenes.json .`. Then
   `mix_bgm.py . --music <music>`: it ducks the music under the voice, adds
   `sfx.wav`, writes `narration_bgm.wav`, and prints RMS levels. The music-only
   lead-in should be about −20 to −30 dB; −55 dB or lower means it is inaudible.
8. **Captions.** `captions.py scenes.json . --size <WxH>` transcribes each line
   with faster-whisper word timestamps and maps them onto the script's own
   characters, because Whisper mishears homophones (it heard 气体 as 漆体).
   It writes `captions.ass` (each character highlights when it is spoken) and
   `words.json` (per-character times, usable for on-screen emphasis in step 5).
9. **Render** (silent picture).
   - `remotion`: `npx remotion render src/index.ts <Comp> out/silent.mp4`.
   - `html`: `capture_html.py index.html out/silent.mp4 --frames <sum of durations.json>`.
   - `manim`: `manim_scenes.py scenes.py . --manim <manim-env>/bin/manim --quality h`.
     It renders each class and pads or trims it to exactly its frame count.
10. **Compose with FFmpeg.** With captions:
    `ffmpeg -i silent.mp4 -i narration_bgm.wav -vf "ass=captions.ass" -c:v libx264
    -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest final.mp4`
    (without captions, use `-c:v copy` and drop `-vf`). Check video and audio
    durations match within about 0.1 s, then pull 4–5 frames from the final MP4
    and look at them, including one mid-sentence to see the caption highlight.
11. **Ship.** Commit to a feature branch, open a PR, and after merging confirm with
    `gh api repos/<o>/<r>/git/trees/<base>?recursive=1` that the files landed.

## Gotchas
- Never put `/* */` comments inside an SVG path `d=` string; Chromium stops parsing
  at the `/`. Use 6-digit hex colours.
- Code with hardcoded absolute frame numbers can put a climax after its scene ends.
- In Manim, position labels and arrows after the object has moved (or attach them
  with `always_redraw` / `add_updater`), otherwise they stay at the old place.
  `DashedVMobject` has no points of its own: take positions from a plain shape.
- A pip-installed Playwright may want a newer Chromium build than the cached one.
- Do not edit one file with several parallel `strReplace` calls; edits get lost.
  Do not write code through shell heredocs; guardrails can block them.
- Seedance 2.x models need account activation and reject reference images with
  real human faces. Seedance 1.5 pro clips are 4–12 s.
- Remotion licence: free for individuals and companies of up to 3 people; larger
  companies need a paid Company License. `html` and `manim` avoid it.
