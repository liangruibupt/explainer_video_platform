# Re-voicing edited scenes: interface contract

Branch `feat/revoice`. Fixes the interfaces between parts A–C. Builds on
`docs/REMOTION_CONTRACT.md` (§5 captions/audio, §7 editor + versions, "Versions and the editor")
and `docs/MOTION_ASSIST_CONTRACT.md`. Change only through the integrator.

## 1. Goal

After the user changes a scene's data in the motion editor, the narration of the affected scenes
can be changed and re-voiced so picture, captions and voice say the same thing, and the whole
film still sounds like one voice-over. Four layers (user-approved):

1. **Voice**: same Polly voice (`job["voice"]`, default Zhiyu, neural, cmn-CN), same SSML
   lexicon (`pronounce.json`), same lead/tail padding as `narrate.py`. NO time-stretching
   (it changes pace and timbre). A scene whose new audio is shorter keeps its original length
   (padding absorbs it); a longer one gets longer and every later scene shifts by the delta.
2. **Mix**: the whole narration is re-concatenated and the whole bed re-mixed with `mix_bgm.py`
   (same music choice/volume as the job, ducking continuous over the film), producing a new
   `voice_bgm.wav`. Never splice a mixed file.
3. **Captions**: changed scenes get new per-character timing from faster-whisper mapped onto the
   NEW narration text (captions.py logic); unchanged scenes keep their timing, shifted by any
   earlier delta.
4. **Content**: an Opus consistency check over the whole film (narration + captions + scene
   props) lists every place that disagrees with the edited data and drafts the fixes; the user
   confirms before anything is re-voiced.

**Voice drift guard**: Polly is deterministic today (verified: all 7 scenes of job
20261111-075413-d48e69 re-synthesized byte-identical a day later). Every re-voice also
re-synthesizes ONE unchanged scene and compares bytes with its stored `audio/sNN.mp3`. If they
differ, the take is marked `voice_drift: true` and the UI offers 「整片重新配音」 (re-voice all
scenes) instead of mixing old and new voice.

Narration text for unchanged scenes is never modified. `final.mp4`, v1 and saved versions are
never touched; a new take reaches a video only via 「保存为新版本」.

## 2. Takes (part A)

A **take** is one full voice track for the job: `jobs/<id>/remotion/.voice/takes/<n>/` with
`scenes.json` (narrations used), `durations.json`, `audio/sNN.mp3`, `narration.wav`,
`voice_bgm.wav`, `words.json`, and the derived `timeline.json`, `captions.json`, `audio.json`
(same formats as `src/data/*`, REMOTION_CONTRACT §3/§5). Take 0 is the original (built lazily
from the job dir + current `src/data`). The live project uses exactly one take; `.voice/live`
holds its number.

HTTP (`server/app/motion/revoice.py` logic, `server/app/motion/revoice_api.py` router):

| Method | Path | Result |
|---|---|---|
| POST | `/api/jobs/{id}/revoice` | body `{narrations: {scene_id: text}, all?: bool}` (only changed scenes; `all` = re-voice every scene) → 202 record; 409 while one is running |
| GET | `/api/jobs/{id}/revoice` | records, newest first |
| GET | `/api/jobs/{id}/revoice/{n}` | record |
| GET | `/api/jobs/{id}/revoice/{n}/audio/{scene_id}.mp3` | preview of a re-voiced scene |
| POST | `/api/jobs/{id}/revoice/{n}/apply` | body `{project}` (current workbench project) → `{status:"applied", project, changes}` |
| POST | `/api/jobs/{id}/revoice/{n}/undo` | latest applied take → previous take restored, `{status:"undone", project}` (body `{project}`) |

Record: `{n, status: queued|running|ready|failed|applied|undone, created, ended, scenes:
[{id, old_text, new_text, old_frames, new_frames, delta_frames}], shift_from: scene_id|null,
total_frames_old, total_frames_new, voice_drift: bool, levels: {voice_rms_db, music_gap_db},
error, timing: {polly_s, whisper_s, mix_s, total_s}}`.

**Apply** (server-side, atomic): back up the live `src/data/{timeline,captions,audio}.json` and
`public/audio/voice_bgm.wav` into `.voice/backup-<n>/`, copy the take's files in, update
`scenes.json`-derived narration in `src/data/timeline.json` labels only if needed, keep
`timeline.revision` UNCHANGED, rebuild the editor UI if it is linked to this job
(`workbench.rebuild`). Transform the given project so the user's edits survive:
- scene clips: keep props/speed/x/y/etc.; start += cumulative delta of earlier scenes;
  duration += own delta when the scene got longer and the clip still had its original length;
- caption clips: replaced by clips built from the take's `captions.json` (same card id, same
  per-clip visual props as the user's existing caption clips, e.g. colour/size);
- sfx clips: start += cumulative delta of the scene they fall in;
- voice clip: start 0, duration = new total;
- `source` = new manifest key `${name}@${total}f#${revision}` so the workbench keeps the
  project (does not re-import) — compute exactly like `manifestKey` in
  `$EVP_TOOLS/shotcraft/workbench/src/cards/manifest.ts`;
- validate with `versions.validate` against the NEW timeline.

**Versions**: `snapshots.snapshot` must also copy `src/data/*.json` and
`public/audio/voice_bgm.wav` (+ `.voice/live`), and `restore` must restore them, so each
version keeps its own voice and the voice lock (`versions.apply_voice_lock`) locks to that
version's voice. Versions saved before this have no voice snapshot: they keep the live voice
(as today, documented).

## 3. Part B — consistency check (`server/app/motion/consistency.py`, router in the same file or `consistency_api.py`)

`POST /api/jobs/{id}/consistency`, body `{project, instruction?: str}` → 200
```json
{"summary": "中文一句话",
 "issues": [{"scene_id": "token_engine", "where": "narration|caption|props",
             "found": "约百分之二十五", "expected": "约百分之三十", "why": "S2 新的拆分 0.6/2.0 = 30%"}],
 "narrations": {"token_engine": "……预估贡献约百分之三十。", "migration_engine": "…", "recap": "…"},
 "props": [{"op":"set_props","clip_id":"…","props":{…}}],
 "rejected": []}
```
- One Opus call (`llm.ask`, strict JSON) with every scene's narration (`scenes.json`), the
  caption texts per scene from the project, the scalar props of every scene clip, and the
  optional user instruction ("把配音里的数字改成和画面一致"). User text is data.
- `narrations` holds full new narration texts only for scenes that must change; each must stay
  close to the original wording (change only what is inconsistent; reject edits that change
  more than ~40 % of the characters → `rejected`). Never invent new facts.
- `props` go through the same validation as `assist.py` (reuse its validator); caption `text`
  edits are allowed there too.
- 400/404/409 like the assist endpoint; 502 on Opus failure AND on a reply with no usable JSON
  (an empty 200 would read as "consistent"); Chinese `detail`.
- The response also carries `changes` (Chinese change lines) and `project` (the input project
  with the valid props applied, or null). Narrations are read from the LIVE take
  (`revoice.live_scenes`), since `scenes.json` is never rewritten. Scene props the user changed
  from the module's `DEFAULTS` are treated as the source of truth and never overwritten.

## 4. Part C — UI (`web/src/components/MotionVoice.jsx`, hooks into `MotionChat.jsx` / `MotionEditPage.jsx`)

- A 「配音」 tab/section in the right panel: per scene the current narration (editable textarea,
  changed ones highlighted), a 「检查一致性」 button (POST §3 with the current project; fills the
  textareas with `narrations`, lists `issues`, offers 「应用参数修改」 for `props` via the existing
  writeProject flow), and 「重新配音改动的段落（N 段）」 (POST §2 with only changed scenes).
- While running: progress + elapsed. When ready: per changed scene old/new text, a play button
  for the new audio, frame delta (「+0.43 秒，后面的段落顺延」 / 「长度不变」), drift warning with
  「整片重新配音」, then 「应用到编辑器」 → POST apply with the current project (flush first),
  write the returned project and reload the iframe (same about:blank → write → reload order as
  writeProject). 「撤销配音」 → undo.
- The Opus chat: when an assist reply refuses a narration change, show a link 「去配音面板修改」.
- Update the voice-lock note: 「配音轨已锁定，改旁白请用『配音』面板重新配音」.

## 5. File ownership

| Part | Owns |
|---|---|
| A | `server/app/motion/revoice.py`, `server/app/motion/revoice_api.py`, `server/app/motion/snapshots.py` (extend), `server/tests/test_revoice.py`; may add an importable function to `skills/explainer-video-pipeline/scripts/captions.py` / `narrate.py` without changing CLI behaviour |
| B | `server/app/motion/consistency.py` (+ `consistency_api.py`), `server/tests/test_consistency.py`; may import from `assist.py` read-only |
| C | `web/src/components/MotionVoice.jsx`, `web/src/components/MotionChat.jsx`, `web/src/pages/MotionEditPage.jsx`, CSS appended under `/* motion voice */`, dev mocks under `web/` |
| Integrator | `server/app/main.py` (router wiring is already done), docs, README, everything else |

## 6. Rules

As REMOTION_CONTRACT §10: own worktree from `feat/revoice`, push only your branch by name, no
PR/merge; one edit at a time per file and read back; no heredocs for code; pytest gated on the
real exit code from a log file. Ports A 8811, B 8812, C 8813. Test data: copy
`/home/ubuntu/workplace/explainer_video_platform/data/jobs/20261111-075413-d48e69` (三年三倍,
has v2) into your own `EVP_DATA`; never write the main checkout's data, never touch the 8765
studio or relink the shared workbench (port 5198). Polly region us-east-1 via the instance
role. Report: what you built, verification with numbers, integration requests, gaps.

Reference numbers (measured): S4 「约百分之二十五」→「约百分之三十」 −0.03 s; S6
「约百分之二十」→「约百分之十五」 −0.07 s; S7 「三五、二五、二十、二十」→「三五、三十、二十、十五」
+0.43 s (349 → 362 frames); per-scene RMS within 0.2 dB of the originals.
