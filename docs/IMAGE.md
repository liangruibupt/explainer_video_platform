# 图像模块设计（阶段 2 第一步）

阶段 2 先做图像模块：用它跑通「SageMaker 异步端点 + 缩容到 0」这条链路，验证通过后再照搬到视频和音乐。总体方案见 [DESIGN.md](DESIGN.md) 第 3、4 节，本文只写图像模块的具体做法。

## 1. 在讲解视频里用来做什么

讲解视频的画面由 Opus 写的 Canvas 代码绘制，图像模块不替代它，只补上 Canvas 画不好的部分：

| 用途 | 例子 | 生成方式 |
|---|---|---|
| 整段背景 | 宋代山水长卷、机房、星空 | 文生图，与画面同比例 |
| 插图 | 苏轼画像、东坡书院、GPU 芯片外观 | 文生图 |
| 透明贴图 | 图标、人物、器物，叠在图表上 | 文生透明 PNG（RGBA） |
| 改图 | 统一几张插图的画风、换背景 | 带参考图编辑，最多 10 张参考图 |

接入工作室的方式：分镜里每段可以带一个可选的 `images` 列表（用途、提示词、比例、是否透明）。生成好的图片和[苏轼示例](../examples/sushi-8-places/)的地图背景走同一条路：以 data URL 放进页面，绘制代码通过 `T.img[id]` 使用。背景图仍由页面外壳在每帧最底层画好。

## 2. 模型

[Qwen-Image 2.1](https://huggingface.co/Qwen/Qwen-Image-2.1)，Qwen Research License，个人非商用可以使用（DESIGN.md 第 3 节已确认）。

| 组件 | BF16 权重大小 |
|---|---|
| 文本编码器（Qwen3-VL） | 17.5 GB |
| 图像主干（7B DiT） | 14.2 GB |
| VAE | 1.4 GB |
| 合计 | 33.1 GB |

另外几个要点：
- 用 diffusers 的 `QwenImage21Pipeline` 推理。目前要从 GitHub 安装 diffusers，并且需要 transformers ≥ 5.17。镜像里要固定 diffusers 的 commit。
- 官方给的比例和分辨率：16:9 是 2752×1536，9:16 是 1536×2752，1:1 是 2048×2048。
- **本项目默认 1080p（已确定）**：16:9 按 1920×1088 生成再裁成 1920×1080，9:16 按 1088×1920 裁成 1080×1920（边长要能被 16 整除），1:1 用 1440×1440。默认 40 步。

## 3. 机型：和 DESIGN.md 原先的选择不同

DESIGN.md 原先写的首选机型是 ml.g7.2xlarge（32 GB 显存）。但 BF16 权重就有 33.1 GB，再加上 2K 分辨率推理时的中间数据，32 GB 放不下。只有两种办法：一是把把一部分组件降精度（本项目把文本编码器降为 8-bit），二是把文本编码器挪到 CPU 上，用时再搬回 GPU，每张图要多花几秒到十几秒。

us-east-1 的按需价格（AWS 价目表 API，2026-11-03 查询）：

| 机型 | GPU 显存 | $/小时 | 能否直接跑 BF16 |
|---|---|---|---|
| ml.g6e.2xlarge | 48 GB（L40S） | 2.80 | 能 |
| ml.g7.2xlarge | 32 GB（RTX PRO 4500） | 3.15 | 不能，需要 FP8 或 CPU offload |
| ml.g7e.2xlarge | 96 GB | 4.20 | 能，余量最大 |
| ml.g6.2xlarge | 24 GB（L4） | 1.22 | 不能 |

**已确定（2026-11-03）：三种机型都支持，按显存够用的前提下价格从低到高排优先级。** `infra/image/deploy.py plan` 在部署时实时查询每种机型的显存和该区域的按需价格，不够显存的剔除，其余按价格排序写进 instance pools。us-east-1 当前结果：

| 优先级 | 机型 | 显存 | $/小时 | 加载方式 |
|---|---|---|---|---|
| 1 | ml.g6e.2xlarge | 45 GB | 2.80 | `full`：全部 BF16 |
| 2 | ml.g7.2xlarge | 32 GB | 3.15 | `lowvram`：文本编码器 8-bit，其余 BF16 |
| 3 | ml.g7e.2xlarge | 96 GB | 4.20 | `full` |

两种加载方式各建一个 SageMaker Model，g7 那一项用 `ModelNameOverride` 指向 `lowvram` 那份。容器用 CUDA 12.8 镜像，端点用 `al2023-ami-sagemaker-inference-gpu-4-1`（NVIDIA 580 驱动），这样同一个镜像能在 L40S 和 Blackwell（g7、g7e）上跑。

本账号在 us-east-1 这三种机型的端点配额都是 2 个实例，单用户够用。

## 4. 推理容器

镜像只放运行时，不放权重：
- 基础镜像：AWS PyTorch 推理 DLC（CUDA 12.x），再装固定版本的 diffusers、transformers 和 accelerate。
- 实现 SageMaker 要求的 `/ping` 和 `/invocations` 两个接口，用一个小的 FastAPI 服务。
- 权重通过 `ModelDataSource` 引用 S3 上未压缩的目录（`S3DataType=S3Prefix`、`CompressionType=None`），SageMaker 在启动时下载到 `/opt/ml/model`。启动时加载到 GPU，加载完成后 `/ping` 才返回 200，避免请求在加载过程中进来。

请求格式：输入 JSON 由编排器写到 S3。

```json
{
  "mode": "t2i | edit | rgba",
  "prompt": "北宋山水长卷风格，远山、江面、渔舟，淡墨设色，无文字",
  "ratio": "16:9",
  "steps": 40,
  "seed": 42,
  "n": 1,
  "refs": ["s3://.../ref1.png"]
}
```

`refs` 只在 `edit` 模式下使用。输出是一个 JSON，里面列出生成的 PNG 在 S3 上的路径、seed 和耗时。PNG 直接写进同一个 S3 输出目录。

## 5. 端点和缩容到 0

| 配置 | 值 | 原因 |
|---|---|---|
| 推理方式 | 异步推理 | 只有异步端点支持缩到 0，且请求能在实例启动期间排队 |
| `MaxConcurrentInvocationsPerInstance` | 1 | 一张卡同时只跑一张图 |
| 伸缩范围 | 最小 0，最大 1 | 单用户；以后要并发再调高 |
| 扩容 | `HasBacklogWithoutCapacity` 告警，加 1 个实例 | 从 0 启动的唯一触发条件 |
| 缩容 | 用 `ApproximateBacklogSizePerInstance` 做目标跟踪，目标 1；缩容冷却 600 秒 | 队列空约 15 分钟后回到 0 |
| 结果通知 | 先轮询 S3 输出路径，不接 SNS | 单用户，量小，少一个服务 |
| 标签 | `project=explainer-video`、`module=image` | 按标签统计成本 |

部署脚本 `infra/image/deploy.py` 提供四个子命令：
- `up`：上传模型配置、创建端点、注册伸缩策略。
- `down`：删除端点和告警，保留 S3 权重。
- `status`：显示实例数、队列长度和最近的冷启动耗时。
- `smoke`：从 0 状态发一张图，记录各段耗时。

凭证使用部署机器的 IAM 角色。端点执行角色只能读写本项目的 S3 桶，以及拉取本项目的 ECR 镜像。

## 6. 工作室里的变化

- 后端新增 `server/app/image.py`，流程是：把输入写到 S3，调用 `InvokeEndpointAsync`，轮询输出。
- 前端的「图像」页从占位页改成真正的生成页：
  - 参数：提示词、比例、透明背景、参考图、张数、seed。
  - 显示端点状态：「已就绪」或「正在启动 GPU，约 N 分钟」。
- 讲解视频管线新增一个可选的「生成插图」步骤，位置在写分镜之后、写绘制代码之前。没有部署端点时自动跳过。
- 演示模式不调 GPU，返回预置样例图，方便没有部署端点时开发前端。

## 7. 成本和耗时（2026-11-03 实测，us-east-1）

| 项目 | 实测 | 说明 |
|---|---|---|
| 首次部署到 InService | 7.5 分钟 | 含实例启动、拉镜像（7 GB）、下载 33 GB 权重、加载 |
| 从 0 冷启动到拿到第一张图 | **8.3 分钟**（498 秒） | 含 64 秒生成 |
| 单张 1920×1080（40 步） | 64 秒生成，76 秒端到端 | ml.g7.2xlarge，`lowvram` 模式 |
| 最后一张图到实例回到 0 | 16–16.5 分钟 | 15 分钟冷却加告警判定时间 |
| 一次使用（冷启动 + 几张图 + 闲置到 0） | 约 30 分钟 × $3.15/小时 ≈ **$1.6** | 两次测试都分到 g7.2xlarge |
| 闲置 | 约 $1/月 | S3 权重和 ECR 镜像 |

两次部署时首选的 ml.g6e.2xlarge 都没有容量，instance pools 自动用了第二优先的 ml.g7.2xlarge。说明资源池起了作用，也说明 32 GB 卡必须能跑通：
- 文本编码器 8-bit，其余 BF16（`lowvram`）。
- VAE 开启分块解码（`enable_tiling` 和 `enable_slicing`）。不开的话，1920×1088 解码时要一次申请 2.2 GB，在 32 GB 卡上会显存不足。

部署过程中修过的问题：
- `torch==2.8.0` 没有 cu128 版，改用 2.7.1。
- SageMaker 不接受 OCI 格式的镜像清单，`deploy.py build` 改用 `docker buildx --provenance=false --output type=registry,oci-mediatypes=false`。

样例：`docs/assets/image_smoke.jpg`，提示词是北宋山水长卷、淡墨设色、无文字。

## 7a. EC2 部署（2026-11-04 起，当前方案）

**SageMaker 端点 `evp-image` 已下线**（`infra/image/deploy.py down`，S3 权重和 ECR 镜像保留）。图像模块现在和视频模块一样跑在平台自己启停的 EC2 GPU 实例上：控制器 `server/app/gpu_ec2.py`，配置 `infra/gpu_ec2/modules/image.json`，工作室客户端 `server/app/image.py`（对外函数 `status()` 和 `generate()` 不变）。`infra/image/deploy.py` 保留，作为 g5/g4dn 这类老 GPU 的后备方案。

- 区域 us-east-1，队列 `s3://evp-models-747411437379-us-east-1/jobs/image-ec2/`（和 SageMaker 用的 `jobs/image/` 分开）。
- 镜像和权重沿用 SageMaker 那套：`evp/qwen-image:20261103-125014-amd64`，权重 33 GB 复制到本地 NVMe 后只读挂到 `/opt/ml/model`。`EVP_MEM_MODE=auto`：显存 ≥ 44 GB 用 `full`，32 GB 的 g7 用 `lowvram`。
- 容量顺序：按需 g6e.2xlarge → g7.2xlarge → g7e.2xlarge（每种在提供该机型的可用区里最多试 3 个），然后同样的顺序试 Spot。拿到一台就停。
- 空闲 15 分钟自己关机并终止，4 小时硬上限。

实测（提示词同第 7 节，16:9 文生图，40 步，seed 7）：

| 项目 | 实测 | 说明 |
|---|---|---|
| 拿到实例的选项 | **Spot g7.2xlarge**，us-east-1a | 前 12 项（按需 g6e/g7/g7e 各 3 个可用区，Spot g6e 3 个可用区）都是 `InsufficientInstanceCapacity` |
| 依次尝试到拿到实例 | 151 秒 | 第 13 项 |
| 实例开机到 bootstrap 开始 | 约 55 秒 | |
| 拉镜像（7 GB） | 112 秒 | 和复制权重同时进行 |
| 复制权重（33 GB，S3 → NVMe） | 68 秒 | |
| 模型加载 | 40 秒 | 容器启动到 `/ping` 200 |
| 生成 1920×1080 | 64.0 秒 | `lowvram` 模式，RTX PRO 4500 Blackwell 32 GB |
| **从 0 冷启动到拿到图** | **8.4 分钟**（501 秒） | 从提交请求算起，和 SageMaker 的 498 秒差不多 |
| 热实例上再出一张（seed 11，走工作室客户端） | 62.1 秒生成，**70.8 秒端到端** | |
| 最后一张到实例自己关机 | **15.1 分钟**（空闲 904 秒） | 04:41:11 最后一张完成，04:56:16 自己关机并终止 |
| 一次使用（冷启动 + 几张图 + 闲置 15 分钟） | 约 30 分钟 × $0.58/小时（Spot）≈ **$0.3**；按需 g7.2xlarge $2.52/小时约 $1.3 | |

画面：淡墨设色的绢本山水，远山层叠、江面开阔，左下一叶带桅杆的渔舟，船尾立一人，没有文字。热实例那张（seed 11）构图相近，青绿设色更重。

## 8. 验收（阶段 2 图像部分）

1. 在 us-east-1 从 0 实例发出一张图。记录冷启动和出图各花了多久，并写回本文第 7 节。
2. 闲置后实例数回到 0，CloudWatch 能看到这个变化。记录从最后一张图出来到回到 0 用了多久。
3. 三种模式各出一张：文生图 16:9、透明 PNG、带 2 张参考图的编辑。
4. 用生成的背景图和插图重新生成苏轼示例，并和当前版本对比。
5. 按标签统计这次测试的实际花费。

## 9. 已确定（2026-11-03）

- 机型：g6e、g7、g7e 都支持，显存够用的里面价格最低的优先（第 3 节）。
- 默认分辨率：1080p。
- 空闲 15 分钟后缩回 0 台（缩容冷却 900 秒）。实际回到 0 的时间会更长一点，因为目标跟踪的缩容告警自己也要连续若干分钟队列为空才触发，阶段 2 验收时实测并写回第 7 节。
