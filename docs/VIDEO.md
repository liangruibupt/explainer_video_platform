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

## 8. 成本（估算，部署后实测更新）

| 项目 | 估算 |
|---|---|
| 冷启动 | 15–25 分钟：实例启动、从 S3 拉 47 GB 镜像和 90 GB 权重、加载、预热 |
| 单条 768p、5 秒 | 约 36 秒生成，加约 10 秒的排队和传输 |
| 一次创作（冷启动 20 分钟 + 生成 5 条约 4 分钟 + 空闲 15 分钟） | 约 40 分钟 × $6.5/小时 ≈ **$4.3** |
| 闲置 | S3 存 90 GB 加 ECR 镜像约 $3/月 |

冷启动比图像模块长很多。所以工作室在写完分镜、确认要生成视频片段时就先唤醒端点，和写绘制代码、合成旁白同时进行，不要等到真正用的时候才启动。

## 9. 验收

1. 从 0 个实例开始，在 ap-south-1 生成一条 768p、5 秒的 t2va 片子。记录冷启动和生成各用多久。
2. 用图像模块生成的首帧图，做一条 fl2va。
3. 闲置后实例数回到 0。
4. 用 1–2 个视频镜头重做一个示例，例如苏轼杭州那段换成西湖实景。
5. 按标签统计这次测试的实际花费。

## 10. 待定

- 默认分辨率：768p 还是 480p。480p 快 3 倍，但放大到 1080p 后会比较软。
- 是否需要 ref2va（参考图生视频，人物或物品保持一致）。需要的话要再部署一个端点，权重再加约 90 GB。
