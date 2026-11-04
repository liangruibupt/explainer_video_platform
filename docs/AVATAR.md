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
| `infra/avatar/bench/` | 测试稿 `scripts.json`、Polly 合成 `make_audio.py`、批量跑 `run_bench.py`、指标 `analyze.py`、对比图 `contact_sheet.py` |
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

`747411437379.dkr.ecr.us-east-1.amazonaws.com/evp/avatar:20261104-190851`：CUDA 12.8.1、Ubuntu 22.04、Python 3.10（InfiniteTalk 用了 3.11 删掉的 `inspect.ArgSpec`）、torch 2.7.1+cu128、xformers 0.0.31.post1、flash-attn 2.8.0.post2（xformers 0.0.31 只接受 ≤2.8.0）、xfuser 0.4.4 + yunchang 0.6.3.post1，其余版本全部固定在 `requirements.txt`。

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
- 720P、40 步全质量、45 秒、90 秒、9:16 这些组合**这次没有跑完**（4 小时上限）。`avatar.json` 的 `render_s_per_s` 里，两个模型的 480_fast 是实测值（暖态），其余格是估算：720 约为 480 的 2.3 倍像素；InfiniteTalk 40 步约为 4 步的 10 倍；LongCat 只有 8 步 distill 一条路径，fast 和 full 相同。

## 6. 选型：默认 `infinitetalk`

- 口播带货最要紧的是「一直是同一个人」：15 秒内 InfiniteTalk 的身份余弦最低 0.68，LongCat 掉到 0.43，嘴唇颜色也漂了。电商场景用户上传的是真实人像或固定 IP 形象，漂移不可接受。
- 速度和成本基本持平（29.5 对 30.9 秒/输出秒，$0.68 对 $0.71 每分钟），显存都在 64–66 GB。
- LongCat 的优势是冷启动短（加载 16 秒对 8.7 分钟）和动作更生动，口型相关略高（0.20 对 0.16）。保留为可选项，适合追求表现力、可以接受轻微「换脸感」的短片。
- 90 秒长片段（集成验证）：InfiniteTalk 没有偏色（ΔE ≤ 0.07），身份在 60 秒后缓慢下降（末段余弦均值 0.56），见第 5 节「长片段稳定性」。默认继续用 `infinitetalk`，超过 60 秒的口播建议切段生成。LongCat 的 90 秒没有跑（15 秒内已漂到 0.43，长片段只会更差）。
- InfiniteTalk 官方提醒：FusionX LoRA 超过 1 分钟会加重偏色、降低身份保持；这里用的 lightx2v 4 步 LoRA 实测 90 秒不偏色，身份有轻微漂移。

## 7. 限制

- 只离线渲染，不做实时直播。
- 一个实例一次一个任务，两个模型轮流用时每次切换都要重新加载（InfiniteTalk 约 8.7 分钟）。
- 速度：480P 快速模式约 25–30 秒算 1 秒视频，60 秒口播约 25–30 分钟、约 $0.6–0.7。工作室页面的估算读 `avatar.json` 里的 `render_s_per_s`、`cold_min`、`usd_per_hour`。
- LongCat 收到参考视频时只用第一帧作为人像（它的 ai2v 只吃图片）；InfiniteTalk 支持视频驱动（v2v）。
- 音频上限 600 秒。

## 8. 权限

- `evp-gpu-worker` 加了内联策略 `evp-gpu-worker-avatar`：`jobs/avatar-ec2/*` 的 Get/Put/Delete（加该前缀的 List），以及 ECR `evp/avatar` 拉取。同样的内容也写进了 `infra/gpu_ec2/grant_worker_role.sh`，管理员重建角色时会带上。权重前缀已在原策略的 `weights/*` 读权限里。
- `evp-video-builder` 加了内联策略 `evp-builder-avatar`：us-east-1 桶 `weights/infinitetalk/*`、`weights/longcat-avatar-1.5/*`、`jobs/avatar-ec2/*`、`jobs/avatar-build/*` 的读写和 List，加 ECR `evp/avatar` 推送。
- 新建了 ECR 仓库 `evp/avatar`（us-east-1）。

## 9. 许可证

- InfiniteTalk：Apache-2.0；Wan2.1-I2V-14B-480P：Apache-2.0；chinese-wav2vec2-base：MIT；lightx2v LoRA（Kijai/WanVideo_comfy 转存）：随 lightx2v 项目，仓库未标许可证，商用前要确认。
- LongCat-Video / LongCat-Video-Avatar-1.5：MIT；whisper-large-v3：Apache-2.0；Kim_Vocal_2 人声分离模型（随 LongCat 权重分发）。
- 输出不加水印（设计如此），使用者自己保证人像有授权。
