# 讲解视频工作室（基础版界面）

本地网页界面，调用 [基础版 skill](../skills/explainer-video-pipeline/) 制作原理讲解、幻灯片式讲解视频。单用户，只监听 `127.0.0.1`。

## 能做什么

- **工作台**：写一句主题，选时长（约 30–120 秒）、横屏或竖屏、背景音乐、是否加逐字字幕，点「开始制作」。
- **作品详情**：实时显示 8 个制作步骤和分镜，完成后播放和下载 MP4；可以用同一主题重做、查看日志、删除。
- **作品集**：已完成的作品。
- **任务中心**：所有任务，包括正在制作和失败的。
- **图像**：Qwen-Image 2.1 生成 1080p 图片（16:9、9:16、1:1，可选透明背景）。页面顶部显示 GPU 状态：已就绪、启动中、已缩到 0。设计和实测见 [IMAGE.md](IMAGE.md)。
- **进阶（规划中）**：AI 视频、音乐、工作流三个入口，只显示设计说明，还没有功能。

## 一个任务做了什么

| 步骤 | 做法 |
|---|---|
| 写分镜 | Bedrock 上的 Opus 5.5（`us.anthropic.claude-opus-5-5`）把主题拆成 5–8 段：标题、旁白、画面描述、音效 |
| 生成插图（可选） | 工作台「AI 插图」选 2 或 4 张时，分镜会给最适合配图的几段写图像提示词和位置（整幅背景、左半或右半）。写完分镜立刻向图像端点提交，和配音、字幕同时进行，所以 GPU 冷启动的约 8 分钟大多被并行掉。绘制代码通过 `T.img[段落id]` 使用图片，并加轻微推近动画。某张图失败只会让那一段不用图，不影响出片 |
| 合成旁白 | `narrate.py` 用 Polly 逐段合成，每段的帧数由真实语音长度决定。合成前把旁白转成 SSML：`pronounce.json` 里的多音字按拼音读（如 调优 tiáo），英文词按英语读，2–5 个大写字母的缩写逐个字母读（如 TTFT、TPOT）。字幕仍用原文 |
| 写绘制代码 | 每一段单独调用一次 Opus 5.5，最多 4 段并行，各写一个 Canvas 2D 的 `draw()` 函数；服务端把它们装进固定的页面框架 |
| 放置音效 | `sfx.py`，分镜里没有音效就跳过 |
| 混音 | `mix_bgm.py`：音乐在人声出现时自动压低。可选合成垫乐、无音乐，或 `music/library.json` 里的公有领域录音（目前有古琴《秋风词》），录音循环时接缝处交叉淡化 4 秒 |
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

图像页用的接口：`GET /api/image/status`（端点状态），`POST /api/images`（`prompt`、`ratio`、`mode`=`t2i`/`rgba`、`seed`、`n`），`GET /api/images`、`GET /api/images/{id}`、`GET /api/images/{id}/0.png`。图片存在 `data/images/`。

`scripts/run_example.py examples/<名字>` 读取该目录的 `prompt.json` 提交任务，等它完成后把分镜、绘制代码、字幕、成片和日志复制到 `examples/<名字>/output/`。目录里有 `places.json` 时，它会先用 `song_map.py` 生成北宋地图作为背景。

## 示例

| 示例 | 画幅 | 时长 |
|---|---|---|
| [LLM 推理的 Prefill 与 Decode](../examples/llm-prefill-decode/) | 竖屏 | 100.5 秒 |
| [苏轼一生停留的 8 个地方](../examples/sushi-8-places/) | 横屏，北宋地图背景 | 135.6 秒 |

## 运行

依赖和基础版 skill 相同（完整的静态 ffmpeg、Python venv、Polly 和 Bedrock 的 AWS 权限），另外需要 Node 22 和 venv 里的 `fastapi`、`uvicorn`。

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
| `web/` | React + Vite 前端 |
| `scripts/studio.sh` | 启动、停止、查看状态 |
