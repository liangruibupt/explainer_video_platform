# 音乐模块设计（阶段 2）

音乐模块照搬图像模块已经跑通的「SageMaker 异步端点 + 缩容到 0」链路（见 [IMAGE.md](IMAGE.md)），换成 YuE2-3B 歌曲生成模型。总体方案见 [DESIGN.md](DESIGN.md) 第 3、4 节。

## 1. 在讲解视频里用来做什么

主要用途是**讲解视频的背景音乐**：一段没有人声的纯音乐垫在旁白下面，风格跟着题材走（古典题材用古琴、箫，科技题材用电子乐）。所以本模块最关心的问题是：YuE2 这个歌曲模型能不能稳定地出**纯音乐**。

次要用途：片头片尾的一小段带词歌曲。

接入工作室的方式（后续工作）：分镜整体带一个可选的 `music` 字段（风格、时长、是否纯音乐），生成的音频按旁白总长裁剪、淡入淡出，以约 -20 dB 的音量混在旁白下面。

## 2. 模型和许可

- [m-a-p/YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B)，固定 revision `8f24312f187763b9854adeec87e437e2b03bbff1`（7.3 GB，单个 `model.safetensors`）。
- VAE：[m-a-p/YuE2-Vae](https://huggingface.co/m-a-p/YuE2-Vae)（默认 VAE），revision `152733a19ad43aa67e367f9b5503ef8075bb5126`（0.5 GB）。
- 许可：CC BY-NC 4.0。个人非商用可以使用，没有地区限制，所以放在 us-east-1。
- 推理：模型仓库里的官方 wheel `yue2_infer-0.1.5`，用它的 HF/PyTorch 路径（不用 vLLM）。它固定了依赖版本：torch 2.10.0、transformers 4.57.6、huggingface-hub 0.36.2、safetensors 0.7.0、tiktoken 0.12.0、numpy 2.2.6、soundfile 0.13.1、accelerate 1.13.0。
- 输入是风格提示词（style）和带段落标签的歌词（lyrics），输出 48 kHz 立体声。`cot` 决定是否先写乐谱：`full`（旋律加和弦，默认）、`melody`（只写旋律）、`off`（不写乐谱直接生成）。
- 模型卡给的资源：一次一首，24 GB 显卡，24 GB 内存，显存峰值 11–14 GiB；RTX 4090 上 3.6 分钟的歌 71 秒生成完。

## 3. 机型

条件：GPU 显存 ≥ 22 GiB，主机内存 ≥ 30 GiB。`infra/music/deploy.py plan` 部署时实时查询显存、内存和 us-east-1 按需价格，按价格从低到高取最便宜的 5 种写进 instance pools（SageMaker 最多允许 5 个）。2026-11-04 的结果：

| 优先级 | 机型 | GPU | 显存 | 内存 | $/小时 |
|---|---|---|---|---|---|
| 1 | ml.g6.2xlarge | L4 | 22 GiB | 32 GiB | 1.222 |
| 2 | ml.g5.2xlarge | A10G | 22 GiB | 32 GiB | 1.515 |
| 3 | ml.g6.4xlarge | L4 | 22 GiB | 64 GiB | 1.654 |
| 4 | ml.g5.4xlarge | A10G | 22 GiB | 64 GiB | 2.030 |
| 5 | ml.g6e.xlarge | L40S | 45 GiB | 32 GiB | 2.605 |
| 不进池 | ml.g6e.2xlarge | L40S | 45 GiB | 64 GiB | 2.803 |

最初只放了 g6.2xlarge、g5.2xlarge、g6e.2xlarge 三种，连续两次部署都因为三种都没有容量（`InsufficientInstanceCapacity`）失败。于是按同样的显存和内存条件把 g6.4xlarge、g5.4xlarge、g6e.xlarge 也加进候选，第三次部署分到了 ml.g5.4xlarge。

所有候选卡都能直接跑 BF16，只建一个 SageMaker Model。端点 AMI 和图像模块一样用 `al2023-ami-sagemaker-inference-gpu-4-1`。本账号这几种机型的端点配额都是 2 个实例。

## 4. 推理容器

`infra/music/container/`：
- 基础镜像 `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04`，Python 3.11 venv，cu128 版 torch 2.10.0，加上 wheel 固定的依赖和 wheel 本身。另装 `ffmpeg`，音频超大时转 MP3 用。
- 镜像不放权重。权重在 S3 上分成 `model/` 和 `vae/` 两个目录，通过 `ModelDataSource`（`S3Prefix`、不压缩）下载到 `/opt/ml/model/model` 和 `/opt/ml/model/vae`。
- `serve.py`（FastAPI）：启动时用 `YuE2Pipeline.from_pretrained(model=..., vae=..., local_files_only=True, device="cuda")` 加载并把主模型放上 GPU，加载完成后 `/ping` 才返回 200。
- 权重在 S3 的位置：`s3://evp-models-747411437379-us-east-1/weights/yue2-3b/8f24312f187763b9854adeec87e437e2b03bbff1/`，19 个文件共 7,794,623,684 字节，清单在同目录的 `_evp_manifest.json`。上传脚本是 `infra/music/stage_weights.py`。
- 镜像：ECR `evp/yue2`。

请求（编排器写到 S3 的 JSON）：

```json
{
  "style": "舒缓的中国古典器乐，古琴与箫，慢速，空灵，instrumental, no vocals",
  "lyrics": "[Intro]\n\n[Instrumental]\n\n[Outro]",
  "cot": "full",
  "seed": 7,
  "cfg_scale": 1.2
}
```

`cot` 默认 `full`，`cfg_scale` 可不填。纯音乐的写法：歌词里只放段落标签不放字，风格用英文写并以 `instrumental, no vocals` 结尾（中文风格词实测会出人声，见第 7 节）。

响应：

```json
{"audio_b64": "...", "format": "flac", "seconds": 95.3, "sample_rate": 48000, "channels": 2,
 "abc": "乐谱文本（cot=off 时为 null）", "truncated": {"abc": false, "semantic": false},
 "timing": {"abc": {...}, "semantic": {...}, "nar_seconds": 0, "vae_seconds": 0, "e2e_seconds": 0},
 "gpu": "NVIDIA L4"}
```

音频是 16-bit FLAC。4 分钟立体声约 25–35 MB，base64 后仍在异步推理输出上限内；如果 FLAC 超过 40 MB，就转成 320 kbit/s MP3，`format` 会写 `mp3`。

## 5. 端点和缩容到 0

和图像模块相同：

| 配置 | 值 |
|---|---|
| 推理方式 | 异步推理，端点名 `evp-music` |
| `MaxConcurrentInvocationsPerInstance` | 1（一次一首） |
| 伸缩范围 | 最小 0，最大 1 |
| 扩容 | `HasBacklogWithoutCapacity` 告警，步进策略加 1 台 |
| 缩容 | `ApproximateBacklogSizePerInstance` 目标跟踪，目标 1，缩容冷却 900 秒 |
| 结果 | 轮询 S3 `jobs/music/out/`，失败在 `jobs/music/fail/` |
| 标签 | `project=explainer-video`、`module=music` |

**执行角色和图像模块共用 `evp-image-endpoint`。** 原因是我们已经有把这个角色传给 SageMaker 的 PassRole 权限，新建角色要用户再授权一次。`deploy.py up` 在这个角色上另加一条内联策略 `evp-music`，只允许拉取 ECR `evp/yue2` 镜像和写 `/aws/sagemaker/Endpoints/evp-music*` 日志；S3 桶的读权限沿用图像模块的 `evp-image` 策略。删除图像模块时不要删这个角色。

`infra/music/deploy.py` 子命令：`plan`、`build`、`up`、`status`、`smoke`、`down`，用法和图像模块一致。`down` 删除端点、配置、模型、伸缩策略和告警，保留 S3 权重和 ECR 镜像。

## 6. 成本估算

- 一次使用（冷启动 + 一两首 + 闲置约 16 分钟回到 0）约 25–30 分钟。分到 g6.2xlarge 约 $0.6，分到这次的 g5.4xlarge 约 $1.0。
- 闲置：S3 权重 7.8 GB 约 $0.18/月，ECR 镜像 7.3 GB 约 $0.7/月。

## 7. 实测（2026-11-04，us-east-1）

镜像 `evp/yue2:20261104-013851-amd64`，7.33 GB（ECR 压缩后大小），Docker v2 清单。

| 项目 | 实测 | 说明 |
|---|---|---|
| 首次部署到 InService | 9.2 分钟 | 第三次部署（前两次没有容量，见第 3 节），分到 ml.g5.4xlarge（A10G 22 GB） |
| 模型加载 | 11 秒 | 从 `/opt/ml/model` 加载到 GPU，之后 `/ping` 返回 200 |
| 从 0 冷启动到拿到结果 | **8.2 分钟**（494 秒） | 其中排队等实例 447 秒，生成 41 秒 |
| 最后一个请求到实例回到 0 | 16.2 分钟和 17.6 分钟（两次） | 15 分钟冷却加告警判定时间 |
| 本次测试 GPU 花费 | 约 66 分钟 × $2.03/小时 ≈ **$2.2** | 两段：首次部署加 4 个请求再闲置到 0 约 42 分钟（02:52–03:34），冷启动那次约 24 分钟（03:41–04:05）；两次没有容量的部署不计费 |

每个请求的结果（A10G，48 kHz 立体声 FLAC，ffprobe 测得的时长与响应里的 `seconds` 一致）：

| 用例 | cot | 时长 | 乐谱 / 语义 / 声学 / VAE | 端到端 | faster-whisper（small）转写 | 有没有人声 |
|---|---|---|---|---|---|---|
| `instrumental`：中文风格「舒缓的中国古典器乐，古琴与箫……instrumental, no vocals」，歌词只有 `[Intro] [Instrumental] [Outro]` | full | 249.6 秒 | 24 / 83 / 49 / 9 秒 | 164 秒 | 14 段中文，例如「情深离境参参长」「你眼中有闪光阴晴」「终身不应缺席」 | **有**：模型自己编了词并唱出来；乐谱里写出了 intro/verse/chorus 结构，Vocal 声部有旋律 |
| `instrumental_en`：同样的歌词，英文风格「Chinese classical instrumental, guqin and xiao flute, slow, ethereal, ambient, no drums, instrumental, no vocals」 | full | 258.8 秒 | 20 / 85 / 53 / 6 秒 | 165 秒 | 没有转写出任何内容 | 转写为空，按这个代理指标判断**没有人声**（尚未人耳确认） |
| `instrumental_off`：中文风格，同样歌词 | off | 303.0 秒 | — / 111 / 59 / 7 秒 | 177 秒 | 只有一个 “You”（whisper 在纯音乐上常见的误识别） | 基本**没有人声** |
| `song_zh`：华语流行，两段各两句的中文歌词 | full | 71.3 秒 | 7 / 22 / 11 / 3 秒 | 44 秒 | 「愛闖開 昨天輕輕吹開 / 腳不走向遠方」 | 有，转写能对上原词的一部分（「昨天轻轻吹开」「走向远方」） |
| `song_zh_cold`：同上，seed 12，从 0 冷启动 | full | 56.7 秒 | — | 41 秒 | 「昨天清清吹一根腳步」 | 有 |

结论：
- **纯音乐不能只靠歌词里不写字。** 用中文风格词加 `cot=full` 时，模型把空的段落当成要填词的歌，编了词唱出来。英文风格词（多写 ambient、no drums）或 `cot=off` 时，转写没有可识别的词。工作室生成背景音乐时先用英文风格词 + `instrumental, no vocals`，生成后用 faster-whisper 检查，转写出词就换 seed 重试。
- **时长不好控制。** 只有三个段落标签的纯音乐出来 4–5 分钟；两段短歌词的歌 57–71 秒。背景音乐需要按旁白长度裁剪并淡出。
- 生成速度：A10G 上约 0.65 倍实时（4 分钟音乐约 2.7 分钟），1 分钟的歌约 40 秒。
- 4 分钟立体声 16-bit FLAC 约 23 MB，5 分钟约 29 MB，都在 40 MB 以下，没有触发 MP3 转码。

样例在 `data/music_smoke/`（不进 git）：`instrumental.flac`、`instrumental_en.flac`、`instrumental_off.flac`、`song_zh.flac`、`song_zh_cold.flac`，每个旁边有对应的 `.abc` 乐谱（`cot=off` 除外）。转写检查脚本 `infra/music/check_audio.py`。

## 8. 验收

## 7a. EC2 部署（2026-11-04 起，当前方案）

**SageMaker 端点 `evp-music` 已下线**（`infra/music/deploy.py down`，S3 权重和 ECR 镜像保留）。音乐模块现在跑在平台自己启停的 EC2 GPU 实例上：控制器 `server/app/gpu_ec2.py`，配置 `infra/gpu_ec2/modules/music.json`，工作室客户端 `server/app/music.py`（`status()`，`generate(style, lyrics, cot, seed)` 返回 `{"flac", "seconds", "sample_rate", "abc"}`）。`infra/music/deploy.py` 保留，作为 g5/g4dn 这类老 GPU 的后备方案。

- 区域 us-east-1，队列 `s3://evp-models-747411437379-us-east-1/jobs/music-ec2/`（和 SageMaker 的 `jobs/music/` 分开）。
- 镜像 `evp/yue2:20261104-013851-amd64`，权重 7.8 GB（`model/` 和 `vae/`）复制到本地 NVMe，只读挂到 `/opt/ml/model`。
- 容量顺序：按需 g6.2xlarge → g5.2xlarge → g6.4xlarge → g5.4xlarge → g6e.xlarge（每种最多 3 个可用区），然后同样的顺序试 Spot。拿到一台就停。
- 空闲 15 分钟自己关机并终止，4 小时硬上限。

实测（`instrumental_en` 那组：英文风格词「Chinese classical instrumental, guqin and xiao flute, slow, ethereal, ambient, no drums, instrumental, no vocals」，歌词只有段落标签，`cot=full`，seed 7）：

| 项目 | 实测 | 说明 |
|---|---|---|
| 拿到实例的选项 | **按需 g5.2xlarge**，us-east-1a | 按需 g6.2xlarge 三个可用区都没有容量 |
| 依次尝试到拿到实例 | 36 秒 | 第 4 项 |
| 实例开机到 bootstrap 开始 | 约 54 秒 | |
| 拉镜像（7.3 GB） | 151 秒 | 和复制权重同时进行 |
| 复制权重（7.8 GB） | 44 秒 | |
| 模型加载 | 20 秒 | 容器启动到 `/ping` 200 |
| 生成 258.8 秒的音乐 | 168 秒（乐谱 21 / 语义 85 / NAR 53 / VAE 10 秒） | A10G 24 GB |
| **从 0 冷启动到拿到结果** | **9.0 分钟**（538 秒） | SageMaker 是 494 秒 |
| 热实例上再生成一次（同样参数，走工作室客户端） | 166.5 秒生成，**171.8 秒端到端** | 同 seed，输出一样长 |
| 最后一首到实例自己关机 | **15.0 分钟**（空闲 900 秒） | 04:43:01 最后一首完成，04:58:02 自己关机并终止 |
| 一次使用（冷启动 + 一两首 + 闲置 15 分钟） | 约 32 分钟 × $1.21/小时 ≈ **$0.65** | |

结果：FLAC，258.8 秒，48 kHz，双声道，23 MB，带 ABC 乐谱。`infra/music/check_audio.py` 的 faster-whisper 转写为空，按这个代理指标判断没有人声（和第 7 节 SageMaker 上的结果一致，尚未人耳确认）。

## 8. 验收

1. ✅ 从 0 实例发出请求，冷启动 494 秒（第 7 节）。
2. ⚠️ 纯音乐：中文风格词 + 只有段落标签的歌词仍然出了人声；英文风格词或 `cot=off` 转写为空。还需要人耳听一遍确认。
3. ✅ 一首 30–60 秒、两段主歌的中文歌（71 秒和 57 秒两版）。
4. ✅ 闲置后实例数回到 0，两次分别用时 16.2 和 17.6 分钟；测试结束时端点保留在 0 台。

## 9. 待定问题

- 背景音乐默认用哪种写法：英文风格词 + `cot=full`（有乐谱，可编辑）还是 `cot=off`（更快一点出 5 分钟，但没有乐谱）。两种都需要人耳确认没有哼唱。
- 时长控制：YuE2 没有时长参数，只有三个段落标签也会出 4–5 分钟。可以试写更少的段落，或者直接生成后按旁白长度裁剪加淡出（后者最简单）。
- 是否加「转写出词就换 seed 重试」的自动检查，还是加人声分离做兜底。
- 本次连续两次部署时 g6.2xlarge、g5.2xlarge、g6e.2xlarge 都没有容量，最后用了第 4 优先的 g5.4xlarge（$2.03/小时）。如果经常这样，每次使用的花费按 g5.4xlarge 估算。
