# explainer_video_platform

用一句话做出讲解视频、图像和音乐。讲解视频是原理讲解、幻灯片式讲解，不做 AI 短剧。

平台分两部分：
- **基础版**：一个 agent skill 加一个本地网页工作室。Opus 5.5 写分镜和绘制代码，代码逐帧画出画面，配上 Polly 旁白、逐字字幕、背景音乐和音效，用 FFmpeg 合成 MP4。
- **生成模块**：图像、视频、音乐三个自部署的开源模型，跑在平台自己启停的 EC2 GPU 上，闲置 15 分钟自动终止。可以单独用，也可以在讲解视频里配插图、实拍镜头和背景音乐。

## 快速开始

```bash
scripts/studio.sh start    # 打开 http://127.0.0.1:8765
```

依赖和用法见 [docs/STUDIO.md](docs/STUDIO.md)。

## 功能

### 讲解视频（工作台）

1. 写一句主题，选时长（30–120 秒）、横屏或竖屏。
2. Opus 5.5 写分镜和每一段的 Canvas 绘制代码，画面逐步搭起图表、箭头、标注。
3. Polly 合成旁白：多音字按拼音读（如调优），英文词按英语读，缩写逐个字母读。每段时长由真实语音长度决定。
4. Whisper 生成逐字高亮字幕。音效按帧放置，背景音乐在人声出现时自动压低。
5. 可选背景：例如北宋地图，地点按经纬度标在正确位置（见 [苏轼示例](examples/sushi-8-places/)）。
6. 可选 AI 素材，和配音同时生成：
   - **AI 插图**（最多 4 张）：分镜挑出适合配图的段落，作为整幅背景或左右半幅。
   - **AI 视频镜头**（最多 2 段）：5 秒实拍感镜头，循环到整段长度，作为背景，上面叠文字和图表。
   - **AI 背景音乐**：按主题写英文风格词生成纯音乐。生成后用 Whisper 检查，有人声就重试，再不行退回古琴或合成垫乐。
7. 音乐也可以选合成垫乐、公有领域古琴录音（《秋风词》）或不要音乐。

### 单独生成

| 页面 | 模型 | 能做什么 |
|---|---|---|
| 图像 | Qwen-Image 2.1 | 1080p 图片，16:9 / 9:16 / 1:1，可选透明背景 PNG |
| AI 视频 | MiniMax H3 | 768p、5 秒、带声音的片子，16:9 或 9:16 |
| 音乐 | YuE2-3B | 歌曲或纯音乐，48 kHz 立体声 FLAC，附乐谱。可写「编排说明」控制乐器和段落，不会被唱出来 |

每个页面顶部显示 GPU 状态：已就绪、启动中，或已缩到 0（提交后自动启动）。

### 云上 GPU

- **EC2 优先**：新的、紧缺的 GPU（g6e、g7、g7e）跑在平台自己启停的 EC2 上，SageMaker 只用于 g5、g4dn 这类容量充足的机型。原因是 SageMaker 在孟买连续 5 次拿不到 g7e。
- 有任务才开机，每个模块最多一台。按价格先试按需、再试 Spot，换可用区重试。Spot 被回收时，任务退回队列。
- 空闲 15 分钟自动关机并终止，开机 4 小时强制关机，不留资源。
- 机器没有 SSH 密钥和入站端口，用最小权限角色 `evp-gpu-worker`。

| 模块 | 区域 | 实测机型 | 从 0 冷启动 | 已开机时 |
|---|---|---|---|---|
| 图像 | us-east-1 | Spot g7.2xlarge | 8.4 分钟 | 一张 71 秒 |
| 视频 | ap-south-1（按 MiniMax H3 许可证的地区限制） | Spot g7e.4xlarge | 12.5 分钟 | 一条约 52 秒 |
| 音乐 | us-east-1 | 按需 g5.2xlarge | 9 分钟 | 4 分钟纯音乐约 3 分钟 |

## 示例

| 示例 | 内容 |
|---|---|
| [examples/llm-prefill-decode/](examples/llm-prefill-decode/) | 竖屏，LLM 推理的 Prefill 与 Decode，100 秒 |
| [examples/sushi-8-places/](examples/sushi-8-places/) | 横屏，苏轼一生停留的 8 个地方，北宋地图背景，古琴配乐 |

## 目录

| 路径 | 内容 |
|---|---|
| [skills/explainer-video-pipeline/](skills/explainer-video-pipeline/) | 基础版 skill：`SKILL.md`、脚本、示例 |
| [server/](server/)、[web/](web/) | 工作室：FastAPI 后端，React 前端 |
| [infra/gpu_ec2/](infra/gpu_ec2/) | EC2 GPU 后端：模块配置、worker、控制脚本、最小权限角色脚本 |
| [infra/image/](infra/image/)、[infra/video/](infra/video/)、[infra/music/](infra/music/) | 三个模块的推理容器；SageMaker 部署脚本作为 g5/g4dn 的备用方案 |
| [infra/budget/](infra/budget/) | AWS 成本告警配置 |
| [docs/STUDIO.md](docs/STUDIO.md) | 工作室的功能、制作步骤、接口和运行方式 |
| [docs/DESIGN.md](docs/DESIGN.md) | 平台总体设计：架构、GPU 后端选择、工作流、对话创作、自我改进、阶段与验收 |
| [docs/IMAGE.md](docs/IMAGE.md)、[docs/VIDEO.md](docs/VIDEO.md)、[docs/MUSIC.md](docs/MUSIC.md) | 三个生成模块的设计和实测数据 |

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 基础版 skill + 工作室（工作台、作品详情、作品集、任务中心） | 已完成 |
| 2 | 云端生成：图像、视频、音乐，自管 EC2 GPU，闲置自动终止；接入工作室 | 已完成 |
| 3 | 工作流编排 + Agent 生成工作流 | 进行中 |
| 4 | 进阶界面：对话创作、工作流编辑器、设置 | 设计中 |
| 5 | 差评驱动的 skill 自动改进（以 PR 形式提交，人工批准） | 设计中 |

## 来源

基础版由 [beyond-text-frontier-model-showcase](https://github.com/liangruibupt/beyond-text-frontier-model-showcase) 里 roadster2 讲解视频的制作过程沉淀而来。

## 许可证

本仓库代码使用 [MIT](LICENSE) 许可证。

平台调用的模型各有自己的许可证，本平台按个人、非商用用途设计：
- 图像模型 Qwen-Image 2.1：Qwen Research License，限制商用。
- 音乐模型 YuE2-3B：CC BY-NC 4.0，限制商用。
- 视频模型 MiniMax H3：MiniMax H3 Community License，授权地区不包括欧盟、英国、韩国和美国。

详见 [设计文档](docs/DESIGN.md) 第 3 节和 [docs/VIDEO.md](docs/VIDEO.md)。MIT 只覆盖本仓库的代码，不改变这些模型的许可条款。
