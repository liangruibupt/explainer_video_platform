# 视频模块设计（阶段 2 第二步）

图像模块（[IMAGE.md](IMAGE.md)）跑通「SageMaker 异步端点 + 缩容到 0」之后，视频模块照同一套做法部署。本文只写视频模块和图像模块不同的地方。

## 1. 在讲解视频里用来做什么

画面主体仍然是 Opus 写的 Canvas 动画。视频模型只用来生成少数几个 5 秒左右的实拍感镜头，例如：
- 西湖的湖面
- 江上的渔舟
- 机房里一排闪灯的服务器

分镜里给某一段加上可选的 `clip` 字段，写明提示词、时长、比例，以及可选的首帧图。首帧图可以先用图像模块生成。生成的片子作为这一段的背景层，Canvas 在上面叠字和标注。

H3 生成的视频自带声音，混音时去掉这条音轨，只保留旁白、背景音乐和音效。

## 2. 模型和许可

[MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3)，33B 参数的视频加音频 DiT，许可证是 MiniMax H3 Community License Agreement。

**地区限制**：授权只覆盖「Applicable Territory」，明确排除欧盟、英国、韩国和美国。所以：
- GPU 端点部署在**孟买 ap-south-1**（已确定，2026-11-03）。东京 ap-northeast-1 也有 g7e，作为将来的备选区域。
- 用户本人所在地区是否属于授权范围，由用户自己确认。本文不替用户下结论。
- 许可证要求分发给第三方时附带 NOTICE 文件。本项目是单用户自用，不分发模型。

**只用 FL2VA 分区**：这个分区支持文生视频（t2va）和首尾帧生视频（fl2va），权重约 135 GB。参考图生视频（ref2va）在另一个分区，暂时不部署，因为一个服务进程只能加载一个分区。

## 3. 推理方案：照搬 whn09/minimax_h3_g7e

参考实现是 [whn09/minimax_h3_g7e](https://github.com/whn09/minimax_h3_g7e)，本文引用的是它 `c380891` 版本的实测数据。

- **推理框架**：sglang，基础镜像按 digest 固定为 `lmsysorg/sglang:nightly-dev-20260818-c0b6474b`。参考仓库的 Dockerfile 已经把 sglang 补丁和 SageAttention（sm_120 版）一起打进镜像，我们直接用它的 Dockerfile 构建。
- **权重**：transformer 部分从 BF16 自己量化成 NVFP4，FL2VA 分区约 10 分钟，只用 CPU。text encoder 加载时放在 CPU 上（`--layerwise-offload-components text_encoder`），参考仓库说明这是单卡 96 GB 的必需项。
- **默认档位**：Turbo LoRA 8 步，再叠加 Cache-DiT（`RDT=0.24`、`WARMUP=2`）。LoRA 先离线合进 BF16 权重，再做 NVFP4 量化。

| 768p、5 秒、单卡 | 生成耗时 | 相对 stock 20 步 |
|---|---|---|
| stock NVFP4 + sage，20 步 | 114 秒 | 1.00× |
| Turbo 8 步 | 47 秒 | 2.41× |
| **Turbo 8 步 + Cache-DiT（默认）** | **36 秒** | 3.14× |

480p 同一档约 12 秒。成片固定 24 fps，5 秒一条是 124 帧。

**分辨率**：默认 768p，比例 16:9 或 9:16。讲解视频是 1080p 输出，先用 ffmpeg 把片子放大到 1080p 再进合成。H3 原生到 768p，更高的分辨率参考仓库没有测过。

## 4. 机型

所有档位都只用 1 张 GPU。参考仓库在 g7e.4xlarge 和 g7e.12xlarge 上测试，单卡峰值显存约 52–56 GB。

| 机型 | GPU | 内存 | EC2 孟买 $/小时 | 能否使用 |
|---|---|---|---|---|
| ml.g7e.2xlarge | 1 × 96 GB | 64 GB | 5.49 | 不能。text encoder 放在 CPU 上要 63 GB 内存，装不下 |
| **ml.g7e.4xlarge** | 1 × 96 GB | 128 GB | 6.53 | **首选** |
| ml.g7e.8xlarge | 1 × 96 GB | 256 GB | 未查 | 备选，内存余量大 |

表里是 EC2 的按需价格。AWS 价目表 API 暂时查不到 SageMaker 在孟买的 g7e 价格，部署时用 `deploy.py plan` 实时取。参考 us-east-1 的情况，SageMaker 价格比 EC2 略低：us-east-1 的 ml.g7e.4xlarge 是 $5.00/小时。

instance pools 按图像模块的规则排列：先剔除内存或显存不够的机型，剩下的按价格从低到高排序。结果是 **g7e.4xlarge 优先，g7e.8xlarge 其次**。本账号在 ap-south-1 这三种机型的端点配额都是 2 个。

## 5. 容器

SageMaker 和 sglang 服务对接的方式：
- 容器里在 8000 端口启动 `sglang serve`，参数与参考仓库 `serve.sh` 中 fl2va 单卡那组一致。
- 再起一个很小的适配层，在 8080 端口提供 `/ping` 和 `/invocations`。
  - `/ping`：sglang 的 `/health` 返回 200 后，它才返回 200。
  - `/invocations`：把请求转成 sglang 的 `POST /v1/videos`，轮询直到完成，再从 `/v1/videos/{id}/content` 取回 MP4，以 base64 返回。
- 请求字段：`mode`（t2va 或 fl2va）、`prompt`、`aspect`、`short_edge`、`duration`（默认 5 秒）、`seed`；fl2va 另有 `first_frame` 和可选的 `last_frame`（base64 PNG）。
- 默认档位需要的环境变量（NVFP4、Cache-DiT 两组）写进 SageMaker Model 的 Environment 里。

一条 768p、5 秒的 MP4 约几 MB，base64 之后仍在异步推理的单条结果上限以内。

## 6. 权重准备（只做一次）

量化需要 CPU 和大内存，本机只有 30 GB 内存，放不下。所以在孟买临时开一台 EC2 来做，做完立刻终止：

1. 开 `r7i.8xlarge`（256 GB 内存，$2.18/小时），挂 600 GB gp3 盘。
2. 从 Hugging Face 下载 FL2VA 分区（约 135 GB），revision 固定为 `42ed227`。下载 Turbo LoRA。
3. 运行参考仓库的 `lora_merge_transformer.py` 和 `nvfp4_quantize_transformer.py`，自检要求 `259/259 modules` 和 `worst rel ≈ 0.095`。
4. 把服务需要的目录同步到 `s3://evp-models-747411437379-ap-south-1/weights/minimax-h3/<sha>/`：FL2VA 分区去掉 BF16 的 transformer，再加上 NVFP4 文件，约 90 GB。
5. 终止这台 EC2，删除它的盘。

预计 1.5–2 小时，费用约 $5，加上一次性的 S3 写入费用。

容器镜像约 47 GB，在本机构建会占满磁盘（本机剩 32 GB），所以也在这台临时 EC2 上构建，推到孟买的 ECR。

## 7. 端点和缩容到 0

和图像模块相同：异步推理，最少 0、最多 1 个实例，空闲 15 分钟后缩回 0，有任务但没有实例时拉起 1 个。不同之处：

| 配置 | 视频 | 图像 |
|---|---|---|
| 区域 | ap-south-1 | us-east-1 |
| `InvocationTimeoutSeconds` | 900 | 3600 |
| 模型下载超时 | 3600 秒（约 90 GB） | 1800 秒 |
| 实例数据盘 | 300 GB | 100 GB |

控制面（工作室、编排、作品存储）仍在 us-east-1。输入写到孟买的 S3，结果从孟买的 S3 取回。一条片子几 MB，跨区域传输费可以忽略。

## 8. 成本和耗时（2026-11-03 部署实测，ap-south-1）

部署代码、镜像、IAM、权重、端点配置全部就绪并验证通过，但**端点始终没能跑到 InService：
ap-south-1 的 g7e 持续没有容量**。至今共 5 次 `deploy.py up` 全部在约 16–17 分钟后以
`InsufficientInstanceCapacity` 失败（先试首选 ml.g7e.4xlarge、再试 ml.g7e.8xlarge，两种都没容量）：

- 2026-11-03：2 次（首轮）。
- 2026-11-04（Spot 容量评分 9/10 时重试，每次失败后退避 20 分钟）：
  - 尝试 1：00:22:20Z 起，00:39:26Z 报 `InsufficientInstanceCapacity`（约 17 分钟）。
  - 尝试 2：00:53:24Z 起，01:10:44Z 报 `InsufficientInstanceCapacity`（约 17 分钟）。
  - 尝试 3：01:30:05Z 起，01:47:02Z 报 `InsufficientInstanceCapacity`（约 17 分钟）。

Spot 容量评分（9/10）只是提示、并不保证按需（on-demand）能拿到容量，实测依旧全程拿不到。
这是 AWS 侧的瞬时容量问题，不是代码/镜像/权限/配置的问题，设计第 4 节已预计到这种情况。
失败的端点从没到过 InService，也没产生实例计费；每次失败后都 `deploy.py down`，确认端点/配置/模型全部清空。
所以下表里和生成速度、缩容、单次创作成本相关的行仍是**估算**，要等有容量跑通后再填实测值。

| 项目 | 状态 | 实测 / 估算 |
|---|---|---|
| 容器镜像构建 + 推 ECR | ✅ 实测 | 临时 EC2（m7i.2xlarge，ap-south-1）上约 7 分钟：拉 14 GB 基础镜像（约 160 s 解包）+ 装 fastapi/uvicorn + 推 v2 manifest。镜像 `evp/minimax-h3:adapter-20261103-150557` |
| IAM 角色校验 | ✅ 实测 | 首次用带 `s3:prefix` 条件的 ListBucket，被 SageMaker 角色预校验拒；去掉条件（Get 全桶 + Put 限 `jobs/`+ List 全桶）后通过 |
| 实例起不来到报错 | ✅ 实测 | 每次约 16–17 分钟，然后 `InsufficientInstanceCapacity`（至今 5 次全是如此）。整个过程没出现日志组，说明容量始终没拿到，不是下载/加载阶段的问题 |
| 首次部署到 InService | ⏳ 未测 | 预计 15–25 分钟：实例启动、拉 14 GB 镜像、下载约 90 GB 权重、加载、预热（只热 768p 16:9） |
| 从 0 冷启动到第一条片 | ⏳ 未测 | 同上冷启动 + 约 36 秒生成 |
| 单条 768p、5 秒（8 步，Turbo + Cache-DiT） | ⏳ 未测（估算） | 约 36 秒生成，加约 10 秒排队和传输。参考仓库 c380891 在 g7e 上的实测是 36 秒 |
| 最后一条到实例回到 0 | ⏳ 未测 | 约 16–16.5 分钟（15 分钟冷却加告警判定） |
| 一次创作（冷启动约 20 分钟 + 生成 5 条约 4 分钟 + 空闲 15 分钟） | ⏳ 未测（估算） | 约 40 分钟 × $6.53/小时 ≈ **$4.4**（g7e.4xlarge，EC2 按需价回退口径） |
| 闲置 | — | S3 存约 102 GB 权重加 14 GB ECR 镜像约 $3/月 |

价格说明：AWS 价目表 API 没有 ap-south-1 的 SageMaker g7e 行，`deploy.py plan` 自动回退到
EC2 按需价并标注来源：ml.g7e.4xlarge **$6.53/小时**（首选）、ml.g7e.8xlarge **$8.60/小时**（备选）。

冷启动比图像模块长很多。所以工作室在写完分镜、确认要生成视频片段时就先唤醒端点，和写绘制代码、合成旁白同时进行，不要等到真正用的时候才启动。

**重跑说明**：代码已就绪，有容量时 `cd infra/video && python3 deploy.py up --weights
s3://evp-models-747411437379-ap-south-1/weights/minimax-h3/42ed227ee7df40d41602854ae760620d6eb651fe/`
即可（镜像和权重都在），跑通后 `deploy.py smoke` 做一条 768p t2va，再把上表的估算行换成实测。
ap-south-1 的 g7e 已连续 5 次（2026-11-03 两次、2026-11-04 三次）都没拿到容量，建议下一步直接改到
东京 ap-northeast-1（设计第 2 节列为备选区）重试——需先把镜像和权重跨区复制过去。

## 9. 验收

1. 从 0 个实例开始，在 ap-south-1 生成一条 768p、5 秒的 t2va 片子。记录冷启动和生成各用多久。
2. 用图像模块生成的首帧图，做一条 fl2va。
3. 闲置后实例数回到 0。
4. 用 1–2 个视频镜头重做一个示例，例如苏轼杭州那段换成西湖实景。
5. 按标签统计这次测试的实际花费。

## 10. 待定

- 默认分辨率：768p 还是 480p。480p 快 3 倍，但放大到 1080p 后会比较软。
- 是否需要 ref2va（参考图生视频，人物或物品保持一致）。需要的话要再部署一个端点，权重再加约 90 GB。
