# 数字人模块（infra + 模型对比）

用途：电商口播、商超口播、直播间预录片段。输入一张人像（照片、AI 生成图，或一段参考视频）和一段口播音频，输出口型对齐、带头部和上半身动作的视频。接口约定见 [AVATAR_CONTRACT.md](AVATAR_CONTRACT.md)，本文写部署、实测和选型。

**版权提醒**：只能用有权使用的人像：本人照片、已同意的他人照片，或 AI 生成的虚构人物。页面在上传处显示这句提示。**按设计不加水印**，也不加「AI 生成」标记（用户决定）。

## 1. 设计

- 一个 GPU 模块 `avatar`（`infra/gpu_ec2/modules/avatar.json`），us-east-1，走现有的 EC2 自管 GPU 后端（`server/app/gpu_ec2.py` + `infra/gpu_ec2/worker/`），和视频、图像、音乐模块同一套：提交任务时拉起实例，空闲 900 秒自己终止，开机 4 小时硬上限。
- **一个镜像两个模型**：`evp/avatar`，HTTP 适配层在 8080（`/ping`、`/invocations`），请求里的 `model` 选 `infinitetalk` 或 `longcat`。模型按需加载并常驻；换模型时先释放前一个（两个都是 14B 级，一张卡只放一个）。
- **权重不进镜像**：bootstrap 只拷一个很小的 `index.json`（`jobs/avatar-ec2/weights-index/`），适配层第一次用到某个模型时，用 s5cmd 从固定 sha 的 S3 前缀拷到实例 NVMe。实测 InfiniteTalk 93 GB 用 55 秒，LongCat 77 GB 用 39 秒（g7e.4xlarge，50 Gbps）。容器启动时还会先预取默认模型。
- 通用 worker（`infra/gpu_ec2/worker/**`）**没有改动**。权重挂载用的是模块配置里的 `docker_args`（`-v /opt/dlami/nvme/avatar-weights:/weights`）。

| 文件 | 作用 |
|---|---|
| `infra/avatar/container/` | Dockerfile、适配层 `serve.py`、`it_runner.py`、`lc_runner.py`、`common.py`、构建时导入检查 `check_imports.py` |
| `infra/avatar/stage_weights.py` | HF → S3，每个仓库固定 commit sha |
| `infra/avatar/builder.py` + `builder.sh` | 临时构建机（本机只剩约 17 GB 磁盘，不在本机下权重或构建镜像） |
| `infra/avatar/weights_index.json` | 两个模型的 S3 前缀 |
| `infra/avatar/bench/` | 测试稿 `scripts.json`、Polly 合成 `make_audio.py`、批量跑 `run_bench.py`、指标 `analyze.py`、对比图 `contact_sheet.py`、逐秒身份/色差 `trace.py`、分段接缝帧 `seams.py` |
| `infra/avatar/iam/` | 本次加的两条内联策略 |

## 2. 部署步骤

1. 构建机：`python3 infra/avatar/builder.py launch [--only image]`。c7i.4xlarge + 400 GB gp3，实例配置文件 `evp-video-builder`，关机即终止，脚本里另设硬上限关机（默认 150 分钟）。它并行做两件事：`stage_weights.py` 把权重拷进 S3；`docker build` + push `evp/avatar:<tag>`。结果写到 `s3://evp-models-747411437379-us-east-1/jobs/avatar-build/<run>/status.json`，用 `builder.py status <run>` 查看。只拷权重约 12 分钟，只构建镜像约 13 分钟。
2. 把 `avatar.json` 的 `image` 改成新 tag。
3. 提交任务（工作室或 `infra/avatar/bench/run_bench.py`），实例自动拉起。

### 权重（固定 sha）

| 模型 | S3 前缀 | 内容 |
|---|---|---|
| InfiniteTalk | `weights/infinitetalk/d59847ebdacf19245bfca3fb23311c0cada8378a/`（29 个文件，92.9 GB） | Wan-AI/Wan2.1-I2V-14B-480P@6b73f84e、TencentGameMate/chinese-wav2vec2-base@5f7a6cfd（refs/pr/1，带 model.safetensors）、MeiGen-AI/InfiniteTalk@d59847eb `single/`、Kijai/WanVideo_comfy@8260d429 `Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors` |
| LongCat-Video-Avatar-1.5 | `weights/longcat-avatar-1.5/92016c71d5d318d0f5d84e4db30015a571484ab6/`（55 个文件，76.5 GB） | meituan-longcat/LongCat-Video@03b55529（只要 tokenizer / text_encoder / vae / scheduler，底座 DiT 不用）、meituan-longcat/LongCat-Video-Avatar-1.5@92016c71（base_model、base_model_int8、dmd LoRA、whisper-large-v3 只留 model.safetensors、vocal_separator） |

每个前缀下有 `_evp_manifest.json`（仓库、revision、文件和字节数），上传后逐个核对过大小。InfiniteTalk 的 `quant_models/` 没拷：bf16 在 96 GB 卡上放得下。

代码：InfiniteTalk@50aa0a94，LongCat-Video@6b3f4b85（Dockerfile 里的 `IT_SHA` / `LC_SHA`）。

### 镜像

`747411437379.dkr.ecr.us-east-1.amazonaws.com/evp/avatar:20261105-014747`：CUDA 12.8.1、Ubuntu 22.04、Python 3.10（InfiniteTalk 用了 3.11 删掉的 `inspect.ArgSpec`）、torch 2.7.1+cu128、xformers 0.0.31.post1、flash-attn 2.8.0.post2（xformers 0.0.31 只接受 ≤2.8.0）、xfuser 0.4.4 + yunchang 0.6.3.post1、pillow 12.3.0，其余版本全部固定在 `requirements.txt`。

pillow 从 11.2.1 升到 12.3.0（修掉 12.3.0 之前的漏洞）。没升到 13.0.0：它要求 Python ≥ 3.11，而这个镜像只能用 3.10。worker 只收工作室重新编码过的 PNG（工作室用 pillow 13.0.0 解码），剩下的风险可以接受。上一个镜像是 `20261104-190851`（pillow 11.2.1）。

`20261105-014747` 的验证（2026-11-05，g7e.4xlarge Spot，`portrait_16x9.jpg` 经 `avatar.portrait_png`，Polly Zhiyu `pitch15` 14.96 秒，480 快速，seed 42）：镜像里 `PIL.__version__` = 12.3.0，构建时 `check_imports.py` 通过；InfiniteTalk 896×448、14.92 秒、373 帧，worker 用时 1020 秒（含冷启动加载模型 526 秒，生成约 494 秒），峰值显存 64.0 GB；LongCat 832×480、14.96 秒、374 帧，渲染 448 秒（加载 14 秒），峰值显存 66.9 GB。两条都带完整音轨，中间帧和第 5 节的样片是同一个人、同一场景，没有花屏。

构建和上线过程中修过的问题：
- xformers 0.0.31 对 compute capability ≥ 9.0 都启用它自带的 FlashAttention-3（Hopper 内核），在 Blackwell sm_120 上报 `flash_fwd_launch_template.h: invalid argument`。适配层在 sm ≥ 10 上关掉 FA3（`dispatch._set_use_fa3(False)`）。
- `wan` 包导入时就会初始化 CUDA，CPU 构建机上查不了；构建时把这类模块标成 SKIP，容器启动时在 GPU 上再完整检查一遍，失败就退出（worker 随即关机）。
- g7e.2xlarge 只有 64 GB 内存，InfiniteTalk 的加载器在 CPU 上合并约 66 GB 的 Wan2.1 DiT 分片，容器被 OOM 杀掉。所以容量列表里**不放 g7e.2xlarge**。

## 3. 机型和成本

容量顺序（`avatar.json`）：g7e.4xlarge Spot（1a、1b）→ g7e.4xlarge 按需（1a、1b）→ g7e.8xlarge Spot（1a）。

| 机型 | GPU | 内存 | 2026-11-04 价格（us-east-1） | 结论 |
|---|---|---|---|---|
| g7e.2xlarge | RTX PRO 6000 96 GB | 64 GB | Spot ~$1.38–2.65/h | InfiniteTalk 加载时内存不够，不用 |
| **g7e.4xlarge** | RTX PRO 6000 96 GB | 128 GB | Spot $1.38/h（1a），按需 $4.00/h | **首选**，两个模型实测都能跑 |
| g7e.8xlarge | 同上 | 256 GB | Spot $1.91/h（1a） | 兜底 |
| g6e.2x/4xlarge | L40S 48 GB | 64/128 GB | ~$2.2/h Spot | 没测；InfiniteTalk 要把 T5 和 DiT 放到 CPU（代码已按 <60 GB 自动切换），LongCat 自动用 INT8 DiT，速度未知 |

冷启动（从提交到开始生成，g7e.4xlarge Spot）：实例启动约 1 分钟，镜像拉取 115–120 秒，适配层就绪约 200 秒；然后拷权重 40–55 秒，再加载模型：InfiniteTalk 合并 LoRA 约 8.7 分钟（`load_s` 520 秒），LongCat 约 14 秒。**冷启动实测约 13 分钟（InfiniteTalk）/ 约 5 分钟（LongCat，19:21 提交、19:26 开始生成）**；之后同一模型常驻，不再付加载时间。工作室在用户确认脚本时就可以先提交任务唤醒实例。

## 4. 测试输入

- 人像：图像模块（Qwen-Image 2.1）生成的虚构年轻女导购，明亮超市货架通道，半身，正对镜头。16:9 和 9:16 各一张（`examples/avatar/portrait_16x9.jpg`、`portrait_9x16.jpg`）。
- 口播：Amazon Polly neural Zhiyu，`infra/avatar/bench/scripts.json`：有机牛奶买二送一 14.3 秒、超市生鲜节 48.6 秒、直播间爆款组合 88.8 秒。

## 5. 实测结果（g7e.4xlarge，2026-11-04）

| 运行 | 模型 | 设置 | 音频 | 渲染（暖） | 秒/输出秒 | 峰值显存 | $/输出分钟（Spot $1.38/h） | 口型相关 / 延迟 | 身份 余弦 首→尾（最低） | 色差 ΔE 首→尾 |
|---|---|---|---|---|---|---|---|---|---|---|
| infinitetalk_15s_480_fast | InfiniteTalk | 480（896×448），lightx2v 4 步，shift 2，cfg 1/2 | 14.3 s | 420 s（另加载 520 s） | **29.5** | 64.1 GB | **$0.68** | 0.16 / +80 ms | 0.95 → 0.79（0.68） | 0.03 |
| longcat_15s_480_fast | LongCat-Avatar-1.5 | 480（832×480），distill 8 步，bf16 DiT，ai2v + 4 次续写 | 14.3 s | 441 s（另拷权重 47 s、加载 16 s） | **30.9** | 66.4 GB | **$0.71** | 0.20 / +240 ms | 0.94 → 0.60（0.43） | 0.58 |
| A：infinitetalk_promo45_480_fast（集成验证，数字人页 API） | InfiniteTalk | 同第一行，场景「超市货架口播」 | 49.3 s | 1317 s（另加载 519 s） | **26.7** | 64.0 GB | **$0.61** | — | 0.96 → 0.87（0.52，第 48 秒） | 0.05（最大 0.07） |
| B：infinitetalk_live90_480_fast（集成验证，数字人页 API） | InfiniteTalk | 同上，场景「直播间带货」，暖机 | 89.5 s | 2319 s | **25.9** | 64.0 GB | **$0.60** | — | 0.96 → 0.56 末段均值（0.44，第 81 秒） | 0.06（最大 0.07） |

说明：
- InfiniteTalk 那条已检查起、中、末三帧（`examples/avatar/infinitetalk_15s_480_fast_frames.jpg`）：人物、服装、货架背景都保持住，嘴型随台词开合，有自然的头部和手部动作，没有明显偏色（ΔE 0.03）。身份余弦（ArcFace，和输入人像比）从 0.95 降到 0.79，最低 0.68，仍是同一个人（>0.5），主要原因是表情和张嘴幅度变化。
- 口型相关 0.16 是「嘴张开程度 vs 语音能量」的粗略代理，不是 SyncNet 置信度；正值、延迟 80 ms 说明嘴型跟着声音走。
- 两个模型同一时间点的对比图：`examples/avatar/contact_15s_480_fast.jpg`（上 InfiniteTalk，下 LongCat，0/4/7/11/14 秒）。LongCat 的手势和表情幅度更大，更像真人主播；但人脸越往后越偏离原图，嘴唇明显变红变深，第 14 秒 ArcFace 余弦只有 0.60（最低 0.43，接近换人的边缘），ΔE 0.58 也比 InfiniteTalk 大一个数量级。InfiniteTalk 动作偏克制，身份和颜色保持得更好。
- LongCat 第一次运行在写 mp4 时 ffmpeg 管道断开，已修 `common.encode`（帧格式归一化、宽高取偶数、保留 ffmpeg 报错），镜像 `20261104-190851` 上重跑成功。
- 40 步全质量还**没有实测**；9:16 见第 5b 节；720P 快速实测一条（D，见下）。`avatar.json` 的 `render_s_per_s` 里，InfiniteTalk 480_fast 是 15/45/90 秒三条暖态实测的均值（29.5、26.7、25.9 → 27），720_fast 是 D 的实测 94.6（取 95），LongCat 480_fast 是 15 秒实测，其余格是估算：InfiniteTalk 40 步约为 4 步的 10 倍（720_full 因此从 670 改成 940）；LongCat 720 仍按像素比估算；LongCat 只有 8 步 distill 一条路径，fast 和 full 相同。
- **D：720P 快速 15 秒（集成验证）**：同一人像，口播稿 `pitch15`（有机牛奶买二送一），输出 1280×704、14.96 秒，暖态渲染 1415 秒 = **94.6 秒/输出秒**（是 480 的 3.5 倍，之前按像素比估的 2.3 倍偏乐观），峰值显存 80.9 GB（96 GB 卡还够），约 $0.54 一条，$2.2 每输出分钟。身份余弦首/末 1/4 均值 0.72/0.73，最低 0.53（第 2 秒），ΔE 0.07。看帧（`examples/avatar/infinitetalk_15s_720_fast_sheet.jpg`）：细节明显更清楚（发丝、开衫纹理、货架商品），嘴型、手势正常。720P 适合短片段；长口播用 480 更划算。

### 长片段稳定性（集成验证，2026-11-04 19:44–21:22）

A、B 两条走工作室数字人页的接口（`POST /api/avatars`，口播稿 + Polly Zhiyu，素材库人像 `portrait_16x9.jpg`，默认模型，480 快速）；C 是模板 `wf_product_pitch`（见下）。同一台 g7e.4xlarge Spot（us-east-1a）依次跑完。逐秒指标用 `infra/avatar/bench/trace.py`（每秒一个 ArcFace 余弦对输入人像，每秒 Lab 均值对第 1 秒的 ΔE），每条按时间切 6 段取身份余弦均值：

| 片段 | 1/6 | 2/6 | 3/6 | 4/6 | 5/6 | 6/6 | 余弦 < 0.5 的秒数 | ΔE 最大 |
|---|---|---|---|---|---|---|---|---|
| A 45 秒超市促销（49.3 s） | 0.75 | 0.71 | 0.68 | 0.66 | 0.67 | 0.71 | 0 / 50 | 0.07 |
| B 90 秒直播带货（89.5 s） | 0.71 | 0.73 | 0.68 | 0.71 | 0.62 | 0.56 | 6 / 90 | 0.07 |
| C 模板商品口播（42.4 s） | 首 1/4 均值 0.70 | | | | | 末 1/4 均值 0.72 | 0 / 43（最低 0.61） | 0.06 |

- **偏色：没有。** 90 秒全程 ΔE 不超过 0.07（肉眼不可见的量级；LongCat 15 秒就到 0.58）。lightx2v 4 步 LoRA 下没有出现 FusionX 那种越往后越偏色的问题。
- **身份：45 秒内稳定，90 秒最后约 1/3 开始缓慢漂移。** 前 60 秒余弦一直在 0.66–0.75，之后降到 0.62，最后 15 秒均值 0.56，最低 0.44（第 81 秒），有 6 秒低于 0.5。看帧（`examples/avatar/infinitetalk_live90_480_fast_sheet.jpg`，0/18/36/54/72/89 秒）：发型、服装、货架背景始终一致，没有换脸感；末段脸型略变窄、表情变平，是「同一个人但不太像原图」，不是 LongCat 那种嘴唇变色。A 起中末帧和原图很接近，最低点 0.52（第 48 秒）是一个侧头张嘴的瞬间。
- **建议**：60 秒以内的口播直接一条生成；超过 60 秒用自动分段（第 5b 节，默认开，每段 ≤ 55 秒），默认模型保持 `infinitetalk`。
- 口型：抽帧看嘴型随台词开合，和声音对得上。`analyze.py` 的嘴张开 vs 能量相关在 A 上只有 0.01（最佳延迟落在搜索边界 +320 ms）；这个代理指标在长片段上不可靠（Polly 语速快、句间停顿短，能量曲线接近平坦），表里没填。
- 速度：暖态 InfiniteTalk 480 快速在 49 秒和 90 秒片段上是 26.7 和 25.9 秒/输出秒，比 15 秒那条（29.5）略快，固定开销被摊薄了。A 是冷启动：提交 19:44:07，实例 19:44:09 拉起，适配层就绪约 19:48，加载模型 519 秒，19:57 开始生成，冷启动约 13 分钟，和之前一致。

### 模板 `wf_product_pitch` 端到端

输入「有机纯牛奶，250ml×12 盒，今天下单买二送一」，人像节点换成 `input.asset`（同一张 16:9 人像）。Agent 写稿 11.5 秒（195 字，只用了介绍里的事实，没有绝对化用语），在任务中心 `POST /api/tasks/reviews/<run>/n3` 批准；Polly 1 秒（42.5 秒语音）；数字人节点排在 A、B 后面，worker 渲染 1123 秒（26.4 秒/输出秒）；第二个审核同样在任务中心批准，`output.save` 存成作品「商品口播」。通知依次有「工作流待审核」×2 和完成记录。成片：`examples/avatar/wf_product_pitch_milk.mp4`，六帧 `wf_product_pitch_milk_sheet.jpg`。

## 5b. 长口播自动分段 + 竖屏 9:16（2026-11-04 22:51–00:20）

### 设计

一条生成时，InfiniteTalk 的身份在约 60 秒后慢慢漂移（见上）。所以超过 `segment_s`（默认 55 秒，0 = 不分段，可设 15–60）的语音切成几段，**每段都从原人像生成**，再拼成一条：

- **切点**：口播稿（Polly 路径）在句末（。！？；）切成估算时长 ≤ `segment_s` 的几块，并尽量均分（不会出现一大段加一个很短的尾巴）。每块单独合成语音，不会切断词。单句太长时退到逗号，最后才按字数硬切。上传音频用 ffmpeg `silencedetect`（-35 dB，≥ 0.25 秒）找停顿，切在离均分点最近的停顿中点。这个停顿不能超过上限，也不能让前后任一块短于上限的 30 %。找不到停顿就在均分点硬切（仍 ≤ 上限）。
- **生成**：同一台暖机上依次跑（worker 一次一个任务）。所有段用同一张人像、同一 seed、prompt、清晰度和模型。`avatar.generate()` 仍是单条原语，`avatar.generate_long(segments, work_dir, ...)` 在它外面包一层。数字人页（`avatar_api._run`）和工作流节点 `avatar.infinitetalk`（新参数 `segment_s`，节点版本 2）都用它。
- **拼接**（`avatar.join`）：每段视频先按它自己的音频长度裁齐或补齐，模型输出通常比音频多几帧。接缝处做 0.3 秒画面 `xfade`：前一段多保留 0.3 秒，垫在下一段开头下面，淡入淡出从前一段音频结束的时刻开始。所以视频总长 = 音频总长，口型不错位。音轨是各段语音直接拼起来的原始 PCM，连续，接缝不做淡化。
- **进度和重试**：任务备注显示「第 k/n 段」。某一段失败时整个任务失败，错误里写明「第 k/n 段失败：…」。做完的段存在 `data/avatars/<id>/segments/<k>-<hash>.mp4`，hash 由段音频、人像和参数算出。页面「重试」（`POST /api/avatars/<id>/retry`）会直接复用这些段，Polly 分段语音（`speech/<k>.wav`）也会复用。节点同样把段缓存在节点目录里。
- **页面**：「分段」两个选项：自动，每段 ≤ 55 秒 / 不分段，下面一行说明。估算会显示分几段，每多一段加约 1 分钟固定开销。参考视频（v2v）输入**不分段**（参考视频驱动整条，切开对不上），页面和节点都写明了。
- 测试：`server/tests/test_avatar_segments.py`（15 条，GpuEC2 打桩，ffmpeg 真跑）：文本切分和上限、真实 `live90` 稿切成 2 段、静音切点和硬切、拼接后时长与音频差 < 0.1 秒、接缝后是下一段画面、重试复用已完成段、seed 改变不误复用、v2v 不分段、节点参数和校验、API 路径。全部测试 152 条通过。

### 实测（g7e.4xlarge Spot us-east-1a，镜像 `20261104-190851`，InfiniteTalk 480 快速，seed 42）

| 运行 | 人像 | 音频 | 输出 | 渲染（暖） | 秒/输出秒 | 峰值显存 | 身份 余弦 首 1/4 → 末 1/4（最低） | ΔE 最大 |
|---|---|---|---|---|---|---|---|---|
| E：9:16 直播带货（面霜） | 新生成 9:16 直播间主播 `portrait_9x16_live.jpg` | 25.8 s | 448×832 | 614 s | **23.8** | 63.0 GB | 0.82 → 0.75（0.62） | 0.05 |
| F：90 秒直播带货，自动分段 55 | `portrait_16x9.jpg` | 89.9 s（43.2 + 46.7） | 896×448，89.89 s | 1134 + 1281 = 2415 s | **26.9** | 64.1 GB | 0.71 → 0.68（0.47，第 34 秒） | 0.21（接缝那一秒，其余 ≤ 0.09） |
| G：9:16 720P 快速 15 秒 | 同 E | 15.0 s（`pitch15`） | 704×1280 | 1409 s | **94.2** | 80.1 GB | 0.85 → 0.77（0.61） | 0.04 |

E 的口播稿按 30 秒写，Polly 读出来 25.8 秒。

**F 和不分段的 B（同一稿、同一人像、同一 seed）每 15 秒身份余弦均值**：

| | 0–15 | 15–30 | 30–45 | 45–60 | 60–75 | 75–90 | < 0.5 的秒数 |
|---|---|---|---|---|---|---|---|
| B 不分段（89.5 s） | 0.70 | 0.73 | 0.68 | 0.71 | 0.62 | **0.56** | 6 |
| F 自动分段（接缝 43.2 s） | 0.71 | 0.72 | 0.66 | 0.64 | 0.72 | **0.68** | 1 |

- **后 1/3 不再漂移**：60–90 秒从 0.62/0.56 回到 0.72/0.68，和开头同一水平。第二段从原人像重新开始，第 45–60 秒窗口里的 0.64 是第二段的开头几秒，不是累积漂移。F 唯一低于 0.5 的一秒（第 34 秒，0.47）在第一段里，是一个侧头张嘴的瞬间，B 在同一位置附近也有低点。看六帧（`infinitetalk_live90_seg55_480_fast_sheet.jpg`，0/18/36/54/72/89 秒）：末帧的脸型和第 0 秒一致，B 末段那种「脸变窄、表情变平」没有了。
- **偏色**：仍然没有。只有接缝那一秒（第 43 秒，包含叠化窗口）ΔE 0.21，其余都 ≤ 0.09。
- **接缝**（`infinitetalk_live90_seg55_seam.jpg`，42.2 / 43.0 / 43.36 / 43.72 / 44.2 秒）：前一段结尾人物在笑，嘴微张，双手在胸前；第二段从原人像的姿势开始，几乎同一位置、同一构图，只是表情换成说话。43.36 秒那帧是两段叠化的半透明画面，能看出轻微的双影（嘴和头发边缘）。逐帧像素差在接缝附近最大 2.16，全片中位数 0.85，第 95 百分位 1.66，比正常动作略大但没有跳切感。正常速度播放时看起来像一次快速的低头/换表情，可以接受。接缝在 Polly 的句间停顿上，声音没有断。
- **耗时**：分段不增加 GPU 渲染时间（26.2 和 27.4 秒/输出秒，与不分段 25.9 同一水平）。每段多付约 1 分钟固定开销，拼接在本机只要几秒。
- **9:16 竖屏**（E、G）：框图合适，脸完整在画面上半部，双手全程在画面下部，手势自然，没有手被裁掉。货架、环形灯和针织衫纹理保持住。身份比 16:9 还好（脸在画面里更大），偏色可以忽略。480 输出是 448×832。720 是 704×1280，速度和 16:9 的 720 一样（约 94 秒/输出秒），细节明显更好，适合 15 秒内的短片段。
- 没有跑：9:16 人像上的 90 秒分段（时间预算不够，约需 45 分钟 GPU）。

### 更新后的建议

- 默认开自动分段（≤ 55 秒）。60 秒以内本来就是一条，没有区别；更长的口播分段后身份全程保持，接缝处只有一次 0.3 秒叠化。
- 介意叠化双影的，可以把接缝放在口播稿的段落之间（稿子按段落写短句，切点就落在句末停顿），或设 `segment_s: 0` 一条生成（90 秒内漂移仍在「同一个人」范围）。
- 竖屏直播间：480 快速做长片段，720 快速做 15 秒内的精选片段。

## 6. 选型：默认 `infinitetalk`

- 口播带货最要紧的是「一直是同一个人」：15 秒内 InfiniteTalk 的身份余弦最低 0.68，LongCat 掉到 0.43，嘴唇颜色也漂了。电商场景用户上传的是真实人像或固定 IP 形象，漂移不可接受。
- 速度和成本基本持平（29.5 对 30.9 秒/输出秒，$0.68 对 $0.71 每分钟），显存都在 64–66 GB。
- LongCat 的优势是冷启动短（加载 16 秒对 8.7 分钟）和动作更生动，口型相关略高（0.20 对 0.16）。保留为可选项，适合追求表现力、可以接受轻微「换脸感」的短片。
- 90 秒长片段（集成验证）：InfiniteTalk 没有偏色（ΔE ≤ 0.07），身份在 60 秒后缓慢下降（末段余弦均值 0.56），见第 5 节「长片段稳定性」。默认继续用 `infinitetalk`，超过 55 秒的口播自动分段生成（第 5b 节：分段后 60–90 秒的余弦回到 0.72/0.68）。LongCat 的 90 秒没有跑（15 秒内已漂到 0.43，长片段只会更差）。
- InfiniteTalk 官方提醒：FusionX LoRA 超过 1 分钟会加重偏色、降低身份保持；这里用的 lightx2v 4 步 LoRA 实测 90 秒不偏色，身份有轻微漂移。

## 7. 限制

- 只离线渲染，不做实时直播。
- 一个实例一次一个任务，两个模型轮流用时每次切换都要重新加载（InfiniteTalk 约 8.7 分钟）。
- 速度：480P 快速模式约 26–30 秒算 1 秒视频，60 秒口播约 27 分钟、约 $0.6；720P 快速约 95 秒算 1 秒视频，15 秒片段约 24 分钟。工作室页面的估算读 `avatar.json` 里的 `render_s_per_s`、`cold_min`、`usd_per_hour`。
- LongCat 收到参考视频时只用第一帧作为人像（它的 ai2v 只吃图片）；InfiniteTalk 支持视频驱动（v2v）。
- 音频上限 600 秒。

## 8. 权限

- `evp-gpu-worker` 加了内联策略 `evp-gpu-worker-avatar`：`jobs/avatar-ec2/*` 的 Get/Put/Delete（加该前缀的 List），以及 ECR `evp/avatar` 拉取。同样的内容也写进了 `infra/gpu_ec2/grant_worker_role.sh`，管理员重建角色时会带上。权重前缀已在原策略的 `weights/*` 读权限里。
- `evp-video-builder` 加了内联策略 `evp-builder-avatar`：us-east-1 桶 `weights/infinitetalk/*`、`weights/longcat-avatar-1.5/*`、`jobs/avatar-ec2/*`、`jobs/avatar-build/*` 的读写和 List，加 ECR `evp/avatar` 推送。
- 新建了 ECR 仓库 `evp/avatar`（us-east-1）。

## 9. 许可证

- InfiniteTalk：Apache-2.0；Wan2.1-I2V-14B-480P：Apache-2.0；chinese-wav2vec2-base：MIT；lightx2v LoRA（Kijai/WanVideo_comfy 转存）：仓库没有写许可证，本项目只做个人、非商业使用。
- LongCat-Video / LongCat-Video-Avatar-1.5：MIT；whisper-large-v3：Apache-2.0；Kim_Vocal_2 人声分离模型（随 LongCat 权重分发）。
- 输出不加水印（设计如此），使用者自己保证人像有授权。
