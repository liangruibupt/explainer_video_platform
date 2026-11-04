# 讲解视频工作室（基础版界面）

本地网页界面，调用 [基础版 skill](../skills/explainer-video-pipeline/) 制作原理讲解、幻灯片式讲解视频。单用户，只监听 `127.0.0.1`。

## 能做什么

- **工作台**：写一句主题，选时长（约 30–120 秒）、横屏或竖屏、背景音乐（可选 AI 生成）、AI 插图、AI 视频镜头、是否加逐字字幕，点「开始制作」。
- **作品详情**：实时显示制作步骤（用不到的步骤显示「跳过」）和分镜（用了 AI 插图、AI 镜头的段落有标记），完成后播放和下载 MP4；可以用同一主题重做、查看日志、删除。
- **作品集**：已完成的作品。
- **任务中心**：所有任务，包括正在制作和失败的。
- **图像**：Qwen-Image 2.1 生成 1080p 图片（16:9、9:16、1:1，可选透明背景）。页面顶部显示 GPU 状态：已就绪、启动中、已缩到 0。设计和实测见 [IMAGE.md](IMAGE.md)。
- **AI 视频**（`/video`）：MiniMax H3 生成 5 秒、768p、24 fps、带音轨的镜头（16:9 或 9:16，可设 seed），记录里直接播放。设计和实测见 [VIDEO.md](VIDEO.md)。
- **音乐**（`/music`）：YuE2-3B 按风格和歌词生成 48 kHz 立体声 FLAC，附 ABC 乐谱。「纯音乐」开关把歌词换成 `[Intro] [Instrumental] [Outro]`，并在风格末尾补上 `ambient, no drums, instrumental, no vocals`；可选 cot（full / off）和 seed。纯音乐长度 4–5 分钟，不能指定。「编排说明」（可选，中英文都行）写乐器怎么进、段落怎么发展，由 Opus 转成英文风格词拼进风格里（纯音乐时 `instrumental, no vocals` 仍放在最后），不会进歌词，所以不会被唱出来；生成记录里显示实际发给模型的风格词。见 [MUSIC.md](MUSIC.md)。
- **数字人**（`/avatar`）：一张人像（上传、素材库、或图像页的生成结果；也可以用一段参考视频）加一段口播，生成口型同步、带表情和上半身动作的口播视频，用于电商口播、商超口播和直播间预录片段（离线生成，不是实时直播）。声音可以写口播稿让 Polly 合成（默认 Zhiyu），也可以上传音频。场景（直播间带货、超市货架口播、门店促销、产品特写讲解）给出默认的英文场景描述。模型默认 InfiniteTalk（标「推荐」），可选 LongCat-Video-Avatar 1.5；清晰度 480p / 720p，快速模式（4 步）或完整质量。提交前页面按 `avatar.json` 的 `render_s_per_s`、`cold_min`（13 分钟）、`usd_per_hour` 估算生成时间和费用。只能用有权使用的人像，输出不加水印。设计和实测见 [AVATAR.md](AVATAR.md)。
- 四个生成页顶部都有 GPU 状态（已就绪、启动中、未运行），GPU 是平台自己启停的 EC2 实例，空闲 15 分钟自己关机。
- **工作流**（`/workflow`）：用节点图编排生成步骤。快捷开始有四个模板（一句话做 MV、科普讲解视频（AI 插图 + AI 背景音乐）、封面图、商品口播（数字人）），也可以用一句话让 Agent 生成工作流，或在右侧「AI 助手」里用一句话改图。点节点改参数，运行时每个节点实时显示状态和用时；上游结果有缓存，改了下游参数重跑时上游不再调 GPU；人工审核节点暂停等批准或打回，失败的节点可以单独重跑。GPU 生成节点（图像、视频、音乐）可以设「候选数」2–4：按 seed、seed+1… 各生成一个，节点暂停等你选一个往下走。选过之后，重跑时如果这几个候选全都来自缓存、和上次选的是同一组（同样的候选缓存键、同样的顺序），就直接沿用上次选的那个，节点备注写「沿用上次的选择：候选 k（seed s）」，不再暂停，也不发「待审核」通知；只要有一个候选是新生成的（比如候选数从 2 改成 3），或者这一组还没人选过，照旧暂停等你选。想换一个：在节点上点「重新选候选」（就是单独重跑这个节点），它会先忘掉这一组的选择，候选都从缓存取，再暂停等你选。选择只在人工审核时记下，存在 `data/wf_cache/_choices/<候选缓存键的哈希>.json`。见 [WORKFLOW.md](WORKFLOW.md)。

## 一个任务做了什么

| 步骤 | 做法 |
|---|---|
| 写分镜 | Bedrock 上的 Opus 5.5（`us.anthropic.claude-opus-5-5`）把主题拆成 5–8 段：标题、旁白、画面描述、音效 |
| 生成插图（可选） | 工作台「AI 插图」选 2 或 4 张时，分镜会给最适合配图的几段写图像提示词和位置（整幅背景、左半或右半）。写完分镜立刻向图像端点提交，和配音、字幕同时进行，所以 GPU 冷启动的约 8 分钟大多被并行掉。绘制代码通过 `T.img[段落id]` 使用图片，并加轻微推近动画。某张图失败只会让那一段不用图，不影响出片 |
| 生成视频镜头（可选） | 工作台「AI 视频镜头」选 1 或 2 段时，分镜会给最需要实拍画面的段落（地点、机器、自然风光；不会是标题卡、图表或流程）写一个英文镜头提示词。写完分镜立刻向视频 GPU（ap-south-1，冷启动约 12.5 分钟）提交，和插图、音乐、配音同时进行。拿到 5 秒的片后：按画面尺寸放大裁切（cover）、去掉原音轨，正放再倒放（ping-pong）循环到这一段的长度，存成 `clip_<段落id>.mp4`，再按 30 fps 抽成 `clip_<段落id>/00001.jpg…`（JPEG q3）。页面框架的 `seek()` 每帧先加载对应的 JPEG（相对 `index.html` 的路径，capture 用 `file://` 打开），设为 `T.clipFrame` 后再调 `draw()`；绘制代码先把它铺满画面，再加一层半透明暗色衬底，文字和简图画在上面。某段失败只会让那一段不用镜头 |
| 生成背景音乐（音乐选「AI 生成」时） | 分镜同时返回一行英文 `music_style`（以 `ambient, no drums, instrumental, no vocals` 结尾），写完分镜立刻向音乐 GPU（us-east-1，冷启动约 9 分钟）提交：歌词只有段落标签，`cot=full`。生成后用 faster-whisper（small）转写，转写出词（单独一个 “You” 不算）就换 seed、`cot=off` 再试一次；还不行或失败，主题是中国古典风格时改用曲库古琴，否则用合成垫乐，并在步骤备注里写明。步骤备注和作品页显示最终用的音乐来源 |
| 合成旁白 | `narrate.py` 用 Polly 逐段合成，每段的帧数由真实语音长度决定。合成前把旁白转成 SSML：`pronounce.json` 里的多音字按拼音读（如 调优 tiáo），英文词按英语读，2–5 个大写字母的缩写逐个字母读（如 TTFT、TPOT）。字幕仍用原文 |
| 写绘制代码 | 每一段单独调用一次 Opus 5.5，最多 4 段并行，各写一个 Canvas 2D 的 `draw()` 函数；服务端把它们装进固定的页面框架 |
| 放置音效 | `sfx.py`，分镜里没有音效就跳过 |
| 混音 | `mix_bgm.py`：音乐在人声出现时自动压低。可选合成垫乐、无音乐、AI 生成，或 `music/library.json` 里的公有领域录音（目前有古琴《秋风词》），录音循环时接缝处交叉淡化 4 秒。音乐文件（曲库或 AI 生成的 4–5 分钟 FLAC）先响度归一到 −24 LUFS，裁到旁白长度，开头淡入 1.5 秒、结尾淡出 3 秒；AI 音乐的音量系数 0.35（音乐单独的片段约比旁白低 12 dB）。步骤备注里是 `mix_bgm.py` 测得的整体和开头 2.5 秒（只有音乐）的 RMS |
| 逐字字幕 | `captions.py`：Whisper 字级时间映射到原文 |
| 逐帧渲染 | `capture_html.py`：Playwright 逐帧调用 `window.seek(frame)` 截图 |
| 合成 | FFmpeg 合成画面、音频，烧入字幕，生成封面图 |

某一段的绘制代码生成失败时，那一段换成标题卡，其余照常出片，并在日志里记一条。

## 接口里的可选字段

`POST /api/jobs` 除了界面上的选项，还接受三个可选字段（界面暂不提供，用脚本提交）：

| 字段 | 作用 |
|---|---|
| `brief` | 写给分镜模型的要求和事实清单，例如段数、顺序、必须使用的事实。会覆盖默认的段数 |
| `scene_context` | 每一段写绘制代码时都会看到的信息，例如地图上各地点的像素坐标 |
| `background_png_b64` | 一张与画面同尺寸的 PNG（base64），画在每一帧的最底层，例如历史地图 |
| `illustrations` | 0–4，最多用几张 AI 插图（默认 0，不调 GPU） |
| `clips` | 0–2，最多用几段 AI 视频镜头（默认 0，不调 GPU） |

`music` 取 `GET /api/music` 返回的键：`synth`、`none`、`ai`（YuE2 按主题生成），以及已下载的曲库曲目（如 `guqin`）。`music` 为 `ai` 时，任务的 `job.json` 里会有 `music_style`、`music_file`（或 `music_fallback`）和 `music_source`。

分镜接口的格式：Opus 返回 `{"scenes": [...], "music_style": "..."}`；旧的纯数组格式仍然接受。

视频页的接口：`GET /api/video/status`，`POST /api/videos`（`prompt`、`aspect`=`16:9`/`9:16`、`seed`），`GET /api/videos`、`GET /api/videos/{id}`、`GET /api/videos/{id}/0.mp4`，存在 `data/videos/`。音乐页的接口：`GET /api/music/status`，`POST /api/musics`（`style`、`lyrics`、`cot`=`full`/`off`、`seed`、可选 `arrangement`），`GET /api/musics`、`GET /api/musics/{id}`、`GET /api/musics/{id}/0.flac`（超过 40 MB 时是 `0.mp3`）和 `score.abc`，存在 `data/music/`。

图像页用的接口：`GET /api/image/status`（端点状态），`POST /api/images`（`prompt`、`ratio`、`mode`=`t2i`/`rgba`、`seed`、`n`），`GET /api/images`、`GET /api/images/{id}`、`GET /api/images/{id}/0.png`。图片存在 `data/images/`。

数字人页的接口：`GET /api/avatar/status`（GPU 状态，另带 `default_model`、`render_s_per_s`、`cold_min`、`usd_per_hour`），`GET /api/avatars/options`（模型、声音、场景、上限），`GET /api/avatars/estimate`（`script` 或 `seconds`、`model`、`size`、`fast`），`POST /api/avatars`（multipart 或 JSON：人像只选一个来源 `portrait` 文件 / `portrait_asset_id` / `image_id`+`image_index` / `ref_video` / `ref_video_asset_id`；声音只选一个 `script`+`voice` / `audio` / `audio_asset_id`；另有 `scene`、`prompt`、`model`（空 = 默认）、`size`=`480`/`720`、`fast`、`seed`、`title`），`GET /api/avatars`、`GET /api/avatars/{id}`、`GET /api/avatars/{id}/video.mp4`（还有 `poster.jpg`、`portrait.png`、`audio.wav`），`DELETE /api/avatars/{id}`。记录存在 `data/avatars/`，也会出现在任务中心。

工作流节点：`tts.polly`（文本 → wav，参数 `voice`）和 `avatar.infinitetalk`（`audio` + `portrait` 或 `ref_video` → 视频，参数 `model`、`prompt`、`size`、`fast`、`seed`，runner `gpu:avatar`）。模板 `wf_product_pitch`「商品口播（数字人）」：商品介绍 → Agent 写 30–60 秒口播稿 → 人工审核 → Polly → 人像（默认 Qwen-Image 生成 9:16，可换成 `input.asset` 用素材库人像）→ 数字人 → 人工审核 → 保存。两个审核都可以在任务中心批准。

工作流的接口：`GET /api/workflows/nodes`（节点声明）、`GET /api/workflows/templates`、`GET/POST /api/workflows`（保存的工作流，`data/workflows/`）、`POST /api/workflows/validate`、`POST /api/workflows/generate`（`prompt`）、`POST /api/workflows/edit`（`workflow`、`instruction`）、`POST /api/wf_runs`（`workflow`）、`GET /api/wf_runs[/{id}]`、`POST /api/wf_runs/{id}/review`（`node_id`、`decision`=`approve`/`reject`、`note`，有候选时批准要带 `choice`）、`POST /api/wf_runs/{id}/retry`（`node_id`）、`GET /api/wf_runs/{id}/file`。运行记录在 `data/wf_runs/`，节点缓存在 `data/wf_cache/`。完整说明见 [WORKFLOW.md](WORKFLOW.md)。

`scripts/run_example.py examples/<名字>` 读取该目录的 `prompt.json` 提交任务，等它完成后把分镜、绘制代码、字幕、成片和日志复制到 `examples/<名字>/output/`。目录里有 `places.json` 时，它会先用 `song_map.py` 生成北宋地图作为背景。

## 示例

**实测：AI 插图 + AI 视频镜头 + AI 音乐（2026-11-04）。** 主题「西湖为什么被称为人间天堂：苏堤、白堤和西湖十景」，约 60 秒、16:9、逐字字幕、插图 1、镜头 1、音乐 AI，三个 GPU 都从 0 冷启动。成片 60.65 秒，总用时 18.5 分钟（1111 秒）。

| 步骤 | 开始（秒） | 用时（秒） | 备注 |
|---|---|---|---|
| 写分镜 | 0 | 21 | 6 段；镜头给了第 2 段「山水与城市相依」，插图给了第 3 段「苏堤」；`music_style`「Gentle traditional Chinese instrumental with guzheng and bamboo flute, slow tempo, serene and elegant mood, ambient, no drums, instrumental, no vocals」 |
| 插图 | 21 | 474 | Spot g7.2xlarge，us-east-1a |
| 视频镜头 | 21 | 704 | Spot g7e.4xlarge，ap-south-1b；06:40:45 开机，06:50:33 拿到片（冷启动约 11.6 分钟） |
| AI 音乐 | 21 | 约 330 | 按需 g5.4xlarge，us-east-1a；第一次就是 100 秒纯音乐，faster-whisper 转写为空 |
| 配音 | 21 | 2 | |
| 写绘制代码 | 23 | 757 | 大部分时间在等视频镜头 |
| 混音、字幕、渲染、合成 | 780 | 331 | 渲染 1820 帧 282 秒 |

三个 GPU 在写完分镜后 1 分钟内同时开机，冷启动全部和写绘制代码重叠；瓶颈是视频的冷启动。

镜头段：航拍西湖，湖面、长堤、石拱桥、山和村落，晨光；正放再倒放循环到 10 秒。上面是半透明暗色圆角衬底，标题「山水与城市相依」和简图（西湖、四周的山、杭州城）在衬底上，读得清楚。30% 和 70% 两个时刻的画面不同（镜头的 JPEG 帧之间平均像素差 6–31）。

混音：AI 音乐音量系数 0.8 时，只有音乐的片段 RMS 约 −28 dB，和旁白（−24 dB）太接近，改成 0.35。0.35 时只有音乐的片段约 −36 dB，旁白说话时约 −23 dB，结尾 3 秒淡出到 −49 dB。

| 示例 | 画幅 | 时长 |
|---|---|---|
| [LLM 推理的 Prefill 与 Decode](../examples/llm-prefill-decode/) | 竖屏 | 100.5 秒 |
| [苏轼一生停留的 8 个地方](../examples/sushi-8-places/) | 横屏，北宋地图背景 | 135.6 秒 |

## 运行

依赖和基础版 skill 相同（完整的静态 ffmpeg、Python venv、Polly 和 Bedrock 的 AWS 权限），另外需要 Node 22 和 venv 里的 `fastapi`、`uvicorn`、`python-multipart`（素材库上传）；跑测试还要 `pytest`、`httpx`。

曲库里的录音不放进仓库，第一次使用前运行 `scripts/fetch_music.sh` 下载到 `~/.cache/explainer-video/music/`。没下载的曲目不会出现在「音乐」选项里。

```bash
scripts/studio.sh start    # 首次会构建前端；打开 http://127.0.0.1:8765
scripts/studio.sh status
scripts/studio.sh stop
```

环境变量（都有默认值）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `PORT` | `8765` | 服务端口 |
| `EVP_TOOLS` | `~/.cache/explainer-video` | ffmpeg、venv 所在目录 |
| `EVP_PYTHON` | `$EVP_TOOLS/venv/bin/python` | 运行 skill 脚本的 Python |
| `EVP_DATA` | `data/` | 任务和作品存放目录（已在 `.gitignore`） |

前端开发：`cd web && npm run dev`，打开 `http://127.0.0.1:5173`，`/api` 会转发到 8765。

## 目录

| 路径 | 内容 |
|---|---|
| `server/app/main.py` | HTTP 接口：`/api/jobs` 的创建、列表、详情、删除，作品文件下载，前端页面 |
| `server/app/pipeline.py` | 按步骤运行一个任务，状态写在 `data/jobs/<id>/job.json` |
| `server/app/llm.py` | 调用 Opus 5.5 写分镜和每一段的绘制代码 |
| `server/app/image.py`、`video.py`、`music.py`、`avatar.py` | 四个 GPU 模块的客户端（`status()`、`generate()`），共用 `gpu_client.py` 和 `gpu_ec2.py` |
| `server/app/avatar_api.py`、`avatar_node.py` | 数字人页的接口和生成记录；`tts.polly`、`avatar.infinitetalk` 两个工作流节点 |
| `server/app/images.py`、`media.py` | 图像页、视频页、音乐页的生成记录 |
| `server/app/workflows/` | 工作流：`nodes.py` 节点库、`validate.py` 校验、`engine.py` 编排器（缓存、审核、重跑）、`agent.py` Agent 生成和改图、`store.py` 保存、`templates/` 模板 |
| `server/tests/` | 校验和缓存键的单元测试（pytest，不调 GPU） |
| `web/` | React + Vite 前端 |
| `scripts/studio.sh` | 启动、停止、查看状态 |
