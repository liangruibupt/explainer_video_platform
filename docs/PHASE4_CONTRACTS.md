# 阶段 4 接口约定（并行开发用）

阶段 4 拆成 5 个互相独立的部分，各自只改自己的文件。共用的入口（`server/app/main.py`、`web/src/main.jsx`、`web/src/Layout.jsx`）已经接好路由和导航，各部分**不要改这三个文件**。最后由集成任务合并、联调、验收。

## 文件归属

| 部分 | 后端文件 | 前端文件 | 路由前缀 / 页面 |
|---|---|---|---|
| A 对话创作 + 引导创作 | `server/app/chat/`（`api.py` 里的 `router`） | `web/src/pages/ChatPage.jsx`、`web/src/pages/GuidePage.jsx` | `/api/chat/*`；`/chat`、`/guide` |
| B 工作流编辑器（拖拽 + 候选数） | `server/app/workflows/candidates.py`（新），可改 `server/app/workflows/engine.py` | `web/src/pages/WorkflowEditor.jsx`（新）、`web/src/components/flow/*`，可改 `web/src/pages/WorkflowPage.jsx` | `/workflow/edit/:id` |
| C 素材库 + 项目 | `server/app/assets/`（`api.py` 里的 `router`） | `web/src/pages/AssetsPage.jsx`、`web/src/pages/ProjectsPage.jsx` | `/api/assets/*`、`/api/projects/*`；`/library`、`/projects`（页面不能叫 `/assets`：那是前端构建产物 JS/CSS 的挂载路径） |
| D 任务中心（审核收件箱）+ 完成通知 | `server/app/tasks/`（`api.py` 里的 `router`） | 可改 `web/src/pages/Tasks.jsx` | `/api/tasks/*`；`/tasks` |
| E 设置（模型部署卡片 + 其他设置） | `server/app/settings/`（`api.py` 里的 `router`） | `web/src/pages/SettingsPage.jsx` | `/api/settings/*`；`/settings` |

规则：
- 只改自己那一行列出的文件。需要别人的数据时，**只调用下面「共用接口」里列出的现有函数或 HTTP 接口**，不改它们。
- 每个后端部分都是一个包，在 `api.py` 里导出 `router = APIRouter(prefix=...)`。`main.py` 已经 `include_router`，包不存在时自动跳过，所以各部分可以单独开发、单独测试。
- 新的前端页面导出 `default function`。`main.jsx` 用 `React.lazy` 引入，页面文件不存在时显示「建设中」，所以各部分互不阻塞。
- 数据放在 `pipeline.DATA` 下各自的目录：`data/chat/`、`data/assets/`、`data/projects/`、`data/tasks/`、`data/settings/`。
- 样式写进页面自己的 `<style>` 或 CSS 模块，**不改 `web/src/styles.css`**（集成时统一整理）。

## 共用接口（只调用，不改）

### 后端 Python

| 用途 | 调用 |
|---|---|
| 调 Opus | `from app.llm import ask`；`ask(system, user, max_tokens) -> str` |
| 工作流节点声明 | `from app.workflows.nodes import NODES`（`{type: {inputs, outputs, params, runner, ...}}`） |
| 校验工作流 | `from app.workflows.validate import validate`；`validate(wf) -> list[error]` |
| 生成 / 改工作流 | `from app.workflows import agent`；见 `agent.py` 里的 `generate(prompt)`、`edit(workflow, instruction)` |
| 启动 / 读运行 | `from app.workflows import engine`；`engine.start(workflow) -> run`、`engine.read_run(run_id)`、`engine.list_runs()`、`engine.review(run_id, nid, decision, note)`、`engine.retry(...)` |
| GPU 状态 | `from app import gpu_client`；`gpu_client.status(module)`，module ∈ image / video / music |
| GPU 控制器 | `from app.gpu_ec2 import GpuEC2`；`GpuEC2(module).status()`、`.stop()`、`.instances()`、`.prices()`；配置在 `infra/gpu_ec2/modules/<module>.json` |
| 讲解视频任务 | `from app import pipeline`；`pipeline.read_job(id)`、`pipeline.job_dir(id)`；HTTP `GET /api/jobs` |

### 运行记录里的审核状态（D 和 A 都会读）

`engine.read_run(run_id)` 返回 `{id, name, workflow, status, nodes: {nid: {status, started, ended, outputs, error, ...}}, ...}`。`status == "waiting_review"` 的节点就是待审核项，用 `engine.review(...)` 批准或打回。

### 前端

- `import { api, usePoll, ago, fileUrl } from '../api.js'`（`api(path, opts)` 自动加 `/api` 前缀和 JSON）。
- `import GpuPill from '../components/GpuPill.jsx'`（`<GpuPill module="image" />`）。
- 现有页面的样式类可以直接用：`page`、`h1-sm`、`sub`、`composer`、`row`、`opt`、`toggle`、`primary`、`btn`、`panel`、`grid`、`card`、`pill`、`muted`、`error`、`empty`、`section-head`。

## 新增的跨部分约定

### 素材引用（C 提供，A、B 使用）

素材 id 形如 `asset_<yyyymmdd-hhmmss>-<6位hex>`。
- `GET /api/assets?kind=image|audio|video` → `[{id, kind, name, size, created, url, project_id}]`
- `GET /api/assets/{id}/file` → 文件本体
- Python：`from app.assets.store import asset_path`；`asset_path(id) -> Path`

工作流里用素材：节点 `input.asset`（由 C 在 `server/app/assets/node.py` 里定义，集成时注册进 `NODES`），参数 `{"asset_id": "..."}`，输出端口类型按素材类型（image / audio / video）。

### 项目（C 提供）

`GET /api/projects` → `[{id, name, created}]`。任务、运行、素材可以带 `project_id`。各部分在自己的数据里存 `project_id` 字段即可，不做强校验。

### 通知（D 提供）

`from app.tasks.notify import notify`；`notify(title, body, url)`。工作流跑完或进入待审核时调用（集成时接到 engine 的状态变化上）。D 负责实现投递方式（页面内通知列表；可选浏览器通知）。

### 对话（A）

`POST /api/chat/sessions` → `{id}`；`POST /api/chat/sessions/{id}/messages {text}` → Agent 回复（可能是追问、生成的工作流、或运行进度）；`GET /api/chat/sessions/{id}`。A 用 `agent.generate` / `agent.edit` 和 `engine.start` 实现，不改工作流代码。
