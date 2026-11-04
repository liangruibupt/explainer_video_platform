# 工作流（阶段 3）

设计见 [DESIGN.md](DESIGN.md) 第 5、6 节。这一版实现了工作流格式、节点库、校验、带缓存和人工审核的编排器、Agent 生成工作流、AI 助手改图、三个模板和工作流页面（`/workflow`）。代码在 `server/app/workflows/`。

## 格式

```json
{"id": "wf_mv_from_one_line", "name": "一句话做 MV", "description": "...",
 "nodes": [{"id": "n1", "type": "input.text", "params": {"text": "..."}}],
 "edges": [{"from": "n1.text", "to": "n2.theme"}]}
```

- 节点 id 用字母开头的字母数字（`n1`、`n12`），端口写成 `节点.端口`。
- 保存的工作流在 `data/workflows/<id>.json`；模板在 `server/app/workflows/templates/*.json`（进 git）。
- 端口类型：`text`、`image`、`audio`、`video`、`json`。`any` 接什么都行；`review.human` 和 `qc.auto` 的输出是 `any`，校验时按接进来的类型往下传（审核一张图后只能接到要 image 的端口）。
- 节点之间传的值：文本是字符串，图像/音频/视频是文件的绝对路径（缓存目录里的文件）。

## 节点

每个节点类型在 `nodes.py` 里声明 `inputs`、`optional`（可以不连的输入）、`outputs`、`params`（`type`、`default`、`enum`、`min`/`max`、`required`、`required_unless_input`）、`runner` 和 `run(inputs, params, ctx)`。`GET /api/workflows/nodes` 返回这份声明，页面的参数表单和 Agent 读的是同一份。

| 节点 | 执行 | 输入 → 输出 | 做法 |
|---|---|---|---|
| `input.text` | cpu | → text | 参数 `text` |
| `agent.write` | agent | text（可选）→ text | 通用 Opus 5.5 调用，参数 `instruction`。没有联网检索节点，「先查资料」用它整理模型已知的资料 |
| `agent.lyrics` | agent | theme → lyrics, style | 写 YuE2 格式歌词（两段主歌两段副歌加尾声，约 1–2.5 分钟）和英文风格词 |
| `agent.image_prompt` | agent | idea → prompt | 写 Qwen-Image 提示词，结尾「画面中没有任何文字」 |
| `agent.video_prompt` | agent | idea → prompt | 写 MiniMax H3 英文镜头描述 |
| `agent.music_style` | agent | idea → style, lyrics | 纯音乐配方：风格以 `ambient, no drums, instrumental, no vocals` 结尾，歌词只有段落标签；可选「编排说明」经 `llm.arrangement_tags` 转成英文风格词 |
| `image.qwen` | gpu:image | prompt（可选）→ image | `image.generate()`，参数 ratio、mode、seed、steps |
| `video.h3` | gpu:video | prompt、first_frame（都可选）→ video | `video.generate()`；连了首帧图是 fl2va，否则 t2va；4–15 秒 |
| `music.yue2` | gpu:music | style、lyrics（都可选）→ audio | `music.generate()`；`instrumental` 开关换成纯音乐歌词和风格结尾 |
| `explainer.video` | cpu | topic → video, job | 把现有讲解视频任务作为子任务跑（`data/jobs/<id>`），参数同 `POST /api/jobs`（含插图、镜头、音乐）。用到 GPU 时走子任务自己的调用 |
| `audio.mix` | cpu | video, audio → video | ffmpeg，做法同 `mix_bgm.py`。`mode`：`replace` 音乐替换原音轨（响度归一到 −14 LUFS），`bed` 垫在原音轨下（−24 LUFS × 音量系数，sidechain 在人声处压低）。`fit`：`loop_video` 画面正放倒放循环到音乐长度（`max_seconds` 封顶，默认 90），`cut_audio` 音乐裁到画面长度。结尾淡出 |
| `review.human` | — | input → output | 暂停运行等审核，批准后原样往下传 |
| `qc.auto` | cpu | media → output, report | 查时长、短边分辨率、响度（ebur128）、可选「不能有人声」（faster-whisper 转写为空，同 `check_audio.py`）。不合格时节点失败并写明原因 |
| `output.save` | — | work → | 文件复制到 `data/wf_runs/<运行>/outputs/<节点>.<扩展名>`，记进运行的 `works` |

## 校验

`validate.validate(wf)` 返回错误列表，每条带 `code`、`message` 和 `node`/`port`/`edge`：

| code | 含义 |
|---|---|
| `format` | 不是对象、没有节点、没有名字 |
| `bad_id`、`duplicate_id`、`unknown_type` | 节点 id 或类型不对 |
| `bad_edge`、`bad_port` | 连线引用的节点或端口不存在（消息里列出这个节点有哪些端口） |
| `type_mismatch` | 端口类型不配 |
| `multiple_inputs` | 一个输入接了多条线 |
| `cycle` | 有环（包括自己连自己） |
| `missing_param`、`bad_param`、`unknown_param` | 必填参数为空、类型/取值范围/枚举不对、多了不认识的参数 |
| `missing_input` | 非可选输入没有连 |
| `module_unavailable` | GPU 节点用到的模块在 `infra/gpu_ec2/modules/` 里没有配置 |

## 编排器

`engine.py`，运行状态在 `data/wf_runs/<运行 id>/run.json`：每个节点的状态（pending、running、waiting_review、done、failed、cached）、开始/结束时间、用时、输出、错误、备注（例如「等待 GPU（3 分钟）」）。

- **调度**：一个驱动线程按拓扑序反复找「上游都完成了」的节点，每个节点一个线程，没有依赖的节点并行。GPU 节点按模块加锁：不同模块同时跑，同一模块排队（worker 一次只处理一个请求）。
- **提前唤醒 GPU**：一个节点开始真正执行（没命中缓存）时，后台对它下游还没跑的 GPU 节点所在模块调用 `ensure_running()`，让冷启动和上游的生成重叠。例如 MV 模板里图像生成时，视频 GPU 已经在开机。全部命中缓存的重跑不会开机。
- **缓存**：键 = sha256（节点类型 + 节点版本号 + 合并默认值后的参数 + 每个输入的哈希）。文件输入按内容哈希，其他按规范化 JSON 哈希；所以显式写出默认值不改变键，同一张图换个路径也命中。结果存到 `data/wf_cache/<键>/`（`outputs.json` 加文件）。命中时节点状态是 `cached`，不调模型也不调 GPU。`input.text`、`review.human`、`output.save` 不缓存（每次运行都要重新审核）。节点实现改了要加 `version`，旧缓存就不再命中。
- **人工审核**：运行到 `review.human` 时节点进入 `waiting_review`，其他分支照常跑完，运行状态变成「等待审核」。`POST /api/wf_runs/<id>/review {node_id, decision: approve|reject, note}`。批准后继续；打回则这个节点失败、运行失败，用户改参数后重新运行（上游走缓存）。
- **单独重跑**：`POST /api/wf_runs/<id>/retry {node_id}` 把这个节点和它下游的节点重置为 pending 再跑，上游的结果保留。
- **服务重启**：还在 running 的节点标成失败（可以单独重跑），等待审核的运行保持等待。
- **不在这一版**：候选数（一个节点出多个候选、审核时选一个）。现在要多个版本，就放多个生成节点、用不同 seed（Agent 生成「三张不同风格的头像」时就是这么做的）。

## Agent 生成工作流

`agent.py`。`POST /api/workflows/generate {prompt}`：Opus 5.5 拿到节点声明（类型、端口、参数 schema、说明）和规则（以 `input.text` 开头、生成媒体在 `output.save` 前加 `review.human`、画幅一致、尽量少用 GPU），返回工作流 JSON；后端校验，不通过就把错误列表发回去改，最多 3 轮。返回 `{workflow, errors, valid, rounds, levels}`。

`POST /api/workflows/edit {workflow, instruction}`（页面右侧「AI 助手」）走同一个循环，提示词要求保留原来的 id、参数和连线，只做用户要的改动。差异由服务端对比新旧工作流算出（新增/删除节点、参数改动、新增/删除连线），不靠模型自述。页面显示差异，用户点「应用改动」才替换。

## 页面

`/workflow`（原来的 `/advanced/workflow` 跳转过来）：快捷开始（模板）、「用一句话生成工作流」、图（节点按拓扑层排成列，连线是曲线，节点颜色表示状态，悬停连线看端口）、右侧节点检查器（按声明生成参数表单；运行后显示状态、用时、输出的图/视频/音频；等待审核的节点有批准/打回，失败的节点有单独重跑）、AI 助手、作品、运行记录（点开查看历史运行）。还没有拖拽编辑。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/workflows/nodes` | 节点声明 |
| GET | `/api/workflows/templates` | 模板 |
| GET / POST | `/api/workflows` | 已保存的工作流 / 保存 `{workflow}` |
| GET / DELETE | `/api/workflows/{id}` | 读（也能读模板）/ 删除 |
| POST | `/api/workflows/validate` | `{workflow}` → `{errors, valid, levels}` |
| POST | `/api/workflows/generate` | `{prompt}` → Agent 生成 |
| POST | `/api/workflows/edit` | `{workflow, instruction}` → 改后的工作流 + `diff` |
| POST | `/api/wf_runs` | `{workflow}` 开始运行（校验不通过返回 422 和错误列表） |
| GET | `/api/wf_runs`、`/api/wf_runs/{id}` | 运行列表 / 详情 |
| POST | `/api/wf_runs/{id}/review` | 审核 |
| POST | `/api/wf_runs/{id}/retry` | 单独重跑 |
| GET | `/api/wf_runs/{id}/file?node=&port=` 或 `&work=` | 节点输出文件 / 作品文件 |

## 模板

| 模板 | 结构 | 说明 |
|---|---|---|
| 一句话做 MV | input.text → agent.lyrics → music.yue2；input.text → agent.image_prompt → image.qwen →（首帧）video.h3 ← agent.video_prompt；video + 歌 → audio.mix → review.human → output.save | 拼接选「画面循环到歌曲长度」：5 秒镜头正放倒放循环，没有跳帧；歌曲完整保留（最多 90 秒，`max_seconds` 可改），结尾淡出。没选「把歌裁到固定长度」，因为 MV 的主体是歌 |
| 科普讲解视频（AI 插图 + AI 背景音乐） | input.text → explainer.video（插图 2，无音乐）；input.text → agent.music_style → music.yue2 → qc.auto（不能有人声）→ audio.mix（bed，音量 0.35）→ review.human → output.save | 没用 explainer.video 自带的 `music: ai`，而是把音乐拆成单独的节点：音乐有自己的缓存（换主题文案不变时不重新生成）、单独的质检和单独重跑，也能在图里看到用了什么风格词。混音参数和 `pipeline.py` 的 AI 音乐一致（−24 LUFS、0.35、人声处压低） |
| 封面图 | input.text → agent.image_prompt → image.qwen（9:16）→ review.human → output.save | |

## 实测（2026-11-04，8766 端口的第二个工作室，三个 GPU 都从 0 开始）

### a. 「一句话做 MV」端到端

输入「夏夜的海边，萤火虫和远处的灯塔」。运行 `20261104-082003-5b7b48`，从提交到可以审核 14.3 分钟（856 秒），批准后保存。

| 节点 | 开始（秒） | 用时（秒） | 备注 |
|---|---|---|---|
| n2 agent.lyrics | 0 | 6.5 | 两段主歌、两段副歌、尾声；风格「Mandarin folk pop, warm female vocal, acoustic guitar, soft piano...」 |
| n4 agent.image_prompt / n6 agent.video_prompt | 0 | 6.6 / 5.2 | 三个 Agent 节点并行 |
| n3 music.yue2 | 7 | 460 | 按需 g5.4xlarge，us-east-1，冷启动；歌 152 秒 |
| n5 image.qwen | 7 | 509 | Spot g7.2xlarge，us-east-1，冷启动；和音乐同时进行 |
| n7 video.h3（fl2va，首帧是 n5 的图） | 517 | 276 | Spot g7e.4xlarge，ap-south-1。这次视频 GPU 在 08:22:57 就开机了（和图像 GPU 同时），所以等图出来时冷启动已经过去大半 |
| n8 audio.mix | 793 | 62 | 循环到 90 秒，−13.1 LUFS |
| n9 review.human | 856 | 等待 191 | 批准 |
| n10 output.save | 1046 | 0.03 | `outputs/n10.mp4` |

成片：1344×768，90.0 秒，视频和音频都是 90 秒。三个时刻的画面（3 秒、30 秒、70 秒）都是同一个镜头：夜晚海边，右侧岬角上的白色灯塔射出一道光束，天上有银河，近处是草丛，中景是沙滩、海浪和礁石，和首帧图一致；三帧之间只有萤火虫光点和光束位置的小变化（5 秒镜头正放倒放循环，30 秒和 70 秒两帧几乎一样）。音频是生成的歌：对成片 3–43 秒做 faster-whisper 转写，得到「潮水靜靜地 要醒醒我反尘」，对应歌词「潮水轻轻低吟」「像星星落凡尘」，说明歌声就是 n2 写的词。

注意：这次运行用的服务进程还没有「提前唤醒下游 GPU」的代码，视频 GPU 是手动和图像同时唤醒的（调用的就是这段代码里的 `ensure_running()`）；之后的代码会自动这样做。不提前唤醒的话 n7 要多等约 8 分钟。

### b. 改一个下游参数重跑

同一模板只把 n7 video.h3 的 seed 从 7 改成 8，运行 `20261104-083738-b74781`：

| 节点 | 状态 | 用时 |
|---|---|---|
| n2 agent.lyrics、n3 music.yue2、n4 agent.image_prompt、n5 image.qwen、n6 agent.video_prompt | **缓存命中** | 0 秒（没有调用模型和 GPU） |
| n7 video.h3 | 重新生成 | 49 秒（视频 GPU 还热着） |
| n8 audio.mix | 重新生成（输入变了） | 71 秒 |
| n9 review.human、n10 output.save | 重新审核、保存 | |

从提交到可以审核 122 秒。

### c. Agent 生成 5 个句子

5 个句子都是**第 1 轮就通过校验**：

| 句子 | 生成的工作流 | 运行 |
|---|---|---|
| 先查资料再做一条科普短视频 | input.text → agent.write（整理资料提纲，标注没有实时检索）→ explainer.video（60 秒、9:16、插图 2）→ review.human → output.save | **跑通**（运行 `20261104-082637-fcc88a`）。第一次 agent.write 的 `max_tokens` 2500 不够被截断，节点失败；之后加了自动用 8000 重试一次（下面另注），这次把参数改成 6000 后重新运行：agent.write 29 秒，自选主题「天空为什么是蓝色的」；explainer.video 755 秒（95.6 秒竖屏成片，2 张插图）。画面：「阳光是彩色的」三棱镜、「紫光更短，为何不是紫天」的光谱图和人眼敏感度曲线、「彩蛋：火星的反转天空」对比卡片配火星日落插图，底部有字幕 |
| 给一首古风纯音乐配一张封面 | 两条分支：agent.music_style（纯音乐、编排说明）→ music.yue2 → qc.auto（不能有人声、至少 60 秒）→ review → save；agent.image_prompt（水墨意境）→ image.qwen（1:1）→ review → save | 只校验 |
| 做一个 15 秒产品宣传片 | agent.video_prompt → video.h3（15 秒）；agent.music_style → music.yue2；audio.mix（replace、cut_audio）→ qc.auto（14–16 秒）→ review → save | 只校验 |
| 把这句诗做成竖屏短视频：明月几时有 | agent.image_prompt → image.qwen（9:16）→ 首帧 → video.h3（9:16、10 秒）← agent.video_prompt；agent.music_style → music.yue2（纯音乐）；audio.mix → review → save | 只校验 |
| 生成三张不同风格的头像 | 三个 agent.image_prompt（动漫、写实、扁平插画）→ 三个 image.qwen（1:1，seed 101/202/303）→ 三个 review → 三个 save | **跑通**（运行 `20261104-082426-b63e4c`，只用图像 GPU）。三张图在同一个图像 GPU 上排队：437、507、577 秒（第一张包含冷启动）。三个审核节点各自独立：写实那张先出来，先批准先保存 |

### d. AI 助手

在去掉审核节点的 MV 模板上说「在视频后面加一个人工审核节点」：1 轮通过校验，差异「新增节点 n9 (review.human)；新增连线 n8.video -> n9.input、n9.output -> n10.work；删除连线 n8.video -> n10.work」，正好是在合成好的视频和保存之间加审核。

在原 MV 模板上（合成后已经有审核）说同一句话：Agent 把审核加在 `video.h3` 和 `audio.mix` 之间（新增 n11，`n7.video -> n11.input`、`n11.output -> n8.video`，删除 `n7.video -> n8.video`），也通过校验，可以理解为「视频镜头生成后先审核再配乐」。

### e. 单元测试

`server/tests/`，不调 GPU 和模型，25 个用例：

- `test_validate.py`：模板都通过；环、自环、不存在的节点和端口、类型不配（包括审核节点按输入类型往下传）、必填参数和必连输入、「未连线时必填」、参数类型/范围/枚举、布尔不能当整数、一个输入多条线、GPU 模块没配置、重复 id、未知类型、格式错误、拓扑序和分层。
- `test_cache.py`：缓存键稳定（参数顺序、显式默认值不影响），参数、节点类型、输入文件内容、节点版本号变了键就变，同内容不同路径的文件键相同；用桩节点跑编排器：人工审核暂停和批准后保存作品、改下游参数后上游命中缓存只跑下游、全部相同时全部命中但仍然要审核、打回让运行失败、失败节点单独重跑不重跑上游、同一 GPU 模块的两个节点串行。

```bash
cd server && ~/.cache/explainer-video/venv/bin/python -m pytest -q tests
```

### 实测中改的问题

- `agent.write` 的输出被 `max_tokens` 截断时节点直接失败。现在自动用 8000 重试一次（节点 meta 里记 `max_tokens_retry`）。
- 等待审核时，另一条分支还在跑，运行状态却已经显示「等待审核」。现在等所有正在跑的节点结束后再决定运行状态。

### GPU 花费

三台实例都是这次测试按需启动、测试后用 `ctl.py stop` 终止：音乐按需 g5.4xlarge 约 16 分钟（$1.62/小时，约 $0.43），图像 Spot g7.2xlarge 约 15 分钟（约 $0.15），视频 Spot g7e.4xlarge（ap-south-1）约 17 分钟（$2.66–6.53/小时，约 $0.75–1.85）。合计约 $1.3–2.5（Cost Explorer 数据晚一天，按标签核对）。
