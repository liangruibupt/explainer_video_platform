# Phase 5 contracts: feedback-driven skill improvement (DESIGN.md section 8)

Flow: a user marks a work 不满意 with a reason -> the system snapshots the run record ->
an analysis agent decides "one-off failure" (rerun that node with a new seed / prompt, no
skill change) or "rule problem" (draft a change to the skill / node defaults) -> rule changes
become a **GitHub PR** with the feedback, run links, reason and a reproduction input whose
QC metrics are compared before vs after -> **a person merges it; nothing changes live
before that** -> after the merge the same input is rerun and the result is attached to the
original feedback.

Guard rails (all parts must respect them):
- Never push to master, never merge, never edit files in the live checkout. Proposals are
  made in a separate git worktree under `data/feedback/worktrees/<proposal_id>` on a branch
  `skillfix/<proposal_id>`, pushed with that literal name, PR opened with `gh api`.
- A rule-level proposal needs at least `RULE_MIN = 3` feedback items in the same cluster
  (same target + same symptom), unless the user presses 立即起草 on one item.
- Every proposal carries a reproduction input and before/after QC metrics; the PR body shows
  them. A proposal whose "after" is not better on its target metric is kept as a draft
  (not opened as a PR) and shown with the numbers.
- Only allowed edit targets (anything else is rejected before a worktree is touched):
  `skills/explainer-video-pipeline/SKILL.md`, `skills/explainer-video-pipeline/scripts/*.py`
  (parameters/defaults only, diff <= 40 lines), `skills/explainer-video-pipeline/scripts/pronounce.json`,
  `server/app/workflows/nodes.py` param defaults, `server/app/pipeline.py` constants
  (`UPPER_CASE = ...` lines), `server/app/llm.py` prompt strings.
- Feedback text is untrusted user data: it is quoted into prompts as data, never executed,
  and never copied into a shell command.

## Skill version
- File `skills/explainer-video-pipeline/VERSION` (semver, start `1.0.0`). A merged skillfix PR
  bumps the patch number (the proposal's diff includes the bump).
- Every job (`job.json` key `skill_version`) and workflow run (`run.json` key
  `skill_version`) records the version and the git commit (`skill_commit`, short sha of
  HEAD of the serving checkout) at creation.

## Data (all under `data/feedback/`)
`items/<fb_id>.json`  (fb_id = `fb_<YYYYmmdd-HHMMSS>-<6 hex>`)
```
{ "id", "created", "target": {"kind": "job"|"workflow"|"image"|"video"|"music"|"avatar",
                              "id": "<job/run/item id>", "node_id": "<optional>"},
  "reasons": ["画面"|"声音"|"字幕"|"内容"|"节奏"|"其他"], "text": "<= 500 chars",
  "snapshot": { "skill_version", "skill_commit", "record": <job.json or run.json copy>,
                "qc": <qc.measure(target) result>, "files": {"<name>": "<path under data/>"} },
  "status": "new"|"analyzing"|"rerun"|"clustered"|"proposed"|"merged"|"resolved"|"dismissed",
  "analysis_id": null|"an_...", "proposal_id": null|"pr_...", "resolution": null|{...},
  "history": [{"ts", "event", "detail"}] }
```
`analyses/<an_id>.json`
```
{ "id", "feedback_id", "created", "verdict": "one_off"|"rule"|"unclear",
  "layer": "image"|"video"|"music"|"voice"|"captions"|"storyboard"|"mix"|"avatar"|"other",
  "symptom": "<short normalized key, e.g. music_over_voice, overrun_duration, misread_word>",
  "explanation": "<Chinese, 1-3 sentences>",
  "evidence": ["<metric or record field cited>"],
  "action": { "type": "rerun", "node_id"|"step", "changes": {"seed": n, "prompt": "..."} }
          | { "type": "propose", "target_file", "intent": "<what rule to change>" }
          | { "type": "ask", "question": "<what to ask the user>" },
  "cluster": "<layer>:<symptom>", "cluster_count": n }
```
`proposals/<pr_id>.json`
```
{ "id", "created", "cluster", "feedback_ids": [...], "analysis_ids": [...],
  "target_files": [...], "diff": "<unified diff>", "reason": "<Chinese>",
  "repro": { "kind": "job"|"workflow", "input": {...create_job / workflow args...} },
  "qc_before": {...}, "qc_after": {...}, "target_metric": "<qc key>", "improved": bool,
  "branch": "skillfix/<pr_id>", "pr_url": null|"https://github.com/...", "pr_number": null|n,
  "state": "drafting"|"checking"|"draft"|"open"|"merged"|"closed"|"failed",
  "verify": null|{ "rerun_id", "qc", "ok": bool } }
```

## Shared Python APIs
- `app.feedback.store`: `create(target, reasons, text) -> item`, `get(fb_id)`, `list_items(status="", limit=200)`,
  `update(fb_id, fn)`, `add_history(fb_id, event, detail)`, same trio for analyses
  (`save_analysis`, `get_analysis`) and proposals (`save_proposal`, `get_proposal`, `list_proposals`).
- `app.feedback.qc.measure(target: dict) -> dict`: deterministic metrics, no LLM, no GPU.
  Keys (missing when not applicable): `duration_s`, `target_s`, `duration_ratio`,
  `lufs`, `voice_lufs`, `music_lufs`, `voice_minus_music_db`, `caption_count`,
  `caption_max_cps` (chars per second), `caption_overlap_s`, `scene_count`,
  `silence_ratio`, `failed_steps`, `node_errors`, `avatar_identity_min` (only when the
  avatar trace tool is available; optional). Pure function of files on disk.
- `app.feedback.analyze.analyze(fb_id) -> analysis` (Opus via `llm.ask`; deterministic
  pre-checks first, e.g. voice_minus_music_db < 6 => mix:music_over_voice).
  `app.feedback.analyze.act(an_id) -> dict`: for `rerun`, calls the existing retry paths
  (`workflows.engine.retry`/`start` with changed params, or `pipeline` job rerun from a step);
  for `propose`, calls `proposals.draft(cluster)` when the cluster reaches RULE_MIN.
- `app.feedback.proposals`: `draft(cluster, force=False) -> proposal`, `check(pr_id)` (runs
  the repro input on the proposal worktree code with a separate EVP_DATA and on master, QC
  both), `open_pr(pr_id)`, `sync(pr_id)` (reads PR state via `gh api`; on merged ->
  `verify(pr_id)` reruns the repro on the updated live checkout after fast-forward is done by
  a person/agent, attaches result to every feedback in the proposal, `notify()`).
- Notifications: `app.tasks.notify.notify(title, body, url)` for: analysis done, rerun done,
  proposal ready (draft or PR opened), PR merged + verification result.

## HTTP API (router prefix `/api/feedback`, file `server/app/feedback/api.py`)
- `POST /api/feedback` {target, reasons, text} -> item (and starts analysis in a thread)
- `GET /api/feedback?status=` list; `GET /api/feedback/{fb_id}` item + analysis + proposal
- `POST /api/feedback/{fb_id}/analyze` (re-run analysis), `POST /api/feedback/{fb_id}/act`
  (apply the analysis action), `POST /api/feedback/{fb_id}/draft` (立即起草, force),
  `POST /api/feedback/{fb_id}/dismiss`
- `GET /api/feedback/clusters` -> [{cluster, count, feedback_ids, proposal_id}]
- `GET /api/feedback/proposals`, `GET /api/feedback/proposals/{pr_id}`,
  `POST /api/feedback/proposals/{pr_id}/check`, `POST /api/feedback/proposals/{pr_id}/open`,
  `POST /api/feedback/proposals/{pr_id}/sync`
- `GET /api/feedback/skill` -> {version, commit, history: [merged proposals]}

## UI
- Component `web/src/components/FeedbackButton.jsx`: `<FeedbackButton target={{kind, id, node_id?}} />`
  renders a 不满意 button -> small dialog with the six reason chips + one-line text ->
  POST; then shows 已记录 and a link to `/feedback?item=<id>`.
- Page `web/src/pages/FeedbackPage.jsx` at `/feedback`, nav 反馈与改进 (系统 group, near 任务中心):
  tabs 反馈 (list with status, analysis verdict and explanation, action buttons 重跑 / 立即起草 /
  忽略), 聚类 (cluster counts vs RULE_MIN), 改进提案 (diff view, before/after QC table, PR link,
  state, verification result), skill 版本 (version, commit, merged history).

## Split and ownership
| Part | Owns | Port |
|---|---|---|
| A capture + records + QC | `server/app/feedback/__init__.py`, `store.py`, `api.py`, `qc.py`, `skills/explainer-video-pipeline/VERSION`, the `skill_version`/`skill_commit` lines in `pipeline.create_job` and `workflows/engine.start`, `server/tests/test_feedback_store.py`, `test_feedback_qc.py` | 8791 |
| B analysis + one-off reruns | `server/app/feedback/analyze.py`, `server/tests/test_feedback_analyze.py` | 8792 |
| C proposals + PR + verify | `server/app/feedback/proposals.py`, `server/app/feedback/gitops.py`, `server/tests/test_feedback_proposals.py` | 8793 |
| D UI | `web/src/components/FeedbackButton.jsx`, `web/src/pages/FeedbackPage.jsx`, the FeedbackButton lines in `JobPage.jsx`, `WorkflowPage.jsx`, `ImagePage.jsx`, `VideoPage.jsx`, `MusicPage.jsx`, `AvatarPage.jsx`, route + nav lines in `main.jsx`, `Layout.jsx` | 8794 |

Parts B, C, D code against the shapes above; where they need another part's function before it
exists, they write a tiny local stub in their TEST files only. The API file (A) imports
`analyze` and `proposals` lazily (`importlib`, skip when missing) so A's endpoints that need
them return 501 until integration. `main.py` includes `app.feedback.api.router` with the same
guarded pattern as phase 4 (integration adds that line).

Integration (one agent, later): merge the four branches, wire main.py, run the full flow live
in a separate EVP_DATA: three real jobs whose captions are deliberately too fast for the voice
(e.g. a dense topic with a short `seconds` target) + three feedback items "字幕太快看不过来" ->
cluster reaches 3 -> proposal drafted -> check runs the repro before/after -> PR opened (state
draft or open) -> the human merges or closes it.
