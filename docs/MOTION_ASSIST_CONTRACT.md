# Opus chat panel in the motion editor: interface contract

Branch `feat/motion-assist`. Fixes the interfaces between parts A–C. Builds on
`docs/REMOTION_CONTRACT.md` (§3 scene modules, §7 editor + versions). Change only through the
integrator.

## 1. Goal

On `/jobs/:id/edit` the user chats with Opus 5.5 next to the workbench and changes the film in
plain language.

- **Parameter edits** (text, colours, sizes, toggles, clip timing): Opus returns operations on
  the editor's current project JSON; the studio validates and applies them; the user sees the
  change list and clicks 「应用」, the editor reloads with the edited project. Instant, no render.
- **Scene rewrites** (change what a scene draws: "柱状图换成折线图", "加一个箭头指向 GPU"):
  Opus rewrites that scene's `src/scenes/<stem>.tsx` in a staged copy; static check, tsc, stills
  and the layout check run there; the user sees the new stills and clicks 「应用」 or 「放弃」.
  Apply copies the file into the job's project, rebuilds the editor UI (`workbench.build`, ~3 s)
  and the editor reloads. Every apply is backed up; 「撤销」 restores the previous file.
- **Out of scope**: narration text and the voice track (locked, contract §1). If the user asks
  for it, Opus says it cannot and suggests remaking the job. No GPU. `final.mp4` (v1) is never
  touched; changes reach a video only through 「保存为新版本」.
- User text is untrusted data: it goes to Opus as data, never into a shell command or a file
  path; Opus output is validated (ops schema, allowed keys, TSX static check) before use.

## 2. Data the parts share

- **Project JSON** = the workbench `ProjectData` (`$EVP_TOOLS/shotcraft/workbench/src/types.ts`):
  `tracks[].clips[]` with `id, cardId, start, duration, inOffset, speed, opacity, scale, x, y,
  props, label`. Scene clips have `cardId` `proj:shot:scene-<scene id>` (check the real prefix in
  `src/cards/projectCards.ts`); caption clips share the caption card; the voice clip is on the
  music track (src `audio/voice_bgm.wav`).
- **Allowed prop keys** for a clip = the keys already in that clip's `props` (they were filled
  from the scene's `DEFAULTS` at import). An op may only set existing keys, with the same JSON
  type (string/number/boolean); colours stay `#rrggbb`.
- **Scene context** for Opus: `jobs/<id>/remotion/src/data/timeline.json` (id, label, from,
  duration, card), each scene's narration/visual from `jobs/<id>/scenes.json`, and for the
  selected scene its TSX source.

## 3. Part A — chat + parameter edits (`server/app/motion/assist.py`, `server/app/motion/assist_api.py`)

`POST /api/jobs/{id}/assist`, body
`{message: str (1..2000), project: ProjectData, scene_id?: str, history?: [{role:"user"|"assistant", text}] (≤ 20)}`
→ 200
```json
{"reply": "中文回答",
 "changes": ["S2 标题：'三倍意味着什么' → '三年三倍的含义'"],
 "project": { ...ProjectData with the ops applied... } | null,
 "ops": [ {"op": "set_props", "clip_id": "...", "props": {"title": "..."}},
          {"op": "set_timing", "clip_id": "...", "start": 0, "duration": 120, "speed": 1.0} ],
 "rejected": ["why an op was dropped"],
 "scene_edit": {"scene_id": "growth_math", "instruction": "英文或中文的具体改法"} | null}
```
- One Opus call (`llm.ask`, JSON only) with the scene context, a compact project summary (per
  clip: id, track, cardId, label, start, duration, props) and the selected scene's TSX.
- Validate every op: clip exists; `set_props` keys/types per §2; `set_timing` never on the voice
  clip, start ≥ 0, 1 ≤ duration, 0.25 ≤ speed ≤ 4; drop invalid ops into `rejected`.
- `project` = input project with valid ops applied (deep copy), or null when there are none.
  Also return a drift warning in `changes` when a scene clip moves > 15 frames vs the timeline
  (same rule as `versions.drift_warnings`).
- `scene_edit` is set when the request needs new drawing code; A does not start it.
- 400/404/409 like the editor endpoint (job must be a done Remotion job). Chinese `detail`.

## 4. Part B — scene rewrites (`server/app/motion/scene_edit.py`, `server/app/motion/scene_edit_api.py`)

| Method | Path | Result |
|---|---|---|
| POST | `/api/jobs/{id}/scene-edits` | body `{scene_id, instruction (1..2000)}` → 202 record; 409 while another edit of this job is queued/running |
| GET | `/api/jobs/{id}/scene-edits` | list of records, newest first |
| GET | `/api/jobs/{id}/scene-edits/{n}` | record |
| GET | `/api/jobs/{id}/scene-edits/{n}/stills/{k}.png` | preview still k (0-based) |
| POST | `/api/jobs/{id}/scene-edits/{n}/apply` | only when `ready` → `{status:"applied", scene_id, defaults}`; copies the file in, rebuilds the editor UI if it is linked to this job |
| POST | `/api/jobs/{id}/scene-edits/{n}/discard` | `ready` → `discarded` |
| POST | `/api/jobs/{id}/scene-edits/{n}/undo` | the latest `applied` edit of that scene → restores the backup, rebuilds, `{status:"undone", scene_id, defaults}` (old DEFAULTS) |

Record: `{n, scene_id, instruction, status: queued|running|ready|failed|applied|discarded|undone,
created, ended, stills: [urls], frames: [scene frames], layout: {status, issues}, defaults: {..} | null,
error, log_tail}` in `jobs/<id>/remotion/.assist/edits/index.json` (atomic writes), files under
`.assist/edits/<n>/` (staged tsx, stills, backup of the previous file).

- Rewrite = one Opus call with the current module source + the instruction + §3 rules of the
  Remotion contract (reuse `generate.scene_module` / its retry-with-error path, or a sibling
  prompt in your module). Then `generate.check_tsx`, tsc on a **staged copy** of the project
  (never the live `src/` before apply), stills at `project.layout_frames(duration)` via
  `render.stills`, and the layout reviewer (`generate.review_layout`). One retry with the error
  text on tsc/still failure; layout issues are reported, not retried (keep it fast).
- `defaults` = the new module's `DEFAULTS` as JSON (evaluate it, e.g. bundle the module with the
  esbuild in `$EVP_TOOLS/remotion-base/node_modules/.bin/esbuild` and print `JSON.stringify(DEFAULTS)`).
- Runs in a background thread; recovery of stale `queued/running` records only in a startup hook.
- You may append small public helpers to `server/app/remotion/project.py` (e.g. a single-scene
  check); do not change existing behaviour.

## 5. Part C — chat panel UI (`web/src/components/MotionChat.jsx`, `web/src/pages/MotionEditPage.jsx`)

- Collapsible right-hand panel on the edit page (the workbench iframe keeps most of the width):
  scene selector (from `GET /api/jobs/{id}` scenes; default 「整片」), message list, input.
  History per job in `localStorage["evp-motion-chat-<job>"]` (last 20 turns).
- Send: flush the workbench autosave (dispatch `beforeunload` on the iframe window, as the save
  button does), read `localStorage["shotcraft-workbench-project-v1"]` from the iframe, POST §3.
- Show `reply`, `changes`, `rejected`. If `project` is set: 「应用修改」 writes it to the iframe's
  localStorage key and reloads the iframe (keep the `?import=project` URL; the manifest is
  unchanged, so the workbench keeps the stored project). Keep the previous project for
  「撤销这次修改」.
- If `scene_edit` is set: 「让 Opus 改这段画面」 → POST §4, poll every 2 s, show progress, then
  the stills (thumbnails, click to enlarge) and layout status, with 「应用」/「放弃」. After
  apply: replace `props` of that scene's clip(s) with `defaults` (keep the user's timing), write
  the project, reload the iframe after the rebuild. 「撤销」 calls undo and does the same with the
  old defaults.
- Explain once in the panel: 旁白和配音不能在这里改；改完画面后用「保存为新版本」出片.

## 6. File ownership

| Part | Owns |
|---|---|
| A | `server/app/motion/assist.py`, `server/app/motion/assist_api.py`, `server/tests/test_motion_assist.py` |
| B | `server/app/motion/scene_edit.py`, `server/app/motion/scene_edit_api.py`, appended helpers in `server/app/remotion/project.py`, `server/tests/test_scene_edit.py` |
| C | `web/src/components/MotionChat.jsx`, `web/src/pages/MotionEditPage.jsx`, CSS appended at the end of `web/src/styles.css` under `/* motion chat */`, dev-only mocks under `web/` |
| Integrator | everything else (`main.py` router wiring is already done), docs, README |

## 7. Rules

Same as `docs/REMOTION_CONTRACT.md` §10: own worktree from `feat/motion-assist`, push only your
branch by name, no PR/merge; one edit at a time per file, read back; no heredocs for code;
pytest gated on the real exit code. Ports: A 8801, B 8802, C 8803. Test data: copy
`/home/ubuntu/workplace/explainer_video_platform/data/jobs/20261111-075413-d48e69` (三年三倍,
16:9, 7 scenes, Remotion) into your own `EVP_DATA`; never write to the main checkout's data or
touch the 8765 studio. The workbench dev server (port 5198) is a singleton shared with the 8765
studio: B and C must not start/relink it; test the editor parts with mocks or with A/B's own
functions, and leave live-editor checks to the acceptance step. Report: what you built, how you
verified it (numbers, images you looked at), integration requests, gaps.
