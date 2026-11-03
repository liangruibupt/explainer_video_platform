# explainer_video_platform

用一句话、一段对话或一张工作流图，做出讲解视频、图像和音乐。

当前包含 **基础版**：一个 agent skill，Opus 5.5 写分镜和绘制代码，代码逐帧画出画面，配上 Polly 旁白、逐字字幕、背景音乐和音效，最后用 FFmpeg 合成 MP4。

**进阶版** 还在设计阶段：自部署的生图、生视频、生音乐模型（云上 GPU，闲置缩容到 0），工作流编排，Agent 自动生成工作流，对话创作，以及根据差评自动改进 skill。设计见 [docs/DESIGN.md](docs/DESIGN.md)。

## 目录

| 路径 | 内容 |
|---|---|
| [skills/explainer-video-pipeline/](skills/explainer-video-pipeline/) | 基础版 skill：`SKILL.md`、7 个脚本、示例 |
| [docs/DESIGN.md](docs/DESIGN.md) | 进阶版设计：架构、模型选型、缩容到 0、工作流、对话创作、自我改进、阶段与验收标准 |

## 基础版能做什么

1. Opus 5.5 写文案、旁白和分镜。
2. 模型写代码画每一帧：Remotion（React/SVG）、HTML（Canvas 2D / SVG / GSAP / p5.js）或 Manim。也可以选 Seedance 生成镜头。
3. Polly 合成旁白，场景时长由真实语音长度决定；音效按帧放置；背景音乐在人声出现时自动压低；Whisper 生成逐字高亮字幕。
4. 无头浏览器逐帧渲染。
5. FFmpeg 合成画面、音频和字幕。

安装和用法见 [skill 的 README](skills/explainer-video-pipeline/README.md)。

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 基础版 skill | 已完成 |
| 2 | 云端生成：图像、视频、音乐的 SageMaker 异步端点，缩容到 0 | 设计中 |
| 3 | 工作流编排 + Agent 生成工作流 | 设计中 |
| 4 | 界面：工作台、对话创作、工作流编辑器、作品集、设置 | 设计中 |
| 5 | 差评驱动的 skill 自动改进（以 PR 形式提交，人工批准） | 设计中 |

## 来源

基础版由 [beyond-text-frontier-model-showcase](https://github.com/liangruibupt/beyond-text-frontier-model-showcase) 里 roadster2 讲解视频的制作过程沉淀而来。

## 许可证

本仓库的代码许可证尚未选定（暂无 LICENSE 文件）。平台调用的模型各有自己的许可证，其中几个限制商用，详见 [设计文档](docs/DESIGN.md) 第 3 节。
