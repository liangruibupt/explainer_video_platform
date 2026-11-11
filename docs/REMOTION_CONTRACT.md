# Remotion renderer + motion editor: interface contract

Branch `feat/remotion`. This file fixes the interfaces between the four parallel parts (A–D)
and the integration step. Change it only through the integrator.

## 1. Goal and decisions

- New explainer renderer `renderer="remotion"` next to the existing Canvas2D one (`html`,
  still the default). Each Remotion job leaves a real Remotion project in
  `jobs/<id>/remotion/`.
- Scenes are written by Opus 5.5 **from video-shotcraft shot cards**: one card per scene,
  the card's recipe (`references/shots/<cat>/<card>.md`) and its Remotion demo
  (`demos/<cat>/<card>/*.tsx`) go into the scene prompt.
- The project ships a shotcraft **Motion Workbench** manifest (`src/workbench.ts`), so the
  finished film opens in the workbench as tracks (scenes, captions, sfx, voice).
- The workbench is reached through the studio itself at `/motion/` (reverse proxy), so the
  one SSM port forward to 8765 is enough. Saving from the editor renders a **new version**
  of the job (`versions/vN.mp4`); `final.mp4` stays as v1.
- **The voice track is locked** (user decision). The voice track = narration + ducked music
  (`narration_bgm.wav` mixed without sfx). On every saved version the studio restores it to
  its original position/length/volume, whatever the editor did. When a scene's start or
  length differs from the narration timeline by more than 15 frames (0.5 s), the version
  record carries a warning.
- Personal, non-commercial use: Remotion's free tier applies; video-shotcraft is Apache-2.0.
- No GPU anywhere in this work; only Opus (Bedrock) and Polly.

## 2. Paths and the project layout

| Thing | Path |
|---|---|
| shotcraft checkout, pinned at `3ac67b7` | `$EVP_TOOLS/shotcraft` (= `~/.cache/explainer-video/shotcraft`, a git worktree of `/home/ubuntu/workplace/video-shotcraft`) |
| shared Remotion install (remotion 4.0.484, React 19.2.7, tsc) | `$EVP_TOOLS/remotion-base/node_modules` |
| template (fixed files) | `server/app/remotion/template/` |
| a job's project | `jobs/<id>/remotion/` |

```
jobs/<id>/remotion/
  package.json tsconfig.json remotion.config.ts      template, FIXED
  node_modules -> $EVP_TOOLS/remotion-base/node_modules   (render.link_base)
  src/index.ts src/Root.tsx src/Main.tsx src/types.ts src/workbench.ts   FIXED
  src/lib/TitleCard.tsx                               FIXED fallback scene
  src/lib/Captions.tsx                                part B
  src/lib/<helpers>.tsx                               part A may add (copied shotcraft assets/lib, keep the Apache header)
  src/scenes/sNN.tsx, src/scenes/index.ts             part A (generated per job)
  src/data/timeline.json                              part A (generated per job)
  src/data/captions.json, src/data/audio.json         part B (generated per job)
  public/img/<scene>.png public/clips/<scene>.mp4 public/background.png   part A (COPIES, never symlinks: Remotion's static server refuses symlinks)
  public/audio/voice_bgm.wav public/audio/sfx/*.wav   part B
```

`Main.tsx`, `Root.tsx`, `workbench.ts` read only the three data files and `src/scenes/index.ts`.
The template renders as-is (one title card); `render.copy_template()` + `render.still()` were
verified on this box (CJK text renders with Noto Sans CJK SC).

## 3. Scene modules (part A writes, Opus authors)

`src/scenes/sNN.tsx` (file name = scene id from `scenes.json`, lower-case) exports exactly:

```ts
export const DEFAULTS = { /* every editable value: copy, colours, font sizes, positions, toggles, data lines */ };
export const schema: PropField[] = [ /* one field per DEFAULTS key worth editing; PropField from ../types */ ];
export const Scene: React.FC<Partial<typeof DEFAULTS> & { duration?: number }>;
```

Rules (from shotcraft `references/workbench.md` §3 and our pipeline):
1. Pure function of `useCurrentFrame()` (frame 0 = scene start; the `<Sequence>` offsets it).
   No `Date.now`, no unseeded `Math.random`, no `requestAnimationFrame`, every `interpolate`
   clamped.
2. Rhythm stays constant (entrance lengths, stagger, easing, camera path). Only context-level
   values go into `DEFAULTS`/`schema`.
3. Use `props.duration` for the exit and for spreading the scene's beats; never hard-code an
   absolute frame past `duration` (the climax must land inside the scene).
4. Keep the bottom 15 % of the frame clear for captions. Support 1920×1080 and 1080×1920
   (read `useVideoConfig()` width/height for layout, not for timing).
5. Assets only through `staticFile("img/<id>.png")`, `staticFile("clips/<id>.mp4")` (use
   `<OffthreadVideo muted>`), `staticFile("background.png")`. No network.
6. Imports allowed: `react`, `remotion`, `../types`, `../lib/*`. Nothing else is installed.
7. `src/scenes/index.ts` = `import * as sNN from "./sNN"` for every scene and
   `export const SCENES: Record<string, SceneModule> = { s01, s02, ... }`.
8. A scene that fails type-check or its still render (after one regeneration with the error)
   becomes `export { Scene, schema } from "../lib/TitleCard"` plus `DEFAULTS` built from the
   scene's narration (see `template/src/scenes/s01.tsx`), and `card: "fallback/title-card"`.
9. **Layout check** (vision, on by default; `EVP_LAYOUT_CHECK=0` turns it off). tsc and the
   midpoint still cannot see layout defects, so after them every generated scene renders 5
   stills: two in the entrance (scene frames ~12 % and ~25 %) and three settled
   (~45 %, ~75 %, duration−12), none before frame 12. (Measured: the TCP header boxes covered
   the title from scene frame 12 to ~45 of 281, and were clear by 25 %.) All stills of a pass
   (every scene) render in ONE batch, `render.stills()` → `stills.cjs`: one webpack bundle
   and one headless browser, PNG pixel-identical to `remotion still` (`EVP_BATCH_STILLS=0`
   = the old one-process-per-frame path). The midpoint still check uses the same batch. ONE
   Opus call (`llm.ask_images`, stills downscaled to JPEG, long side 960) per scene, up to 4
   at once, reviews them with the narration/visual and the frame size. The prompt walks every still
   through fixed checks: anything touching/crossing a frame edge (also during camera moves),
   labels over data points/lines/labels, mostly empty panels or chart areas, covered heading,
   text height under 2.2 % (16:9) / 1.8 % (9:16) of frame height (with `height_pct` of the
   smallest text), and the bottom 15 % caption band; no style/taste. Reply: strict JSON
   `{ok, issues: [{kind: clipped|overlap|empty|covered_title|too_small|caption_band|other,
   where, fix, height_pct?}]}`. In the first review an unparsable reply
   counts as ok (logged); a failed call or still marks the scene `skipped`.
   **Only clear defects trigger the rewrite** (`generate.blocking`): `clipped`,
   `covered_title`, `overlap`, `caption_band`, and `too_small` with `height_pct` < 1.2
   (16:9) or < 1.5 (9:16; portrait labels at 1.4–1.6 % stayed visibly small without a rewrite)
   (truly unreadable). `empty`, `other` and readable-but-small text are recorded only:
   status `noted`, no rewrite (`EVP_LAYOUT_REWRITE=all` restores rewriting on every issue).
   A scene with a clear defect is regenerated ONCE with the full issue list as the error text,
   then re-checked (static check, tsc, still) and reviewed again; this confirmation pass gets
   the earlier issues and must re-check each. Outcome, counted on clear defects: none left →
   `fixed` (rewrite kept; remaining non-blocking issues stay in `after`); fewer → `improved`
   (rewrite kept); otherwise, or when the rewrite breaks or its confirmation is
   unparsable/failed → `issues_left` with the previous module restored (and its
   `.check/<stem>.png` re-rendered). Layout issues alone never lead to the title-card
   fallback. Per scene, `remotion/.check/result.json` → `layout[<id>]` =
   `{status: ok|noted|fixed|improved|issues_left|skipped, frames, before, after, rewrite?,
   rewrite_issues?, note?}` (plus `layout_calls`, `layout_seconds`, and `timing`: `total_s`,
   `layout_wall_s`, and per phase `main`/`layout` the count `n` and summed seconds `s` of
   `still`, `vision`, `generate`, `rewrite`, `tsc`, `pick`; parallel calls sum above wall time);
   `job["scenes"][i]["layout"]` = the status and, when issues remain,
   `layout_issues` (short strings). Stills: `.check/layout/<stem>.v1|v2.f<frame>.png`.
   `EVP_REMOTION_PIN_CARDS=1` skips the card picker and reuses `job["scenes"][i]["card"]`
   (for comparable timing runs). Known limit: the reviewer has false negatives, so `fixed`
   means "the reviewer saw no clear defect", not a guarantee.

`src/data/timeline.json` (type `Timeline` in `types.ts`): `fps` 30, `width`, `height`,
`background`, `revision` (any string that changes per generation), `scenes:
[{id, label, from, duration, card}]` with `from`/`duration` = cumulative `durations.json`.
`card` = `"<category>/<card>"`. Part A also sets `job["scenes"][i]["card"]` to the same value.

## 4. Workbench manifest

`template/src/workbench.ts` is fixed: shots = scenes (cardId `scene-<id>`, `durationProp`
`duration`), captions = caption lines (one shared card `caption`), `sfx` = audio.json sfx,
`bgm` = the voice track labelled 「配音（已锁定）」, `original` = `Main`. Parity between
`ProjImported` and `ProjOriginal` (`npm run parity` in the workbench) must hold; part C runs it.

## 5. Captions and audio data (part B)

`src/data/captions.json` (type `CaptionLine[]`): lines split exactly as `captions.py` splits
them, `from`/`duration` in absolute frames, `chars[i] = [start, end]` frames relative to
`from`, `chars.length == text.length`. Empty array when `job["captions"]` is false.

`src/lib/Captions.tsx` keeps the exported names `Captions`, `CAPTION_DEFAULTS`,
`CAPTION_SCHEMA`, and looks like the ASS burn-in of `captions.py` (font, size relative to the
frame, white text, gold `#ffd700` per-character highlight, dark outline, bottom margin 6 % of
height). When the editor changes `text` so its length no longer matches `chars`, spread the
highlight evenly over the line instead of breaking.

`src/data/audio.json` (type `AudioData`): `voice = {id:"voice", from:0, duration:<total
frames>, src:"audio/voice_bgm.wav", volume:1}`; `sfx = [{id, from (absolute), duration, src:
"audio/sfx/<kind>.wav" | copied file, volume, label: kind}]` from each scene's `sfx` cues.
The sfx sounds are the same synthesized ones as `sfx.py` (same ffmpeg filters, one file per
kind). Levels must match the html path's mix within ~2 dB (measure).

Pipeline order for Remotion jobs (already wired in `pipeline.py`): narrate → scene_code
(`remotion.write_project`) → (sfx.py skipped) → mix (no sfx.wav) → captions.py (words.json) →
capture = `remotion.prepare_media` + `render.render(proj, final.mp4)` → compose (poster and
duration only).

## 6. `server/app/remotion/render.py` (FIXED, shared)

`copy_template(proj)`, `link_base(proj)`, `typecheck(proj, log)`, `still(proj, frame, out,
log)`, `render(proj, out, log, *, entry, comp, props, public_dir, cwd, timeout)`. Concurrency
defaults to 2 (`EVP_REMOTION_CONCURRENCY`); the box has 4 cores. Need a change? Write it in
your report; do not edit the file.

## 7. Motion editor + versions (part C)

Workbench runtime: `$EVP_TOOLS/shotcraft/workbench`, one dev server at a time on
`127.0.0.1:5198`, linked to one job's project (switching jobs restarts it; only restart a
server you started, tracked by a pid file). Start it detached (`setsid`) so an agent or studio
restart does not kill it. Our changes to the vendored workbench live as patch files in
`infra/motion/patches/*.patch`, applied idempotently by `infra/motion/setup.sh` (also does
`npm install`, gen-index, and fetches gallery media if cheap). Keep patches small.

HTTP (router `server/app/motion_api.py`, logic in `server/app/motion/`):

| Method | Path | Result |
|---|---|---|
| POST | `/api/jobs/{id}/editor` | link + (re)start the workbench for this job → `{job_id, url:"/motion/?import=project"}`; 409 if the job is not `renderer=="remotion"` and `status=="done"` |
| GET | `/api/motion/status` | `{running, job_id, port}` |
| POST | `/api/motion/stop` | stop the server we started |
| ANY | `/motion/{path}` (+ whatever absolute paths the workbench page needs) | reverse proxy to the dev server (vite `base` = `/motion/`; HMR off or proxied). The UI itself (`index.html`, `assets/*.js|css`) is served from a production build made on every editor open (`workbench.build`, ~3 s, `.evp-dist/`, pre-gzipped): 4 requests / ~0.6 MB on the wire instead of ~300 dev modules / ~15 MB, which took about a minute through an SSM port forward. Public files and `/motion/api/export` still go to the dev server; if the build fails the dev UI is served as before |
| GET | `/api/jobs/{id}/versions` | `[{n, file, status: "queued"\|"rendering"\|"done"\|"failed", created, warnings: [str], error}]`; entry `n=1` is `final.mp4` |
| POST | `/api/jobs/{id}/versions` | body `{project: <workbench ProjectData>}` → 202 + the new record; renders in the background |
| GET | `/api/jobs/{id}/versions/{n}/video` | the mp4 |

Saving a version: validate the JSON (size cap, fps/size equal to the job), apply the voice
lock (§1), render with the workbench's own entry (`src/remotion/index.ts`, comp `Main`,
props `{project, renderExact: true}`, `--public-dir=.render-public` synced like the vite
export plugin does) into `jobs/<id>/versions/vN.mp4`, store records in
`jobs/<id>/versions/index.json` (atomic write). Never touch `job.json`.

The project JSON lives in the workbench's `localStorage["shotcraft-workbench-project-v1"]`;
because the editor is same-origin under `/motion/`, the studio page reads it from the iframe.

## 8. UI (part D)

- `Workbench.jsx` (home form): 「渲染方式」 select: `html` 「Canvas 绘制（默认）」 /
  `remotion` 「Remotion 动效卡（可在编辑器里改）」, sent as `renderer`.
- `JobPage.jsx`: renderer badge; for Remotion jobs each scene shows its shot card (link to the
  gallery `https://vincentwei1021.github.io/video-shotcraft/`); 「在动效编辑器中打开」 button
  (done Remotion jobs only) → `/jobs/:id/edit`; versions list with players and warnings.
- New `MotionEditPage.jsx` (route already wired): calls `POST /api/jobs/{id}/editor`, shows the
  workbench in a full-height iframe, top bar with 「保存为新版本」 (reads the iframe's
  localStorage key, POSTs it, polls the version until done/failed, shows warnings) and the
  note 「配音轨已锁定：保存时恢复原配音；镜头时长和配音偏离超过 0.5 秒会提示」.

## 9. File ownership

| Part | Owns (creates/edits only these) |
|---|---|
| A generator | `server/app/remotion/project.py`, `server/app/remotion/generate.py`, `server/app/remotion/catalog.py`, new files under `server/app/remotion/template/src/lib/` except `Captions.tsx`/`TitleCard.tsx`, `server/tests/test_remotion_project.py` |
| B captions/audio | `server/app/remotion/media.py`, `server/app/remotion/template/src/lib/Captions.tsx`, `skills/explainer-video-pipeline/scripts/sfx.py` (only to make its filter table importable, behaviour unchanged), `server/tests/test_remotion_media.py` |
| C editor | `server/app/motion_api.py`, `server/app/motion/**`, `infra/motion/**`, `server/tests/test_motion*.py` |
| D UI | `web/src/pages/MotionEditPage.jsx`, `web/src/pages/JobPage.jsx`, `web/src/pages/Workbench.jsx`, `web/src/components/Motion*.jsx`, CSS appended at the end of `web/src/styles.css` under a `/* motion editor */` comment |
| Integrator | everything else: this file, fixed template files, `render.py`, `__init__.py`, `pipeline.py`, `main.py`, `main.jsx`, `Layout.jsx`, docs, README, workflow nodes |

Need a change in a file you do not own? List it under "integration requests" in your report.

## 10. Rules for every part

- Branch from `feat/remotion` into your own worktree; push only your branch by explicit name
  (`git push origin <branch>`); no PR, no merge.
- Never send several edits to one file at the same time; edit sequentially and read back.
  Before committing, grep that every intended change is present.
- Do not write code through shell heredocs (guardrails block them); use the file editor.
- Gate pushes on tests with the real exit code (`pytest ... > log; rc=$?`), not a pipe.
- Your own data dir (`EVP_DATA=<worktree>/data`) and studio port if you need one
  (A 8791, B 8792, C 8793, D 8794). Copy sample jobs from
  `/home/ubuntu/workplace/explainer_video_platform/data/jobs/` (e.g. 西湖
  `20261103-150536-b75769` with illustrations, TCP 9:16 `20261103-032718-790cba`); never
  write into that directory. Do not touch the studio on 8765.
- Python: `~/.cache/explainer-video/venv/bin/python`. Long-running servers: separate command
  from polling, and kill by recorded pid only.
- Report: what you built, how you verified it (numbers, stills you looked at), integration
  requests, known gaps.
