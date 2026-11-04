# 视频模块设计（阶段 2 第二步）

图像模块（[IMAGE.md](IMAGE.md)）用的是「SageMaker 异步端点 + 缩容到 0」。视频模块要用 g7e，SageMaker 在孟买连续 5 次拿不到容量，所以改成平台自己启停的 EC2 实例（设计文档第 4 节「GPU 后端选择」）。本文只写视频模块和图像模块不同的地方。

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

## 4. 机型和容量顺序

所有档位都只用 1 张 GPU。参考仓库在 g7e.4xlarge 和 g7e.12xlarge 上测试，单卡峰值显存约 52–56 GB。

| 机型 | GPU | 内存 | 本地 NVMe | EC2 孟买按需 $/小时 | 能否使用 |
|---|---|---|---|---|---|
| g7e.2xlarge | 1 × 96 GB | 64 GB | — | 5.49 | 不能。text encoder 放在 CPU 上要 63 GB 内存，装不下 |
| **g7e.4xlarge** | 1 × 96 GB | 128 GB | 1.9 TB | 6.53 | **首选** |
| g7e.8xlarge | 1 × 96 GB | 256 GB | 1.9 TB | 8.60 | 备选 |

容量顺序写在 `infra/gpu_ec2/modules/video.json`，平台按顺序调 `RunInstances`，遇到 `InsufficientInstanceCapacity`、`InstanceLimitExceeded`、`Unsupported` 就试下一项：

1. 按需 g7e.4xlarge：ap-south-1a，再 ap-south-1b（只有这两个可用区有 g7e）
2. 按需 g7e.8xlarge：1a，再 1b
3. 同样四项改用 Spot（一次性请求，被回收时终止；正在跑的任务放回队列）

`ctl.py plan` 打印这个顺序和实时价格（按需价来自价目表 API，Spot 取最近成交价）。2026-11-04 实测 1b 的 Spot g7e.4xlarge 约 $2.67/小时，1a 是 $6.53/小时。

## 5. 容器

镜像沿用 SageMaker 那次构建的 `evp/minimax-h3:adapter-20261103-150557`（孟买 ECR，14 GB）：
- 容器里在 8000 端口启动 `sglang serve`，参数与参考仓库 `serve.sh` 中 fl2va 单卡那组一致。
- 外面一层很小的适配层，在 8080 端口提供 `/ping` 和 `/invocations`。
  - `/ping`：sglang 的 `/health` 返回 200 后，它才返回 200。
  - `/invocations`：把请求转成 sglang 的 `POST /v1/videos`，轮询直到完成，再取回 MP4，以 base64 返回。
- 请求字段：`mode`（t2va 或 fl2va）、`prompt`、`aspect`、`short_edge`、`duration`（默认 5 秒）、`seed`；fl2va 另有 `first_frame` 和可选的 `last_frame`（base64 PNG）。
- NVFP4、Cache-DiT 两组环境变量写在模块配置的 `env` 里，worker 用 `docker run -e` 传入。
- 端口只绑定 127.0.0.1，实例外面访问不到。

## 6. 权重准备（只做一次，已完成）

量化需要 CPU 和大内存，本机只有 30 GB 内存，所以在孟买临时开了一台 `r7i.8xlarge` 来做，做完即终止：从 Hugging Face 下载 FL2VA 分区（revision `42ed227`）和 Turbo LoRA，合并后量化成 NVFP4（自检 `259/259 modules`、`worst rel ≈ 0.095`），把服务需要的部分（约 96 GB，121 个对象）放到
`s3://evp-models-747411437379-ap-south-1/weights/minimax-h3/42ed227ee7df40d41602854ae760620d6eb651fe/`。镜像也在那台机器上构建并推到孟买 ECR。

## 7. EC2 后端：自己启停的 GPU 实例

代码：控制器 `server/app/gpu_ec2.py`（各模块通用），配置 `infra/gpu_ec2/modules/video.json`，实例上的程序 `infra/gpu_ec2/worker/`，命令行 `infra/gpu_ec2/ctl.py`。

**队列放在 S3**（单用户，不需要新的 IAM 权限），都在 `jobs/video/` 下：
- `in/<id>.json`：请求。控制器写入后，如果没有实例就启动一台。
- `claimed/<id>.json`：认领标记。worker 用 S3 条件写入（`If-None-Match`）认领，两台机器不会同时拿到同一个任务。
- `out/<id>.json` 和 `out/<id>.mp4`：结果；`fail/<id>.json`：失败原因。
- `heartbeat.json`：实例 id、状态（booting / loading / ready / busy）、时间、机型、可用区、按需还是 Spot。超过 3 分钟没更新就算过期。
- `logs/<实例 id>.log`：实例日志（实例没有入站端口，日志随心跳传到 S3）。

**控制器**：`submit(req)` 写入请求并确保有实例；`poll(id)` 查结果；`status()` 返回 none / launching / booting / loading / ready / busy；`stop()` 终止实例。启动前按标签 `project=explainer-video,module=video` 检查，每个模块最多一台。

**实例启动参数**：AMI 取 SSM 参数里最新的 Deep Learning Base OSS Nvidia Driver GPU AMI (Amazon Linux 2023)，本次是 `ami-06b0a48b43294cce8`（20261101），NVIDIA 驱动 595.112.07（要求 ≥ 580）。根盘 gp3 100 GB，终止时删除。安全组 `evp-gpu-worker` 没有任何入站规则。默认 VPC 公有子网，强制 IMDSv2，不配密钥对。`InstanceInitiatedShutdownBehavior=terminate`：实例自己关机就是终止，不留下任何东西。

**实例上的流程**（user-data 安装一个 systemd 服务，每一步可重入）：
1. 设 4 小时后强制关机。
2. 检查驱动版本。
3. 格式化本地 NVMe（1.9 TB 实例存储）挂到 `/opt/dlami/nvme`，docker 数据目录也放在上面。
4. 同时拉镜像、从 S3 复制权重到 NVMe（`aws s3 cp --recursive`，CRT 传输客户端）。
5. `docker run --gpus all`，权重目录只读挂到 `/opt/ml/model`。
6. 等 `/ping` 返回 200，然后每 5 秒查一次队列，一次跑一个任务。
7. 连续 15 分钟没有任务就 `shutdown -h now`，实例随即终止。

**权限**：这次测试用现有的 `evp-video-builder` 实例配置文件（它能读写整个桶、能推 ECR，但没有 S3 删除权限，所以由控制器清理 `in/` 和 `claimed/`）。**建议改用** `infra/gpu_ec2/grant_worker_role.sh` 创建的最小权限角色 `evp-video-worker`：只读权重，读写 `jobs/video/*`，只能拉镜像，加 SSM。这个脚本要由管理员在 CloudShell 里运行，运行后把配置里的 `instance_profile` 改成 `evp-video-worker`。

控制面（工作室、编排、作品存储）仍在 us-east-1，输入输出走孟买的 S3。一条片子 1–2 MB，跨区域传输费可以忽略。

### 之前 SageMaker 方案的结果

`infra/video/deploy.py` 保留，用来记录这条路为什么没走通：异步端点 + instance pools（ml.g7e.4xlarge → ml.g7e.8xlarge），2026-11-03 两次、2026-11-04 三次，每次约 17 分钟后都报 `InsufficientInstanceCapacity`，端点从没到过 InService。同一天改用 EC2 后，按需四项仍然全部没有容量，但第 6 项（Spot g7e.4xlarge，1b）一次就拿到了，整轮尝试约 80 秒。

## 8. 成本和耗时（2026-11-04 EC2 实测，ap-south-1）

测试提示词「西湖清晨，薄雾笼罩湖面……」，t2va，768p 16:9（1344×768），5 秒，8 步 Turbo + Cache-DiT。

| 项目 | 实测 | 说明 |
|---|---|---|
| 拿到实例的选项 | Spot g7e.4xlarge，ap-south-1b | 按需 4 项和 1a 的 Spot 都是 `InsufficientInstanceCapacity`，三次启动都一样 |
| 依次尝试到拿到实例 | 75–84 秒 | 8 个选项里试到第 6 个 |
| 实例开机到 bootstrap 开始 | 约 2.5 分钟 | DLAMI 首次开机 |
| 拉镜像（14 GB） | 145–154 秒 | 和复制权重同时进行 |
| 复制权重（96 GB，S3 → NVMe） | **70 秒**（CRT 客户端） | 默认传输客户端用了 797 秒，换成 CRT 后快 11 倍 |
| sglang 加载 + 预热 | 140 秒 | 容器启动到 `/ping` 200 |
| 第一条片生成 | 36.1 秒 | |
| **从 0 冷启动到拿到第一条片** | **12.5 分钟**（750 秒） | 从提交请求算起 |
| 热实例上再生成一条（seed 2） | **35.0 秒生成，52.5 秒端到端** | 端到端包括 S3 排队和传输 |
| 最后一条到实例自己关机 | **15.0 分钟**（空闲 902 秒） | 03:17:07 最后一条完成，03:32:09 关机；再过约 7 分钟 EC2 显示 terminated（03:39） |
| 第二次冷启动（实例终止后再提交，seed 11） | **12.6 分钟**（759 秒），生成 35.1 秒 | 拉镜像 154 秒、复制权重 71 秒；之后又空闲 15 分钟自己终止 |
| 一次创作（冷启动 12.5 分钟 + 5 条约 4 分钟 + 空闲 15 分钟） | 约 32 分钟 × $2.67/小时 ≈ **$1.4**（Spot）；按需 $6.53/小时时约 $3.5 | |
| 闲置 | 约 $3/月 | S3 里约 96 GB 权重加 14 GB ECR 镜像；没有实例时不产生 GPU 费用 |

成片：H.264 1344×768、24 fps、124 帧、5.18 秒；AAC 双声道 32 kHz 音轨（RMS 约 −40 dBFS，混音时本来就要去掉）。第一条 1.4 MB。

画面：开头是西湖湖面、远处的三孔石拱桥、两岸绿树和高大的水杉，左边一艘红色游船；中间游船往右划到桥前；结尾游船到了画面中央偏左。镜头全程不动，湖面有轻微波光。提示词里的「薄雾」不明显，画面偏阴天。

![第一条片中间一帧](assets/video_smoke_mid.jpg)

本次测试 4 台实例（2 台因启动问题提前终止）合计约 90 分钟 Spot g7e.4xlarge，约 **$4**；SageMaker 的 5 次失败没有产生实例费。

部署过程中修过的问题：
- 第一次启动时 DLAMI 没有自动挂载实例存储，按设备名找盘也没找到，worker 按设计自己关机终止了。改成按型号「Amazon EC2 NVMe Instance Storage」找盘并格式化。
- 第二次启动时 `evp-video-builder` 没有 `s3:DeleteObject`，worker 认领任务时出错退出了。改用 S3 条件写入认领，worker 不再需要删除权限。另外 worker 异常退出时实例会立刻关机，不会出现没人干活的 GPU 一直开着。

冷启动约 12 分钟，所以工作室在写完分镜、确认要生成视频片段时就先提交任务唤醒实例，和写绘制代码、合成旁白同时进行。

## 9. 验收

1. ✅ 从 0 个实例开始，在 ap-south-1 生成一条 768p、5 秒的 t2va 片子。记录冷启动和生成各用多久（第 8 节）。
2. 用图像模块生成的首帧图，做一条 fl2va。
3. ✅ 闲置后实例数回到 0（15 分钟空闲后自己终止，两次都确认）。
4. 用 1–2 个视频镜头重做一个示例，例如苏轼杭州那段换成西湖实景。
5. 按标签统计这次测试的实际花费（Cost Explorer 数据晚约 1 天，届时按 `module=video` 核对第 8 节的估算）。

## 10. 待定

- 默认分辨率：768p 还是 480p。480p 快 3 倍，但放大到 1080p 后会比较软。
- 是否需要 ref2va（参考图生视频，人物或物品保持一致）。需要的话要再部署一个端点，权重再加约 90 GB。
�是把镜像和权重复制到东京 ap-northeast-1，在容量顺序里加上东京。
