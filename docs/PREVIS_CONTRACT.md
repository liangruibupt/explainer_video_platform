# 3D 白模预演（previs）：接口约定

分支 `previs/p0-contract`（P0）。这份文件固定并行各部分（A–E）和集成之间的接口，只能由集成方修改。需要改的部分写进你的报告，不要自己改这份文件和 `server/app/previs/js/core/`。

## 1. 目标和已定的决定

讲解视频流水线在「分镜」和「出片」之间缺两步，这一版补上：

1. **静态资产**：带真实尺寸（米）的 3D 模型，进素材库，类型 `model`（GLB）。
2. **白模预演**：把资产摆进布景，按镜头驱动相机路径和物体动作，时间点对到旁白的字，每个镜头渲染成独立片段，再剪成一条多场景成片。

决定：

- 白模是几何、比例和时间的唯一依据。相机、动作和卡点都写成 JSON，由程序确定地算出每一帧，同样的输入总是得到同样的画面。模型只写 JSON，不直接写渲染代码。
- 渲染用现有的 Remotion 加 `@remotion/three`（React Three Fiber），在本机 CPU 上用 SwiftShader 软件 WebGL 渲染，不用 GPU，也不用 Blender。实测见第 10 节。
- 资产有三个来源：**参数化建模**（Opus 写图元规格，默认）、**图生 3D**（Qwen-Image 出概念图，Hunyuan3D-2 生成网格，第一版就做）、**上传 GLB**。三者都输出同样的 GLB + `meta.json`。
- 一个分镜段 = 一个镜头（v1）。段内要换角度时，相机关键帧写 `cut: true`。镜头长度由配音决定（`durations.json`），和其他渲染方式一样。
- 定位是讲解视频：机械、建筑、过程演示。v1 不做角色骨骼动画。
- 成片就是白模/黏土风格。可选的 AI 质感化（白模首尾帧 → 图像风格化 → H3 fl2va）不在这一版。

## 2. 在流水线里的位置

新的渲染方式 `renderer="previs3d"`（和 `html`、`remotion` 并列）。顺序：

```
storyboard → previs_plan（C）→ 资产构建（A / E，和配音并行）→ narrate → mix → captions（words.json）
  → compile_shots（B，锚点换算 + 检查）→ 写 Remotion 工程（B）→ 每个镜头单独渲染 shots/sNN.mp4（B）
  → Main 合成成片 final.mp4（字幕、配音、音效同 remotion 渲染方式）→ compose
```

镜头编译必须在 `captions` 之后，因为锚点要用 `words.json` 的逐字时间。

## 3. 单位、坐标和时间

- 长度单位米。右手系，+Y 向上，地面 y = 0。资产正面朝 +Z（和 glTF 一致）。
- 作者写的文件里角度是**度**，编译后的文件用弧度。
- 写作时时间用秒，编译后用帧，fps 30。镜头内的帧从 0 开始；`words.json` 的时间是**成片时间**，编译时减去这一段的起点（`durations.json` 累加）。

## 4. 资产

### 4.1 参数化资产规格 `asset.json`

TypeScript 定义在 `server/app/previs/js/core/types.ts` 的 `AssetSpec`（固定）。完整示例：`server/app/previs/examples/four_stroke/plan.json` 里的 `four_stroke`（单缸四冲程发动机，13 个部件、1 个自由度、5 个驱动量）。

- `params`：命名参数，带 `value`、范围和中文标签。部件尺寸、位置都可以写成参数表达式，例如 `"bore/2 + 0.02"`。
- `parts`：图元 `box cylinder tube cone sphere capsule torus extrude lathe group`，可选 `array`（直线或环形阵列）。每个部件的 `id` 成为 GLB 里的节点名。部件可以有父部件。
- `dims`：声明的整体尺寸（静止姿态），构建后和实际包围盒比，误差默认不超过 2%。
- `anchors`：命名点，镜头可以看向它、在上面挂标注。可以是部件 id、坐标，或 `{part, at}`（跟着部件动）。
- 颜色用调色板槽位 `clay accent1..accent4 glass` 或 `#rrggbb`。槽位到颜色的映射由渲染器决定（B），所以整片配色一致。
- v1 没有布尔运算。要看内部用镜头的 `section`（剖切平面）；空心件用 `tube`。

### 4.2 表达式（`core/expr.ts`，固定，已测试）

数字、名字（参数、自由度、驱动量、`pi`）、`+ - * / ^`、括号、函数。三角函数输入输出都是**度**：`sin cos tan asin acos atan atan2 sqrt abs min max clamp floor ceil round mod`。没有 eval；未知名字、未知函数、参数个数不对、结果不是有限数都会抛 `ExprError`。

### 4.3 自由度、驱动量和关节（`core/rig.ts`，固定，已测试）

- `dofs`：镜头能动的量（hinge 用度，slide 用米），带范围和默认值。
- `drivers`：按顺序求值的派生量，用来保证联动一致，例如活塞高度和连杆角度都由曲轴转角算出。示例里的驱动量：`piston_y`、`rod_ang`、四冲程的 `intake_lift` / `exhaust_lift`（`mod(crank, 720)` 分出进气、排气区间）。
- `joint`：`hinge` 绕父坐标系里的轴转（度），`slide` 沿父坐标系里的轴平移（米），值 = 驱动它的量 × `scale`。
- `dofValues(rig, pose)` 和 `partTransforms(rig, pose)` 是构建器、镜头编译器和渲染器**唯一**的姿态计算，三处结果逐位一致。

### 4.4 模型资产在素材库里的存法

- `data/assets/<id>/file.glb` + `meta.json` + `thumb.jpg`（转台第一帧）+ `turntable.mp4`（4 秒 360°，720p）。参数化资产另存 `spec.json`。
- `store.KINDS` 加 `"model"`，`FORMATS` 加 `"glb": ("model", ("glb",), "model/gltf-binary")`，按文件头 `glTF` 识别。`MAX_BYTES` 对 model 也是 200 MB。
- `meta.json` 除了现有字段，再加 `ModelMeta`（`types.ts`）：`bbox`、`size`、`tris`、`parts`（每个部件的静止包围盒）、`rig`（`dofs`、`drivers`、`joints`、`params`、`rest`）、`anchors`、`anchor_parts`、`source`。
- GLB 规则：glTF 2.0 二进制，单位米，节点名 = 部件 id，每个部件一个 mesh（或 group 节点）。部件的静止姿态写在 `meta.rig.rest` 里，GLB 里的节点变换也是静止姿态。材质是 `MeshStandardMaterial` 能读的 PBR，颜色槽位写在材质的 `extras.evp_slot`。v1 没有贴图（图生 3D 的贴图版本见 4.6）。

### 4.5 构建、规范化和校验（A）

Node 命令行，所有部分都通过它调用：`node server/app/previs/js/dist/previs.mjs <命令>`（用 `server/app/previs/js/build.sh` 由 esbuild 打包；Python 包装在 `server/app/previs/assets_build.py`）：

| 命令 | 输入 → 输出 |
|---|---|
| `build-asset <spec.json> <out.glb> <meta.json>` | 规格 → GLB + meta；退出码 0 = 成功，2 = 校验失败（错误写到标准输出，一行一个 JSON `CheckIssue`） |
| `normalize-glb <in.glb> <out.glb> <meta.json> --target <axis>:<metres> [--max-tris N]` | 任意 GLB（图生 3D、上传）→ 按目标尺寸等比缩放，底面落到 y = 0，水平居中，减面到 `max_tris`（默认 60000），整体一个部件 `body` |
| `validate-asset <glb> <meta.json>` | 只校验 |
| `turntable <glb> <meta.json> <out_dir>` | `thumb.jpg` + `turntable.mp4`（经 B 的渲染器，第 9 节） |

资产校验码：`expr_error`、`unknown_parent`、`cycle`、`duplicate_id`、`bad_shape_param`（半径 ≤ 0、内径 ≥ 外径等）、`dims_mismatch`（超过 `tol`）、`below_ground`（静止姿态最低点 < −1 mm）、`floating`（最低点 > 1 mm）、`dof_range`（在自由度范围内的 9 个采样姿态下，任何驱动量出现 NaN 或部件飞出 `dims` 的 3 倍范围）、`too_many_tris`、`bad_anchor`、`nan`。

Python：`assets_build.build(spec: dict, project_id=None) -> dict`（返回素材库记录）、`assets_build.normalize(raw_glb: Path, target: dict, source: dict, name: str, project_id=None) -> dict`。

### 4.6 图生 3D GPU 模块（E）

- 模块 `mesh`，配置 `infra/gpu_ec2/modules/mesh.json`，格式和 `image.json` 一样。区域 us-east-1。Hunyuan3D 2.0 社区许可证不适用于欧盟、英国、韩国，美国区可以用。
- 模型：Hunyuan3D-2（shape：2mini 或 2.0，由 E 实测后选），可选 Paint 贴图。默认只出几何（白模不需要贴图）。
- 镜像 `evp/mesh:<tag>`，CUDA 12.8（能跑 Blackwell g7e），权重不进镜像，放 `s3://evp-models-747411437379-us-east-1/weights/hunyuan3d-2/<sha>/`。容器 8080 端口提供 `/ping`、`/invocations`，和其他模块一样。`jobs_prefix` 是 `jobs/mesh-ec2/`，`name` 是 `evp-mesh-worker`，标签 project=explainer-video、module=mesh，闲置 900 秒、开机最多 4 小时。
- 机型由 E 实测后排序（候选 g6e.2xlarge、g7e.2xlarge、g7.2xlarge；先按需再 Spot，每种两个可用区）。
- IAM：`evp-gpu-worker` 需要 `jobs/mesh-ec2/*` 的 Get/Put/Delete 和 `evp/mesh` 的 ECR 拉取权限，加进 `infra/gpu_ec2/grant_worker_role.sh`。这个脚本要用户自己用管理员身份运行（本机角色不能改 IAM）。
- 已知依赖坑（之前在另一个项目实测过）：diffusers 要固定在 0.31.0（新版会调用 torch 2.6+ 的 API），huggingface-hub 用 0.28.1；需要 xatlas、rembg、onnxruntime；贴图另外需要 omegaconf、pygltflib 和两个 CUDA 扩展。HF 下载的文件是软链接，模型加载器拒绝软链接，所以设 `HF_HUB_DISABLE_SYMLINKS=1`。

请求（`GpuEC2.submit`）：

```
{"model": "hunyuan3d-2mini" | "hunyuan3d-2",   # 默认用 E 实测选出的那个
 "image_b64": "<png>",            # 单个物体，干净背景
 "remove_bg": true,
 "texture": false,                # true = 再跑 Paint
 "octree_resolution": 256, "steps": 30, "guidance": 5.0, "seed": 42,
 "max_faces": 60000}
```

响应：`{"ok": true, "files": {"glb_b64": [<s3 key>]}, "meta": {"model", "render_s", "faces", "textured", "peak_vram_gb"}}`。

工作室端 `server/app/mesh.py`：`status()`（和 `image.status()` 同样的结构）、`generate(image: bytes, model="", texture=False, seed=42, **kw) -> bytes`（GLB 原始文件，没有规范化）。`asset.image3d` 节点和 `POST /api/assets/model/image3d` 先用 `image.generate()` 按提示词出概念图（提示词模板：「单个物体，纯白背景，三分之二侧视角，完整不裁切，无投影，画面中没有任何文字」），再 `mesh.generate()`，最后 `assets_build.normalize()`。

## 5. 规划：分镜 → 资产、布景、镜头（C）

一个任务的规划写成 `PrevisPlan`（`types.ts`），存到 `jobs/<id>/previs/plan.json`。

- `previs_plan(scenes, topic, ratio, library_assets=[]) -> PrevisPlan`，在 `server/app/previs/plan.py`。Opus 5.5 拿到分镜（每段的 `narration`）、这份约定的第 3–7 节、示例 `four_stroke`、可用的素材库模型（id、名字、尺寸、自由度、锚点）。
- 静态校验 `plan.validate(plan, scenes) -> list[CheckIssue]`（纯 Python，不渲染）：JSON 结构、id 引用（资产、布景、物体、锚点、标记点、自由度）、每个分镜段正好一个镜头、每个 `word` 锚点在这一段的旁白里能找到（含 `occurrence`）、每个镜头至少两个相机关键帧、`scale` 不是 1 时警告。不通过就带着错误清单重写，最多 3 轮，做法同 `workflows/agent.py`。
- 参数化资产构建失败（A 的退出码 2）时，把 `CheckIssue` 发回去重写这个资产，最多 2 轮。
- 外形检查：构建成功后渲染 4 张转台静帧，Opus 视觉调用判断「像不像 X、比例对不对」，有明显问题重写一次。结果记在 `plan_check.json`。
- 资产 `kind`：机械、建筑、示意结构用 `parametric`；有机形体（动物、雕塑、植物）用 `image3d`，必须写 `target`（例如 `{"axis": "h", "metres": 0.3}`）；素材库里已有的用 `library`。

## 6. 布景 `SetSpec`

见 `types.ts`。物体引用素材库 id（`asset_...`），或同一规划里的资产（`@<spec id>`，构建后由集成换成素材库 id）。`scale` 只能是统一缩放，默认 1。`marks` 是命名点：坐标，或 `"物体"`、`"物体.锚点"`。灯光预设 `studio`（主光 + 补光 + 半球光，开阴影）、`top`、`dramatic`，由 B 实现。

## 7. 镜头 `ShotSpec` 和时间锚点

见 `types.ts`，示例里有三个镜头（`s01` 环绕介绍、`s02` 剖切看进气和压缩、`s03` 点火处切近景）。

- **点**（`PointRef`）：`[x,y,z]`、标记点名、`"物体"`（物体原点）、`"物体.锚点"`。跟着部件动的锚点每帧重新算。
- **相机关键帧**：`pos` 或 `orbit {around, r, az, el}`（度；az 从 +Z 向 +X 转），`look`，`lens_mm`（全画幅等效，默认 35，竖直视角按 24 mm 高的画幅算），`roll`，`ease`（进入这一帧的缓动），`cut`（硬切，不从上一帧插值）。`path` 默认 `catmull`（位置用 Catmull-Rom 中心参数化，看向点和焦距线性插值，都经过缓动）。
- **动作**：`pose`（把自由度在 `at` 到 `until` 间插值到 `to`）、`spin`（匀速转）、`move`、`show`/`hide`、`highlight`、`label`、`dimension`（尺寸标注线）、`section`（剖切平面，只切 `targets`）、`explode`（按部件到物体中心的方向分开）。同一个自由度在同一帧被两个动作控制 → `dof_conflict` 错误。
- **时间锚点** `TimeAnchor`，只能写一种：
  - `t`（镜头内秒）、`frame`（镜头内帧）、`mark: start|end`；
  - `word`：在这一段旁白里找这串字（不含标点，`occurrence` 从 1 开始），`edge: start` 取第一个字的 `start`，`end` 取最后一个字的 `end`，减去段起点，再加 `offset` 秒，四舍五入到帧，夹到 `[0, frames-1]`。
  - 找不到 → `anchor_not_found` 错误；被夹住 → `anchor_clamped` 警告。

## 8. 镜头编译和检查（B）

`node .../previs.mjs compile-shot --plan plan.json --shot sNN --assets <asset_dir_map.json> --words words.json --durations durations.json --size 1920x1080 --out resolved.json`，再 `check-shot resolved.json --assets ... > report.json`。Python 包装：`server/app/previs/shots.py`：`compile_all(job_dir) -> dict`（每个镜头的 `ResolvedShot` 路径和检查报告）。

`ResolvedShot`（`types.ts`）是渲染器的唯一输入：每一帧的相机 `[px,py,pz, lx,ly,lz, vfov, roll]`、每个物体每一帧的变换和每个部件的局部变换（由 `rig.partTransforms` 算出）、标注、剖切、所有锚点换算出来的帧号。渲染器不做任何时间或姿态计算，只按帧读数组。数组里不变的量只存一个元素。

检查（每帧都查，CPU）：

| code | 级别 | 规则 |
|---|---|---|
| `camera_inside` | error | 相机位置在任一部件当前姿态的包围盒里（外扩 2 cm） |
| `look_out_of_frame` | error | 看向点投影到画面外 |
| `subject_out_of_frame` | warn | 动作或标注的目标在它生效期间有超过 30% 的帧中心不在画面里（安全框 5%） |
| `subject_occluded` | warn | 同上期间，从相机到目标中心的射线先碰到别的部件的包围盒，帧数超过 50% |
| `camera_jump` | error | 非 `cut` 帧之间相机移动超过场景尺寸的 15%，或转向超过 25° |
| `fast_motion` | warn | 相机角速度超过 90°/秒 |
| `axis_crossing` | warn | 同一布景的相邻两个镜头，相机分别在主体连线的两侧（越轴） |
| `anchor_not_found`、`anchor_clamped` | error / warn | 见第 7 节 |
| `dof_conflict`、`unknown_dof`、`unknown_target`、`unknown_point` | error | |
| `cut_too_close` | warn | 两次硬切间隔小于 1 秒 |

有 error 的镜头不渲染；C 的规划循环会把报告发回去改（最多 2 轮），还不行就用一个自动的环绕镜头代替，并在报告里写明。

另外每个镜头输出一张**俯视图** `previs/sNN.plan.png`：从上往下画布景（部件包围盒）、相机路径（颜色表示时间）、关键帧编号和每个锚点的位置，供审核页使用。

## 9. 渲染和 Remotion 工程（B）

工程还是 `jobs/<id>/remotion/`（REMOTION_CONTRACT 第 2 节），previs3d 多出这些：

```
src/lib/previs/PrevisShot.tsx   B：读 ResolvedShot，渲染一个镜头（固定文件，从模板复制）
src/lib/previs/*.tsx            B：灯光、材质、地面、标注、剖切等
src/scenes/sNN.tsx              B 生成：DEFAULTS（标注文字、配色、是否显示网格和阴影）+ SCHEMA + Scene，
                                Scene 只是 <PrevisShot data={...} {...props}/>，所以动效编辑器可以改这些参数
src/data/previs/sNN.json        ResolvedShot
public/previs/<asset_id>.glb    复制，不能用软链接（Remotion 的静态服务器拒绝软链接）
```

- `Root.tsx` 除了 `Main`，再为每个 previs 段注册一个 `Shot-sNN` 合成（同样的 fps 和尺寸，只有这一段，不带字幕和配音），用来渲染独立片段 `jobs/<id>/shots/sNN.mp4`。`Main` 和其他 Remotion 任务一样：所有段 + 字幕 + 配音 + 音效。镜头之间默认硬切；`style.fade_in`/`fade_out` 在镜头内做淡入淡出。
- GLB 用 `useLoader(GLTFLoader, staticFile(...))` 加载，加载完之前要 `delayRender`，确保第一帧就有模型。渲染器把部件的 `MeshStandardMaterial` 换成黏土材质：哑光，颜色按 `evp_slot` 查调色板。调色板 `clay #c9c4bb`、`accent1 #d98c5f`、`accent2 #6f9fd8`、`accent3 #8cc084`、`accent4 #d8c46f`、`glass` 是半透明 `#a9b8c6`。
- 剖切用 three.js 的 `clippingPlanes`（局部剖切，只作用于 `targets`），剖面用 stencil 填实（做法同 math-visualizations 的 raup-shell 页面）。
- 标注和尺寸线用 HTML 叠在画布上（不在 WebGL 里），文字用 Noto Sans CJK SC，避开字幕区（下方 18%）。
- **GL 后端必须用 `swangle`**：`render.render()` 用 Remotion 默认值就能渲染 WebGL；`render.stills()` 和 `render.still()` 原来写死 `angle`，在本机建不出 WebGL 上下文，P0 已经给它们加了 `gl=` 参数（`comp=` 也是），previs 调用时传 `gl="swangle"`。默认值不变，其他渲染方式不受影响。
- 每个镜头渲染 5 张静帧（同 Remotion 的版面检查），交给现有的 Opus 版面检查；这里只记录，不自动重写（v1）。
- 共享安装：`infra/previs/setup.sh` 把 `infra/previs/packages.*.txt` 里的包（固定版本）装进 `$EVP_TOOLS/remotion-base`。每个部分在自己的 `packages.<部分>.txt` 里加包，不要改别人的文件。P0 已经装好 `@remotion/three 4.0.484`、`three 0.178.0`、`@react-three/fiber 9.3.0`、`@types/three 0.178.0`。

## 10. 实测渲染速度（2026-11-25，本机）

机器：aarch64、4 核、30 GB 内存、没有 GPU。Chromium 是 Remotion 自带的 chrome-headless-shell，`--gl=swangle`（SwiftShader）。基准测试工程在 `infra/previs/bench/`，`run.sh` 复现。场景是一台类似单缸发动机的白模，约 30 个网格加地面网格，2048 阴影贴图，相机推拉加环绕，活塞和曲轴每帧都在动。每个用例 150 帧（5 秒），时间**包含约 5 秒的打包**。

| 用例 | 用时 | 帧/秒 |
|---|---|---|
| 720p，1 台，阴影，并发 2 | 17.7 秒 | 8.5 |
| 1080p，1 台，阴影，并发 2 | 23.3 秒 | 6.4 |
| 1080p，1 台，无阴影，并发 2 | 15.3 秒 | 9.8 |
| 1080p，4 台（约 120 个网格），阴影，并发 2 | 30.4 秒 | 4.9 |
| 同上，并发 4 | 31.7 秒 | 4.7 |
| 1080p，16 台（约 500 个网格），阴影，并发 2 | 32.3 秒 | 4.6 |

结论：

- 1080p 带阴影大约每秒 5–6 帧。一条 60 秒（1800 帧）的成片约 5–6 分钟，单个 10 秒镜头约 1 分钟。审核和成片都可以直接用 1080p，不需要降成 720p/15fps。
- 并发 4 不比并发 2 快（可能是 SwiftShader 自己就用多线程，4 个核已经占满；没有单独验证），保持 `EVP_REMOTION_CONCURRENCY=2`。
- 网格从 30 个加到 500 个只慢了约 30%，主要开销在每帧的像素和阴影上，不在网格数。所以图生 3D 的网格减到 6 万面以内就够了。
- 阴影大约占 1080p 用时的 35%。`style.shadows` 默认开，长片想快可以关。
- `--gl=angle`、`swiftshader`、`vulkan`、`angle-egl` 都建不出 WebGL 上下文，只有 `swangle` 和默认值可以。

## 11. 工作流节点和接口

节点（各部分在自己的文件里用 `register(NODES)` 声明，集成时注册，做法同 `assets/node.py`）：

| 节点 | 部分 | 执行 | 输入 → 输出 | 参数 |
|---|---|---|---|---|
| `asset.build` | A | cpu | spec: json → model | — |
| `asset.image3d` | E | gpu:mesh（概念图用 gpu:image） | prompt: text、image: image（都可选）→ model | target_axis、target_metres、model、texture、seed |
| `agent.previs_plan` | C | agent | scenes: json → plan: json | ratio |
| `previs.render` | B | cpu | plan: json、job: json → video、shots: json | size、shadows |

端口类型加 `model`（值是素材库 id）。`validate.py` 的类型表里加 `model`，由集成改。

HTTP：

| 方法 | 路径 | 部分 | 作用 |
|---|---|---|---|
| POST | `/api/assets/model/build` | A | `{spec, project_id?}` → 素材库记录；校验失败 422 + `issues` |
| POST | `/api/assets/model/upload` | A | multipart GLB + `target_axis`、`target_metres` → 规范化后入库 |
| POST | `/api/assets/model/image3d` | E | `{prompt? , image_asset?, target, model?, texture?, seed?}` → `{task_id}`（后台任务，进任务中心） |
| POST | `/api/assets/model/plan` | C | `{description}` → `{spec, issues, rounds}`（一句话写参数化规格，不入库） |
| GET | `/api/assets/{id}/file`、`/thumb`、`/turntable` | A | 现有的文件路由加 GLB 和转台视频 |
| GET | `/api/jobs/{id}/previs` | B | `{plan, shots: [{scene, resolved_url, report, stills: [url], plan_png, clip_url, status}]}` |
| POST | `/api/jobs/{id}/previs/{scene}/render` | B | 只重编译、重渲染这一个镜头 |
| PUT | `/api/jobs/{id}/previs/{scene}` | B | `{shot}` 替换一个镜头的 ShotSpec（审核页和 Opus 助手用），返回新的检查报告 |

路由分别放在 `server/app/assets/model_api.py`（A）、`server/app/mesh_api.py`（E）、`server/app/previs/plan_api.py`（C）、`server/app/previs/api.py`（B），都导出 `router`；`main.py` 的 include 由集成加。

## 12. 界面（D）

- **素材库**：模型卡片显示转台缩略图，点开是 three.js 查看器（旋转、缩放、网格地面、尺寸标注、自由度滑块，按 `meta.rig` 生成，调用同一个 `core/rig.ts`）。「新建 3D 资产」有三个入口：一句话（参数化，调 `/model/plan` 后预览，再 `/model/build`）、图生 3D（提示词或选一张图，填目标尺寸，显示 GPU 状态和预计时间）、上传 GLB。
- **作品页**：previs3d 任务多一个「镜头」标签：每个镜头一行，显示 5 张静帧、俯视路径图、检查报告（error 红、warn 黄）、独立片段播放器、「重渲这个镜头」按钮。
- **工作台**：渲染方式加「3D 白模预演」。
- 先用 mock 数据按第 11 节的接口写，不依赖后端完成。`web/src/pages/` 新页面加 `ModelViewer.jsx` 组件；`core/rig.ts` 用 Vite 别名直接引用（`@previs-core` → `server/app/previs/js/core`）。

## 13. 分工和文件归属

| 部分 | 内容 | 拥有的文件 |
|---|---|---|
| P0（已完成） | 这份约定、`core/`（types、expr、rig 和测试）、示例 `four_stroke`、基准测试、`setup.sh`、`render.py` 的 `gl`/`comp` 参数 | `docs/PREVIS_CONTRACT.md`、`server/app/previs/js/core/**`、`server/app/previs/examples/**`、`infra/previs/{setup.sh,packages.core.txt,bench/**}` |
| A 资产 | 规格构建、规范化、校验、转台图（调 B 的渲染器；B 没完成时先用一个最小的 R3F 转台合成）、素材库 `model` 类型、`asset.build` 节点、模型 HTTP 接口 | `server/app/previs/js/asset/**`、`server/app/previs/js/cli.ts`（命令分发；B 的命令以 `import` 接入，冲突由集成解决）、`server/app/previs/assets_build.py`、`server/app/assets/store.py`、`server/app/assets/model_api.py`、`server/app/assets/model_node.py`、`server/tests/test_previs_asset*.py`、`infra/previs/packages.asset.txt` |
| B 镜头和渲染 | 镜头编译、检查、俯视图、Remotion 渲染器（PrevisShot、材质、灯光、剖切、标注）、场景模块生成、`Shot-sNN` 合成、`shots.py`、previs HTTP 接口、`previs.render` 节点 | `server/app/previs/js/shot/**`、`server/app/previs/js/render/**`、`server/app/remotion/template/src/lib/previs/**`、`server/app/previs/shots.py`、`server/app/previs/project.py`、`server/app/previs/api.py`、`server/app/previs/node.py`、`server/tests/test_previs_shot*.py`、`infra/previs/packages.render.txt`；`template/src/Root.tsx` 的改动只限于注册 `Shot-*` 合成 |
| C 规划 | `previs_plan`、静态校验、重写循环、外形检查、`/model/plan`、`agent.previs_plan` 节点 | `server/app/previs/plan.py`、`server/app/previs/plan_api.py`、`server/app/previs/plan_node.py`、`server/app/previs/prompts/**`、`server/tests/test_previs_plan*.py` |
| D 界面 | 第 12 节 | `web/src/pages/AssetsPage.jsx` 的模型部分、`web/src/components/ModelViewer.jsx`、`web/src/pages/previs/**`、`JobPage.jsx` 的「镜头」标签、`Workbench.jsx` 的渲染方式选项、`web/vite.config.js` 的别名 |
| E 图生 3D | 第 4.6 节：容器、模块配置、实测和选型、`mesh.py`、`mesh_api.py`、`asset.image3d` 节点 | `infra/mesh/**`、`infra/gpu_ec2/modules/mesh.json`、`infra/gpu_ec2/grant_worker_role.sh`（只加 mesh 那几行）、`server/app/mesh.py`、`server/app/mesh_api.py`、`server/app/mesh_node.py`、`server/tests/test_mesh*.py`、`docs/MESH.md` |
| 集成 | 合并 A–E、`pipeline.py` 接入 `renderer="previs3d"`、节点注册、`validate.py` 的 `model` 类型、`main.py` 路由、动效编辑器打开 previs 工程、四冲程发动机端到端示例（真配音、真 Whisper）、README 功能列表、STUDIO.md、DESIGN.md | 其他所有文件 |

规则：每个部分在自己的 worktree 和分支上（`previs/a-asset` 等）开发，基于 `previs/p0-contract`；每完成一段有意义的工作就提交并推送；开 PR 但不合并。`server/app/previs/js/test.sh` 和 `server/tests` 的测试都要通过。

## 14. 验收（集成时）

用 `four_stroke` 示例的分镜，真实配音和字幕：

1. 三个镜头都编译通过，没有 error。
2. 同一台发动机在三个镜头里尺寸一致：`meta.size` 和 `dims` 的误差在 2% 以内，三个镜头的 `ResolvedShot` 用的是同一个资产 id。
3. 卡点：「进气」「压缩」「点火」「做功」「排气」这几个锚点换算出的帧，和 `words.json` 里对应字的时间差不超过 1 帧。在这几帧截静帧，人工看气门和火花塞的状态对不对。
4. 独立片段 `shots/s01..s03.mp4` 和成片 `final.mp4` 都能播放，成片长度 = `durations.json` 之和，有字幕、有配音。
5. 图生 3D：用一个有机形体（例如「一只坐着的猫，高 0.3 米」）从 0 开机跑通，入库后 `meta.size[1]` = 0.3 m ± 2%，能放进布景渲染。记录冷启动和生成时间。
6. 素材库能看到模型和转台预览；作品页的「镜头」标签能重渲单个镜头。

## 15. 修订记录（集成方）

### 2026-11-25，根据 A（[PR #40](https://github.com/liangruibupt/explainer_video_platform/pull/40)）和 D（[PR #39](https://github.com/liangruibupt/explainer_video_platform/pull/39)）的报告

1. **`size` 冲突**：素材库记录原来用 `size` 存文件字节数。对 `kind = "model"` 的记录，`size` 是包围盒尺寸 `[w,h,d]`（米，同 `ModelMeta`），字节数放在 `bytes`。其他类型的记录不变。界面按类型读。
2. **`meta.parts[].local`**：每个部件在自己坐标系里的包围盒（A 已实现）。B 的当前姿态检查用它乘上 `rig.partTransforms` 的变换，比用静止姿态的 `bbox` 准。
3. **`extras.evp_slot`** 可以是槽位名，也可以是 `#rrggbb`（部件写了十六进制颜色时）。渲染器两种都要接受。
4. **资产校验码**加 `bad_id`（id 不合规）和 `missing_part`（锚点或父部件引用了不存在的部件）。
5. **A 补的默认约定**：torus 默认平放（轴 y）；extrude 的倒角在轮廓内，声明尺寸是精确的；环形阵列平分角度；锚点写部件 id 时指向这个部件包围盒的中心。
6. **素材库接口**：`GET /api/assets?kind=model` 要接受 `model`（集成改 `assets/api.py`）；模型记录带 `turntable_url`。
7. **镜头状态**：`GET /api/jobs/{id}/previs` 里每个镜头的 `status` 取 `queued | running | done | failed | blocked`（`blocked` = 检查有 error 没有渲染）；`report` 是 `{"issues": CheckIssue[]}`。
8. **四冲程示例**：曲轴盘原来被不透明的曲轴箱挡住，曲轴箱改成半透明（opacity 0.45），缸盖 0.6。
9. **`core/expr.ts`** 修了 `tsc --strict` 下的 5 个类型错误，行为不变。
