# Digital human (数字人) module: contract

Goal: talking-presenter videos for 电商口播 / 商超口播 / pre-recorded 直播 segments.
A portrait (photo, AI image, or a short reference video) + speech audio -> lip-synced
video with matching head, face and upper-body motion. Two candidate models, compared on
the same inputs; the better one becomes the default, the other stays selectable:
- `infinitetalk`: MeiGen-AI InfiniteTalk (Apache-2.0) on Wan2.1-I2V-14B-480P (Apache-2.0)
  + chinese-wav2vec2-base. Optional lightx2v/FusionX LoRA for 4-8 steps; fp8 quant model.
- `longcat`: meituan-longcat LongCat-Video-Avatar-1.5 (MIT) on LongCat-Video, Whisper-large-v3
  audio encoder, `--model_type avatar-v1.5 --use_distill` (8 steps, required), optional
  `--use_int8`; tasks ai2v (image) and video continuation for long audio.
Real-time live streaming is OUT of scope: both render offline. "直播" here means
pre-rendered segments a person plays during a stream.

Ethics rule (in the design doc and the page): only portraits the user has the right to
use (their own photo, a consenting person, or an AI-generated face). The page shows this
note next to the upload. No watermark or "AI 生成" mark is added to outputs (user decision).

## GPU backend (EC2-first, see infra/gpu_ec2)
- Module `avatar`, config `infra/gpu_ec2/modules/avatar.json`, same shape as video.json.
  ONE worker image serves both models (request field `model`), so a warm GPU switches
  model without a new instance; if both cannot fit one image/disk, use two modules
  `avatar` (default model) and `avatar_alt`, same request/response shape.
- Region us-east-1 (no license region limits). Bucket `evp-models-747411437379-us-east-1`,
  weights prefixes `weights/infinitetalk/<sha>/` (Wan2.1-I2V-14B-480P, chinese-wav2vec2-base,
  InfiniteTalk single/ + quant_models/ if used, optional lightx2v 4-step LoRA) and
  `weights/longcat-avatar-1.5/<sha>/` (LongCat-Video base + Avatar-1.5 + whisper-large-v3).
- jobs_prefix `jobs/avatar/`, name `evp-avatar-worker`, tags project=explainer-video,
  module=avatar, instance_profile + SG `evp-gpu-worker`, idle 900 s, hard cap 4 h.
- Capacity order is chosen by the infra agent from measurements (candidates: g7e.2xlarge /
  g7e.4xlarge, g6e.2xlarge / g6e.4xlarge; on-demand then Spot, two AZs each).
- ECR image `evp/avatar:<tag>`, CUDA 12.8 (Blackwell), weights NOT in the image,
  HTTP adapter on 8080 like the other containers (`/ping`, `/invocations`).

## Worker request (JSON posted through GpuEC2.submit)
```
{
  "model": "infinitetalk" | "longcat",  # default: the winner of the comparison
  "image_b64": "<png/jpg>"            # one of image_b64 / video_b64 (reference video, v2v)
  "video_b64": "<mp4>",
  "audio_b64": "<wav or mp3>",        # required, speech; up to 600 s
  "prompt": "a woman presenting a product in a bright store, natural gestures",
  "size": "480" | "720",              # default "480"
  "fast": true,                        # lightx2v/FusionX LoRA few-step path; false = 40 steps
  "seed": 42,
  "audio_cfg": 4.0, "text_cfg": 5.0    # worker picks LoRA-appropriate defaults when fast
}
```
Response (`poll` result): `{"ok": true, "files": {"video_b64": [<s3 key>]}, "meta": {"model":
"...", "seconds": <video length>, "render_s": <wall time>, "frames": n, "size": "480",
"fast": true, "peak_vram_gb": x}}`.
The output mp4 carries the input audio. `GpuEC2.fetch(res, "video_b64")` returns bytes.

## Studio side (server/app)
- `server/app/avatar.py`: `status()` (same shape as video.status), `generate(audio: bytes,
  image: bytes|None=None, video: bytes|None=None, prompt="", model="", size="480", fast=True,
  seed=42) -> bytes mp4` (submit, poll, fetch; model "" = default). No watermark.
- Store: generations saved like image/video/music pages (`data/avatars/<id>/`), listed by
  `GET /api/avatars`, created by `POST /api/avatars` (multipart or JSON with asset ids),
  `GET /api/avatars/{id}`, file routes. Router in `server/app/avatar_api.py` exposing
  `router`; main.py includes it with the same optional-import pattern as phase 4.
- Script + voice: `POST /api/avatars` accepts either an audio asset/upload or `script` text +
  `voice` (Polly neural Chinese voices, e.g. Zhiyu; reuse narrate code paths) so a user can
  type a 口播稿 and get speech.
- Workflow node `avatar.infinitetalk` (runner `gpu:avatar`, inputs `audio: audio`,
  `portrait: image` (optional), `ref_video: video` (optional); params model, prompt, size,
  fast, seed; output `video: video`), registered from `server/app/avatar_node.py` via
  `register(NODES)` like assets/node.py. Plus template `wf_product_pitch`: product text ->
  agent writes a 30-60 s 口播稿 -> TTS -> (optional image.qwen portrait or input.asset) ->
  avatar.infinitetalk -> review -> output.save.
- Page `web/src/pages/AvatarPage.jsx` at `/avatar`, nav entry 数字人 in the 生成 group: portrait
  (upload / from library / generate), script or audio, scene prompt presets (直播间带货,
  超市货架口播, 门店促销, 产品特写讲解), model (default marked 推荐), size/fast, cost + time estimate, history.
- Settings page picks the module up automatically from infra/gpu_ec2/modules/*.json.

## Ownership
- Part 1 (infra + model test + comparison): infra/avatar/**, infra/gpu_ec2/modules/avatar*.json,
  docs/AVATAR.md (incl. the comparison and the chosen default), examples/avatar/** (sample
  outputs + README). May patch
  infra/gpu_ec2/worker/** only if the generic worker truly needs it (say so in the report).
- Part 2 (studio): server/app/avatar.py, avatar_api.py, avatar_node.py,
  server/app/workflows/templates/wf_product_pitch.json, server/tests/test_avatar*.py,
  web/src/pages/AvatarPage.jsx, and the include/route/nav lines in server/app/main.py,
  web/src/main.jsx, web/src/Layout.jsx. Codes against this contract with GpuEC2 stubbed.
- Integration (later, one agent): merge both, live end-to-end run, README/STUDIO.md/DESIGN.md.
