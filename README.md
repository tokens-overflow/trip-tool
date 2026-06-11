# 🧭 trip-tool

> 基于 **LLM 编排 + Google Maps Platform** 的旅游深度调研 / 攻略生成工具
> 输入一句主题 → 自动拆解任务 DAG → 并发调用 Maps API → 汇总成深度报告 + **可执行时间线行程**
>
> 前端不内嵌地图：每个地点直接给一个可点击的 Google 地图外链，更轻、更省 key。

---

## 一、它能做什么

输入「东京涉谷 3 日游」+（可选）出行时间、预算，约 1–2 分钟后得到：

- **佳处**：候选地点卡片，含评分 / 价位 / 评论数 / 营业时间 / 官方简介 / **真实评价**，按质量分（评分 × log(1+评论数)）排序，每个都能一键跳 Google 地图；
- **行程**：按**时间线**编排的攻略——时刻 / 时长 / 品类 / **门票** / 营业提醒 / 到下一站交通 / 避坑，外加**当天天气**与注意事项；
- **手记**：结构化深度报告——概览 · 地点详览 · 分类对比表 · 行程时间线 · **预算估算** · 天气与时令 · 避坑提示 · 参考来源；
- **行迹**：实时工具调用日志（缓存命中 / 耗时 / 返回数量）。

天气走 **wttr.in**（免费、免 key）；行程会结合天气、营业时间、预算给出取舍。

---

## 二、系统架构

### 1. 顶层数据流

```
┌──────────────────────────────────────────────────────────────────────┐
│                        前端 Vue 3 + Vite                              │
│                    http://localhost:5173                              │
│   表单 ─────► SSE 流 ─────► 任务脉络 │ 佳处 │ 行程 │ 手记 │ 行迹       │
│              （每个地点 = 可点击的 Google 地图外链，无内嵌地图）        │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  /api  →  vite proxy
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  后端 FastAPI  http://localhost:8000                  │
│                                                                      │
│   POST /research(/stream)                                            │
│                ▼                                                     │
│   ┌────────────────────────────────────────────────────────────┐    │
│   │                   研究 Agent (facade)                       │    │
│   │  ┌──────────────────────────────────────────────────────┐  │    │
│   │  │  Pipeline                                            │  │    │
│   │  │  ① PlanStage  → ② ExecuteStage → ③ ComposeStage     │  │    │
│   │  └──────────────────────────────────────────────────────┘  │    │
│   └────────────────────────────────────────────────────────────┘    │
│                                                                      │
│   每个 Stage 用到的零件：                                              │
│       PlanStage    →  PlanTask (LLM)                                 │
│       ExecuteStage →  DAGScheduler + ToolRunner + SummarizeTask      │
│       ComposeStage →  ReportTask + ItineraryTask (并行) + 天气        │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
       ┌─────────────────┼──────────────┬──────────────────┐
       ▼                 ▼              ▼                  ▼
  Google Maps       LLM Provider     wttr.in          SQLite + LRU 缓存
  Platform         (DeepSeek /       (天气，免 key)     (./.cache/maps/)
  (4 个 REST API)   Anthropic / …)
```

### 2. 后端分层（高度解耦）

```
backend/src/
│
├─ 🧩 core/                ── 通用骨架（与业务无关，可移植）
│   ├─ events.py           EventEmitter = Callable[[Event], None]
│   ├─ pipeline.py         Stage 协议 + Pipeline
│   └─ llm_task.py         LLMTaskBase + Text/Json/Streaming 三档
│
├─ ⚙️ execution/           ── DAG 调度 + 工具执行（与 LLM/事件无关）
│   ├─ scheduler.py        DAGScheduler[N: HasDependencies]
│   └─ tool_runner.py      prepare_args → execute → _to_evidence
│
├─ 🤖 llm_tasks/           ── 4 个 LLM 用例（prompt + parse 二合一）
│   ├─ plan_task.py        JsonLLMTask[PlanInput, list[TaskNode]]
│   ├─ summarize_task.py   StreamingLLMTask[SummarizeInput, str]
│   ├─ report_task.py      TextLLMTask[ReportInput, str]
│   └─ itinerary_task.py   JsonLLMTask[ItineraryInput, (days, overview)]
│
├─ 🎬 stages/              ── 3 个 Pipeline Stage（唯一混合 LLM+tool+event）
│   ├─ plan_stage.py
│   ├─ execute_stage.py
│   └─ compose_stage.py    报告 + 行程并行；先拉天气 + 拼出行/预算上下文
│
├─ 🔌 llm/                 ── 多 provider 适配
│   ├─ base.py             LLMClient Protocol + LLMUsage
│   ├─ loader.py
│   └─ adapters/           openai_compat / anthropic_adapter (Anthropic + Bedrock)
│
├─ 🌐 tools/               ── 工具与缓存
│   ├─ base.py             Tool / ToolRegistry / ToolResult[T]
│   ├─ cache.py            两级缓存（LRU + sqlite）
│   ├─ weather.py          wttr.in 天气（免 key，尽力而为，失败不阻塞）
│   └─ maps/               places / directions / geocoding / distance_matrix
│       ├─ client.py       async HTTP（共用 _get/_post 模板）
│       └─ results.py      4 个 typed Payload（Pydantic）
│
├─ agent.py                顶层 facade：装配 Pipeline，run / run_stream
├─ main.py                 FastAPI 入口
├─ config.py               config.yaml → Pydantic 嵌套模型
├─ models.py               域模型 + SSE 事件
└─ prompts.py              4 个 Prompt 模板（中文）
```

### 3. 解耦原则

| 层 | 知道什么 | 不知道什么 |
|---|---|---|
| `core/` | 什么都不知道 | 业务、地图、LLM、HTTP |
| `tools/` | HTTP、Maps API、天气、缓存 | LLM、Pipeline、Stage |
| `llm/` | LLM 协议、provider | 业务、prompt、工具 |
| `llm_tasks/` | 单个 LLM 用例（prompt + parse） | Pipeline、其它任务、工具 |
| `execution/` | DAG 调度、工具注册表 | LLM、事件分发 |
| `stages/` | 把上面所有零件组合起来 | —— |
| `agent.py` | 装配 Stage 列表 | Stage 内部如何工作 |

新人阅读顺序：**`agent.py` → `core/pipeline.py` → `stages/*`**，需要细节再深入对应零件层。

### 4. 三段式编排时序

```
   ┌────────── PlanStage ──────────┐  ┌──────── ExecuteStage ────────┐  ┌─── ComposeStage ───┐
   │                               │  │                              │  │                    │
   │  PlanTask (chat_json)         │  │  for each task in DAG:       │  │  拉天气 + 出行/预算  │
   │     ↓                         │  │    prepare_args()            │  │     ↓              │
   │  validate + topo + 兜底       │  │    execute() → ToolResult    │  │  ReportTask ↕ 并行  │
   │     ↓                         │  │    SummarizeTask.stream() ──►  │  ItineraryTask     │
   │  state.tasks = [...]          │  │    state.tasks[*].evidence   │  │     ↓              │
   │  emit(plan_ready)             │  │  emit(task_update × N)       │  │  emit(report)      │
   │                               │  │  emit(summary_chunk × M)     │  │  emit(usage)       │
   └───────────────────────────────┘  └──────────────────────────────┘  └────────────────────┘
            ▲ 同一条 Pipeline 被 run() 和 run_stream() 共用，差别只在传给 emit 的实现 ▲
```

---

## 三、初次安装设定

### 1. 开通 Google Maps API（后端用，1 把 key）

在 [console.cloud.google.com](https://console.cloud.google.com) 同一项目下：

1. 「**APIs & Services → Library**」启用 **4 个 REST API**：
   - Places API (New)
   - Directions API
   - Geocoding API
   - Distance Matrix API

   > 前端不再内嵌地图，**无需** Maps JavaScript API，也**不需要前端 Browser key**。

2. 「**Credentials → Create credentials → API key**」创建 **1 把后端 Server key**，
   建议 Application restrictions 限到服务器 IP、API restrictions 限到上面 4 个 API。

3. 「**Billing**」绑定计费账户（Google 每月赠送 $200 免费额度）。

### 2. LLM Provider 凭证

`config.yaml` 的 `llm.providers` 下可放多个 provider，启动时只看 `llm.active` 字段：

| 类型 | 凭证 |
|---|---|
| `deepseek`（推荐起步，便宜） | `${DEEPSEEK_API_KEY}` |
| `anthropic` | `${ANTHROPIC_API_KEY}` |
| `openai` | `${OPENAI_API_KEY}` |
| `bedrock` | AWS access key + secret + region |

> ⚠️ Places 字段掩码包含 `reviews` / `editorialSummary`，会把搜索计费提到较高档（Enterprise + Atmosphere）。
> 想省钱可在 `tools/maps/client.py` 的 `PLACE_FIELD_MASK` 里去掉 `reviews`（或连 `editorialSummary` 一起去掉）。

### 3. 编辑配置文件

**`backend/config.yaml`**（从 `config.example.yaml` 拷贝；含真实 key，已被 gitignore）：

```yaml
llm:
  active: deepseek          # 切换 provider 改这一行
  providers:
    deepseek:
      type: deepseek
      model: deepseek-chat
      api_key: sk-...        # 直填或写 ${DEEPSEEK_API_KEY}
      base_url: https://api.deepseek.com

app:
  maps:
    api_key: AIzaSy...       # ★必填：后端 Server key
    default_places_limit: 20 # 候选数量等可按需调整
  # 其余字段（端口、并发、缓存、retry…）有默认值
```

**`frontend/.env.local`**（拷贝 `.env.local.example` 即可，**不需要任何 key**）：

```env
VITE_BACKEND_BASE=/api
```

### 4. 安装依赖

需要 **Python 3.10+、Node 18+**。

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .            # 按 active provider 自带 [bedrock]/[anthropic] extra 可选追加

# 前端
cd ../frontend
npm install
```

> Windows 用户也可用根目录的 `setup.bat` / `setup.ps1` 一键装好（脚本为 Windows 编写）。

---

## 四、启动方式

开 **两个终端**：

```bash
# 终端 A：后端
cd backend
python -m src.main
# → INFO Uvicorn running on http://127.0.0.1:8000
```

```bash
# 终端 B：前端
cd frontend
npm run dev
# → VITE v6.x ready, Local: http://localhost:5173
```

浏览器打开 **http://localhost:5173**，填入主题（如「东京涉谷 3 日游」），
可选填**出行时间**（如「6 月下旬」）和**预算**（如「人均 500」）→ 点「开始研究」。
约 1–2 分钟后，任务脉络、佳处、行程时间线、手记报告依次渲染完成。

> Windows 用户可用 `start.bat` / `start.ps1` 一键起双端并自动开浏览器。

### 健康检查 / 切换 provider

```bash
curl http://localhost:8000/healthz
# {"status":"ok","provider":"deepseek","model":"deepseek-chat","config_path":"...config.yaml"}
```

改 `backend/config.yaml` 的 `llm.active` 后重启后端即生效。

---

## 五、云端部署

### 1. 两套配置文件的分工

| 文件 | git 状态 | 用途 |
|---|---|---|
| `backend/config.yaml` | **gitignored**（含真实 key） | 本地开发 |
| `backend/config.example.yaml` | committed（全部 `${ENV_VAR}` 占位） | 模板：本地兜底、云端直接用 |

`${VAR}` 占位符支持两种语法：

```yaml
api_key: ${DEEPSEEK_API_KEY}              # 未设环境变量 → 空字符串
region:  ${AWS_REGION:-ap-northeast-1}    # 未设 → 用 ap-northeast-1（bash 风格）
```

### 2. 云端启动

启动时通过 `CONFIG_PATH` 指向模板，secret 由平台环境变量注入：

```bash
CONFIG_PATH=config.example.yaml python -m src.main
```

### 3. 必填环境变量

按 `LLM_ACTIVE` 选用的 provider 设对应组：

| Provider | 必须设的环境变量 |
|---|---|
| `bedrock`   | `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、（可选 `AWS_REGION`） |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai`    | `OPENAI_API_KEY` |
| `deepseek`  | `DEEPSEEK_API_KEY` |

**所有 provider 都必须设**：

| 环境变量 | 用途 |
|---|---|
| `GOOGLE_MAPS_API_KEY` | 后端 Server key |
| `CORS_ORIGINS` | 前端真实域名（如 `https://app.example.com`） |

可选覆盖：`LLM_ACTIVE` / `HOST` / `PORT` / `LOG_LEVEL` / `CACHE_DIR` 等（见 `config.example.yaml`）。

### 4. 公网暴露的最低安全要求

* 加简单 token 鉴权：在 FastAPI middleware 校验 `X-API-Token` 头，否则陌生人调一次 `/research` 就是你 $0.1+ 账单；
* 后端 Server key 在 Cloud Console 限制 IP 到部署平台的出口 IP；
* CORS 白名单去掉 `localhost`，只留生产域名。

---

## 六、密钥保护

仓库内**不含任何真实密钥**：`backend/config.yaml` 与 `frontend/.env.local` 均被 `.gitignore` 排除，
仅提交 `config.example.yaml`（`${ENV_VAR}` 占位）与 `.env.local.example` 模板。clone 后自行拷贝并填入即可。
