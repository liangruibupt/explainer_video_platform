# 图生 3D 模块（mesh）

用途：3D 白模预演（[PREVIS_CONTRACT.md](PREVIS_CONTRACT.md) 第 4.6 节）里的有机形体资产，比如动物、雕塑、植物。参数化建模做不好这类形状，就用一张概念图生成网格，再按真实尺寸放进素材库。机械、建筑这类规整的东西还是走参数化建模。

## 1. 流程

```
提示词 ──image.generate()（Qwen-Image，1:1）──▶ 概念图 ─┐
图片素材 ─────────────────────────────────────────────┴─▶ mesh.generate()（Hunyuan3D-2，GPU）
  ─▶ 原始 GLB（Hunyuan 自己的单位立方体）─▶ assets_build.normalize()（A：缩放到目标尺寸、落地、减面）─▶ 素材库 model
```

- 概念图提示词模板：「<提示词>。单个物体，纯白背景，三分之二侧视角，完整不裁切，无投影，画面中没有任何文字」。
- 默认只出几何（白模渲染时材质统一换成黏土色，用不到贴图）。`texture: true` 时再跑 Hunyuan3D-2 Paint，输出带 2048² 底色贴图的 GLB。
- 入口：`POST /api/assets/model/image3d`（后台任务）和工作流节点 `asset.image3d`，两者走同一个函数 `mesh_api.run_image3d()`。

## 2. 文件

| 文件 | 作用 |
|---|---|
| `infra/mesh/container/` | Dockerfile、适配层 `serve.py`（8080：`/ping`、`/invocations`）、导入检查 `check_imports.py`、`requirements.txt` |
| `infra/mesh/stage_weights.py` | HF → S3，固定 commit，逐个文件下载、上传、核对大小后删除（本机就能跑，最多占一个文件的磁盘） |
| `infra/mesh/builder.py` + `builder.sh` | 临时 x86 构建机（c7i.4xlarge，150 GB gp3，关机即终止，90 分钟硬上限），构建并推送 `evp/mesh:<tag>` |
| `infra/mesh/bench.py` | 从 0 实例实测：概念图（或固定图片）→ 依次跑各个用例，结果写 `data/mesh_smoke/results.json` |
| `infra/mesh/preview/render.sh` | 本机渲染 GLB 预览静帧（Remotion + `@remotion/three`，`--gl=swangle`） |
| `infra/mesh/iam/` | 本模块需要的两条内联策略（工作机、构建机） |
| `infra/gpu_ec2/modules/mesh.json` | GPU 工作机配置，格式同 `image.json` |
| `server/app/mesh.py` | 工作室客户端：`status()`、`generate(image, model, texture, seed, **kw) -> GLB` |
| `server/app/mesh_api.py` | HTTP 接口和后台任务（`data/mesh3d/<id>/`） |
| `server/app/mesh_node.py` | `asset.image3d` 节点，`register(NODES)` |

通用 worker（`infra/gpu_ec2/worker/**`）没有改动：结果里的 `glb_b64` 由 `outputs: {"glb_b64": "glb"}` 存成 `out/<id>.0.glb`。

## 3. 模型和权重

代码：Tencent-Hunyuan/Hunyuan3D-2@`f8db6309`（不 pip 安装，用 `PYTHONPATH` 引用，避免它的 setup.py 把 diffusers、transformers 顶到新版）。

权重：`s3://evp-models-747411437379-us-east-1/weights/hunyuan3d-2/9cd649ba6913f7a852e3286bad86bfa9a2d83dcf/`（tencent/Hunyuan3D-2 的 commit），34 个文件，23.35 GB，2026-11-25 从本机上传并逐个核对过大小，清单在 `_evp_manifest.json`。

| 目录 | 大小 | 用途 |
|---|---|---|
| `tencent/Hunyuan3D-2mini/hunyuan3d-dit-v2-mini/`（@`f90a0f7d`） | 3.8 GB | 形状 2mini（0.6B），fp16 safetensors |
| `tencent/Hunyuan3D-2/hunyuan3d-dit-v2-0/` | 4.9 GB | 形状 2.0（1.1B），fp16 safetensors |
| `tencent/Hunyuan3D-2/hunyuan3d-paint-v2-0-turbo/` | 10.3 GB | Paint 多视角扩散（turbo） |
| `tencent/Hunyuan3D-2/hunyuan3d-delight-v2-0/` | 4.3 GB | Paint 去光照 |

- 每个形状检查点里自带 VAE 和 DINOv2 条件编码器，不需要单独的 `vae-*` 目录。
- Paint 的 UNet 加载器（`hunyuanpaint/unet/modules.py`）只读 `diffusion_pytorch_model.bin`，所以拷的是 .bin（7.3 GB），没拷同目录的 .safetensors。
- 容器设 `HY3DGEN_MODELS=/opt/ml/model`，hy3dgen 的加载器直接在本地找到这些目录，不访问 HF（另外设了 `HF_HUB_OFFLINE=1`）。
- 背景去除用 rembg 的 u2net，`u2net.onnx` 在构建时下载进镜像（核对 md5），工作机不需要访问 GitHub。

## 4. 镜像

`evp/mesh:<tag>`（ECR 仓库 2026-11-25 已建，`scanOnPush`）。CUDA 12.8.1 + cuDNN devel、Ubuntu 22.04、Python 3.10、torch 2.7.1+cu128。固定的版本见 `requirements.txt`，其中：

- `diffusers==0.31.0`（新版会调用 torch 2.6+ 的 API）、`huggingface-hub==0.28.1`、`transformers==4.48.3`（hy3dgen 要求 ≥ 4.48）。
- `xatlas`、`rembg`、`onnxruntime`、`omegaconf`、`pygltflib`、`pymeshlab`、`trimesh`。
- Paint 的两个扩展在镜像里编译：`custom_rasterizer`（CUDA，`TORCH_CUDA_ARCH_LIST="8.9;12.0"`，覆盖 L40S 和 Blackwell RTX PRO）和 `mesh_processor`（C++，`build_ext --inplace`，不然渲染器会退回到慢的纯 Python 版本，导入检查会拦下这种情况）。
- `HF_HUB_DISABLE_SYMLINKS=1`。

依赖能否解析已在本机用 `pip install --dry-run --platform manylinux_x86_64 --python-version 3.10` 检查过（78 个包，torch 来自 cu128 索引）。改过的版本：`pygltflib` 1.16.3 只有源码包，改成 1.16.5。

适配层：

- 启动时先跑导入检查（失败就退出，worker 随即关机），然后在后台加载 `MESH_PRELOAD`（默认就是默认模型）和 rembg，加载完 `/ping` 才返回 200。另一个形状模型和 Paint 在第一次用到时加载，之后常驻（三个加起来在 32 GB 卡上也放得下）。
- 处理：解码图片 → 没有透明通道时用 rembg 去背景 → 形状模型（`octree_resolution`、`steps`、`guidance`、`seed`）→ `FloaterRemover`、`DegenerateFaceRemover`、`FaceReducer` 减到 `max_faces` → 可选 Paint → 导出 GLB。
- 贴图时先减到最多 4 万面再上色：Paint 的 UV 展开（xatlas）和烘焙在 CPU 上跑，在很密的网格上会卡住（在另一个项目实测过 100 万面卡了 9 小时以上），Hunyuan 官方也是先减到约 4 万面。
- 响应：`{"glb_b64", "seconds", "meta": {"model", "render_s", "faces", "vertices", "raw_faces", "textured", "peak_vram_gb", "torch_peak_gb", "gpu", "shape_s", "post_s", "paint_s", "rembg_s", "load_s", ...}}`。`peak_vram_gb` 用 nvml 采样（含 CUDA 上下文和缓存），`torch_peak_gb` 是 torch 自己的峰值。

## 5. 实测

**状态（2026-11-25 03:05 UTC）：还没跑。** 工作机角色 `evp-gpu-worker` 没有 `jobs/mesh-ec2/*` 和 `evp/mesh` 的权限，构建机角色 `evp-video-builder` 也不能推送 `evp/mesh`，本机角色不能改 IAM（第 7 节的命令要用户用管理员身份跑）。跑完后按下面的表填写，并把 `mesh.json` 里的 `default_model`、`capacity` 顺序、`render_s`、`cold_min` 改成实测值。

复现：

```bash
python3 infra/mesh/builder.py launch            # 约 20-30 分钟；记下输出里的 image
python3 infra/mesh/builder.py status <run>      # 看 status.json：build=0
# 把 infra/gpu_ec2/modules/mesh.json 的 "image" 改成上面的 tag
python3 infra/mesh/bench.py --cases 2mini,2,2mini+tex --stop   # 从 0 实例；默认先用图像模块出「一只坐着的猫」的概念图
infra/mesh/preview/render.sh data/mesh_smoke/2mini.glb docs/media/previs/mesh_cat_2mini.jpg clay
```

| 用例 | 机型 | 冷启动 | 生成秒数 | 面数（原始 → 减面后） | 峰值显存 |
|---|---|---|---|---|---|
| 2mini，只出几何 | | | | | |
| 2.0，只出几何 | | | | | |
| 2mini + 贴图 | | | | | |

`mesh.json` 现在的值是**暂定**的：默认模型 2mini（更小、更快，白模只看轮廓），容量顺序按价格：按需 g6e.2xlarge（$2.24/小时）→ g7.2xlarge（$2.52）→ g7e.2xlarge（$3.36），然后 Spot g7.2xlarge（约 $0.51）→ g6e.2xlarge（约 $2.07）→ g7e.2xlarge（约 $2.35～2.49），每种两个可用区（2026-11-25 的价目表和 Spot 价格）。三种卡（48 GB L40S、32 GB RTX PRO 4500、96 GB RTX PRO 6000）显存都远大于形状模型需要的量，按需求只看价格和容量。

## 6. 工作室接口

`POST /api/assets/model/image3d`，JSON：

```json
{"prompt": "一只坐着的猫", "target": {"axis": "h", "metres": 0.3},
 "model": "", "texture": false, "seed": 42, "name": "猫", "project_id": null}
```

- `prompt` 和 `image_asset`（素材库里的图片 id）只能给一个；`target.axis` 是 `w / h / d / longest`，`metres` 在 0.001–1000 之间。
- 返回 202 `{"task_id", "status": "queued", ...}`。任务状态 `GET /api/mesh/tasks/{id}`（`queued|running|done|failed`、`step`、`notes`、完成后的 `asset_id` 和 `mesh_meta`），文件 `GET /api/mesh/tasks/{id}/concept|input|raw`，失败重试 `POST /api/mesh/tasks/{id}/retry`（已有的概念图和原始 GLB 直接复用），列表 `GET /api/mesh/tasks`，GPU 状态 `GET /api/mesh/status`。
- 输入图片在工作室端检查并重新编码成 PNG（png / jpg / webp，短边 ≥ 128，长边 ≤ 8192，≤ 20 MB，长边缩到 1024，保留透明通道：带透明通道的图在工作机上跳过去背景）。
- 规范化通过 `mesh_api.set_normalize(fn)` 注入，默认调 A 的 `app.previs.assets_build.normalize(raw_glb, target, source, name, project_id)`。`source` 写 `{"type": "image3d", "model", "seed", "target", "prompt" 或 "image_asset"}`（对应 `types.ts` 的 `ModelMeta.source`）。

## 7. IAM（要用户用管理员身份跑）

本模块加了两条内联策略，不改已有的策略。CloudShell 里在仓库根目录跑：

```bash
aws iam put-role-policy --role-name evp-gpu-worker --policy-name evp-gpu-worker-mesh \
  --policy-document file://infra/mesh/iam/worker-mesh.json
aws iam put-role-policy --role-name evp-video-builder --policy-name evp-builder-mesh \
  --policy-document file://infra/mesh/iam/builder-mesh.json
aws iam get-role-policy --role-name evp-gpu-worker --policy-name evp-gpu-worker-mesh --query PolicyName --output text
aws iam get-role-policy --role-name evp-video-builder --policy-name evp-builder-mesh --query PolicyName --output text
```

`infra/gpu_ec2/grant_worker_role.sh` 也加上了 mesh 的几行（工作机的 `jobs/mesh-ec2/*`、`evp/mesh` 拉取，构建机的 `evp-builder-mesh`），以后整套重建时一起生效。权重读取（`weights/*`）、ECR 登录和 PassRole 已有，不用再加。

## 8. 成本

- 权重 23 GB S3 标准存储约 $0.54/月；镜像约 10 GB 约 $1/月。
- 一次使用：冷启动 + 几次生成 + 闲置 15 分钟，约 30 分钟。按需 g6e.2xlarge 约 $1.1，Spot g7.2xlarge 约 $0.25（暂估，等实测）。
- 构建一次镜像：c7i.4xlarge（$0.71/小时）约 30 分钟，约 $0.4。
