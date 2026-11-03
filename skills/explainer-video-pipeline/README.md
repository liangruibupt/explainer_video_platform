# explainer-video-pipeline

一个 agent skill：给一个主题，产出一支带旁白、逐字字幕、背景音乐和音效的讲解视频（MP4）。
流程由 roadster2 那次制作沉淀而来（[14-roadster2-remotion](https://github.com/liangruibupt/beyond-text-frontier-model-showcase/tree/master/opus-5.5/14-roadster2-remotion)）。

规则和步骤都在 [SKILL.md](SKILL.md)，这里只说怎么装、有什么。

## 流程

1. Opus 5.5 写文案、旁白和分镜（`scenes.json`）。
2. 模型写代码画每一帧，不直接生成视频：Remotion（React/SVG）、HTML（Canvas 2D / SVG / GSAP / p5.js）或 Manim。也可以选 Seedance 生成镜头。
3. Polly 合成每段旁白，场景时长由真实语音长度决定；音效按帧放置；背景音乐在人声出现时自动压低；Whisper 给出每个字的时间，生成逐字高亮字幕。
4. 无头浏览器逐帧渲染（Remotion 自带；HTML 用 Playwright；Manim 自己渲染）。
5. FFmpeg 合成画面、音频和字幕。

## 默认参数

| 参数 | 默认值 |
|---|---|
| 分镜模型 | `claude-opus-5.5` |
| 画面作者 | `claude-opus-5.5`（写绘制代码） |
| 渲染器 | `remotion`（可选 `html`、`manim`） |
| 配音 | AWS Polly neural，`Zhiyu`，普通话；按 `scripts/pronounce.json` 转 SSML，纠正多音字，英文词和缩写单独标注 |
| 字幕 | Whisper `small`，逐字高亮 |
| 背景音乐 | ffmpeg 合成的无版权垫乐，或用 `--music PATH` 指定一段录音（循环并交叉淡化） |
| 音效 | 开（在 `scenes.json` 里按帧写） |
| 尺寸 | 1920×1080，30 fps |

## 安装

把整个目录复制到 Kiro Crew 的 skills 目录，例如 `~/.kiro/crew/skills/explainer-video-pipeline/`。

依赖（放在稳定的缓存目录，不要放在会被回收的 scratch 目录）：

- 完整的静态 ffmpeg（需要 `aevalsrc`、`anoisesrc`、`tremolo`、`sidechaincompress` 和 libass）。Remotion 自带的 ffmpeg 缺这些滤镜。
- Python venv：`boto3 gTTS playwright faster-whisper pillow numpy`，并固定 `av==16.1.0`（faster-whisper 1.2.x 与 PyAV 19 不兼容），然后 `python -m playwright install chromium-headless-shell`。
- Polly 需要 AWS 凭证。
- Manim（只在 `renderer=manim` 时需要）：在 aarch64 上 pip 装不了，用 micromamba 从 conda-forge 装：`micromamba create -p <cache>/manim-env -c conda-forge python=3.12 manim`。

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/narrate.py` | 逐段 TTS，写出 `durations.json`、`narration.wav`、`audio/sNN.mp3` |
| `scripts/sfx.py` | 按 `scenes.json` 里的帧号合成并放置音效，写出 `sfx.wav` |
| `scripts/mix_bgm.py` | 背景音乐、人声闪避、混入音效，打印响度 |
| `scripts/captions.py` | Whisper 字级时间映射到原文，写出 `captions.ass` 和 `words.json` |
| `scripts/capture_html.py` | Playwright 逐帧调用 `window.seek(frame)` 截图，直接写入 ffmpeg |
| `scripts/manim_scenes.py` | 按旁白时长渲染每个 Manim 场景，补齐到精确帧数后拼接 |
| `scripts/seedance_clips.py` | 每个场景提交一个 Seedance 任务，轮询并下载片段 |
| `scripts/song_map.py` | 生成北宋（约 1100 年）纸本风格地图作为背景，并输出地点的像素坐标 |

## 示例

`examples/rocket/` 是两段的小样（火箭点火、进入轨道），用来验证整条流程：

- `scenes.json`：分镜，含音效事件。
- `index.html`：Canvas 2D 版本，实现 `window.seek(frame)`。
- `manim_scenes.py`：同一分镜的 Manim 版本。
- `html_frames.png`、`html_caption.png`、`manim_frames.png`：从成片里抽的帧。

实测结果：308 帧（165 + 143，由 Polly 旁白长度决定）；HTML 版 11 秒截完；成片 10.27 秒，音轨 10.28 秒；每个音效在指定帧上响起；字幕逐字高亮与语音对齐。

## 已知限制

- Manim 的 `MathTex` / `Tex` 需要 LaTeX 和 `dvisvgm`。aarch64 上 conda-forge 的 `texlive-core` 缺 `latex.fmt`，公式渲染会失败。没有 root 时用 `Text()` 写 Unicode 公式；有 root 时装 `texlive-latex-extra dvisvgm`。
- `seedance_clips.py` 按 BytePlus 官方文档的接口写成，但还没有用真实 `ARK_API_KEY` 生成过片段。
- GSAP 和 p5.js 的接法写在 SKILL.md 里，只实测了 Canvas 2D。
- Remotion 对 4 人及以上的公司需要付费授权；`html` 和 `manim` 渲染器没有这个限制。
