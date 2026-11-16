# BioQuorum 架构设计

> 状态：草案 v0.1（2026-11-11）。产品定义见 [PRODUCT.md](PRODUCT.md)，调研见 [research/landscape-2026-11.md](research/landscape-2026-11.md)。

## 1. 设计原则

1. **科学层和 agent 层分离**：科学层（kernel、溯源、连接器）不依赖 agent 层，可以单独作为 MCP Server 使用。这样以后即使换掉 agent 内核，科学层的投入也不会浪费。
2. **一切皆事件**：会话中发生的每件事（消息、模型调用、工具调用、权限决定、审查发现）都写入只追加的事件日志。UI 流式推送、审计、溯源和重放都基于同一份数据。
3. **大数据留在 kernel**：模型只看到摘要（形状、表头、统计量、图的缩略图），不看到原始数据。
4. **后端只用 Python**：前端是唯一的例外，它是一个独立的 TypeScript 子项目，构建后作为静态文件随 Python 包一起发布。
5. **依赖少而稳**：每引入一个依赖都要有理由，核心依赖锁定版本。
6. **默认安全**：默认零遥测；敏感操作需要授权；密钥永远不进入模型上下文。

## 2. 分层架构

```
┌──────────── 入口层 ─────────────────────────────────────────────────┐
│  web/ (React UI)     cli/ (bq 命令)     sdk     mcp server 模式      │
└──────────▲────────────────▲──────────────────────────▲──────────────┘
           │ HTTP + SSE     │                           │
┌──────────┴────────────────┴───────┐                   │
│ server/  FastAPI · 事件流推送       │                   │
├───────────────────────────────────┤                   │
│ agent/   编排层                     │                   │
│  coordinator loop · plan · compaction · specialists    │
│ review/  审查层                     │                   │
│  claim 抽取 · 检查器 · 重跑 · 共识   │                   │
│ models/  模型层                     │                   │
│  provider 注册 · 角色 · 能力矩阵 · 路由 · 成本            │
╞═══════════════════════════════════╪═══ 科学层（可独立运行）═╡
│ tools/      工具定义（kernel/file/web/artifact/connector）           │
│ runtime/    kernel 管理（Python/R）· shell · 沙箱                     │
│ provenance/ 产物存储（CAS）· 血缘 · 环境锁定 · 研究包导出/重跑         │
│ connectors/ 生物医药数据库    skills/ SKILL.md 加载    mcp/ MCP 客户端 │
├──────────────────────────────────────────────────────────────────────┤
│ policy/  权限 · 数据分级 · 审计 · 密钥        storage/  SQLite · 迁移   │
│ core/    领域模型（纯 Pydantic，无外部依赖）                            │
└──────────────────────────────────────────────────────────────────────┘
```

**依赖方向（必须遵守，由 import-linter 在 CI 中检查）**：
`server/cli → agent → review → models → tools → runtime/provenance/connectors/skills/mcp → policy/storage → core`

科学层（`tools` 及其下层）**禁止** import `agent`、`review`、`models`、`server`。

## 3. 目录结构

```
bioquorum/
├── pyproject.toml            # uv 管理；一个 Python 包，内部分子包
├── uv.lock
├── src/bioquorum/
│   ├── core/                 # 领域模型：Project, Session, Event, Run, Artifact...
│   ├── storage/              # SQLAlchemy + Alembic 迁移；事件日志表
│   ├── policy/               # permissions.py, classification.py, audit.py, secrets.py
│   ├── runtime/              # kernels.py (jupyter_client), summarize.py, shell.py, sandbox/
│   ├── provenance/           # store.py (CAS), lineage.py, env.py (pixi), bundle/ (export/rerun)
│   ├── connectors/           # base.py + 各数据库：opentargets.py, chembl.py, uniprot.py ...
│   ├── skills/               # loader.py（SKILL.md 渐进加载）+ builtin/ 内置 skills
│   ├── mcp/                  # client.py（接外部 MCP）+ server.py（暴露科学层）
│   ├── tools/                # 统一的工具接口，供 agent 和 MCP server 共用
│   ├── models/               # registry.py, roles.py, capabilities.py, routing.py, cost.py
│   ├── review/               # claims.py, checks/, rerun.py, consensus.py
│   ├── agent/                # loop.py, plan.py, compaction.py, prompts/, specialists.py
│   ├── server/               # app.py, routes/, sse.py, static/（构建好的前端）
│   └── cli/                  # main.py (typer)
├── web/                      # React + Vite + TypeScript + Tailwind/shadcn
├── bundle-spec/              # 研究包开放规范 + JSON Schema
├── evals/                    # Inspect AI 评测任务（3 个北极星场景 + 公开 benchmark 子集）
├── examples/                 # 示例项目：target-dossier/, scrna-analysis/, evidence-review/
├── docs/                     # 设计文档 + mkdocs 文档站
├── tests/                    # 与 src 结构对应；unit/ integration/ e2e/
├── CLAUDE.md                 # 给 AI 协作者的项目约定
├── LICENSE (Apache-2.0) · SECURITY.md · CONTRIBUTING.md · THIRD_PARTY.md
└── .github/workflows/        # CI：test, lint, typecheck, import-linter, release
```

## 4. 技术栈

| 用途 | 选型 | 备注 |
|---|---|---|
| 语言 / 包管理 | Python 3.12+ / **uv** | |
| Agent 内核 | **Pydantic AI v2** | 原生支持 Anthropic / OpenAI / Google / OpenAI 兼容接口；跨 provider 的 thinking 回放；FallbackModel |
| 模型网关 | **不内置 LiteLLM** | 用户想用网关时，把 LiteLLM Proxy / OpenRouter 当作 OpenAI 兼容端点配进来即可。这样少一个依赖，也避开了它 2026 年的两次投毒风险 |
| Web 服务 | FastAPI + SSE | |
| 存储 | SQLite（WAL 模式）+ SQLAlchemy 2 + Alembic | 团队版可以换成 Postgres |
| 执行 | jupyter_client + ipykernel / IRkernel | 标准协议，可以导出 ipynb |
| 环境 | **pixi**（conda-forge + bioconda + PyPI，锁在一个文件里） | 每个项目一个环境；锁文件 hash 写进每条 Run 记录 |
| MCP | 官方 `mcp` Python SDK | 既当客户端也当服务端 |
| CLI | typer + rich | |
| 前端 | React + Vite + TS + Tailwind + shadcn/ui；Mol*（3D 结构）、RDKit.js（分子）、igv.js（基因组） | 构建后放进 `server/static`，用户不需要装 Node |
| 沙箱 | v0.5：Linux 用 bubblewrap，macOS 用 sandbox-exec，服务器部署用 Docker | 网络经本地代理按白名单放行 |
| 评测 | Inspect AI | |
| 质量 | pytest、ruff、pyright、import-linter、pre-commit | |
| 文档 | mkdocs-material | |

## 5. 核心数据模型

```python
Project(id, name, root_dir, data_dirs[], classification: public|internal|confidential,
        model_policy, env_id, created_at)

Session(id, project_id, title, parent_session_id?, forked_at_event_id?, status)

Event(id, session_id, seq, ts, type, actor, payload: json)   # 只追加
  type ∈ user_message | assistant_message | model_call | tool_call | tool_result
       | plan_proposed | plan_approved | plan_step_done
       | permission_request | permission_decision
       | run_started | run_finished | artifact_saved
       | review_started | review_finding | review_finished | error

Run(id, session_id, kernel_id, language, code, env_lock_hash,
    inputs: [DataRef], outputs: [DataRef], stdout_ref, status, started_at, ended_at)

DataRef(uri, content_hash, size, kind)            # 文件或内存对象快照

Artifact(id, project_id, name, kind: figure|table|report|dataset|structure|notebook)
ArtifactVersion(id, artifact_id, version, parent_version_id?, content_hash,
                run_id, event_range, description,
                verification: unverified|reviewed|reproduced|disputed)

ReviewFinding(id, session_id, target: claim|artifact|plan, check, severity,
              evidence, reviewer_model, status: open|addressed|dismissed)

ModelCall(id, event_id, provider, model, role, classification,
          input_tokens, output_tokens, cache_tokens, cost_usd, latency_ms)

AuditEntry(id, ts, actor, action, target, classification, detail)
```

## 6. 关键模块设计

### 6.1 模型层 `models/`

配置示例（`~/.bioquorum/config.toml` 或项目内的 `bioquorum.toml`）：
```toml
[providers.anthropic]   # 密钥放在系统钥匙串，这里只写引用
api_key = "keyring:anthropic"
[providers.openai]
api_key = "keyring:openai"
[providers.local]
type = "openai-compatible"
base_url = "http://localhost:8000/v1"
allowed_classifications = ["public", "internal", "confidential"]

[roles]
planner  = "anthropic:<model>"
worker   = "openai:<model>"
reviewer = "google:<model>"       # 默认要求与 planner 不同家族
summarizer = "local:<model>"
```

- **能力矩阵**（`capabilities.py`）：每个模型记录上下文长度、是否支持并行 tool call、thinking、视觉输入、缓存方式、单价。数据来自 models.dev 的快照，用户可以覆盖。
- **路由**（`routing.py`）：按项目分级过滤可用的 provider，再选出该角色配置的模型；不可用时按回退链依次尝试。**违反分级策略的调用直接拒绝并记入审计**，不会静默降级。
- **兼容层**：主要交给 Pydantic AI。我们额外做三件事：保存每次调用的原始响应（方便调试和审计）；在 tool call 的 JSON 出错时修复或重试；对不擅长 tool call 的模型切换到 code-action 模式（直接生成 Python 代码来调用工具）。

### 6.2 执行层 `runtime/`

- `KernelManager`：每个 Session 默认一个 Python kernel，按需再开 R kernel；运行在项目的 pixi 环境里；空闲超时后回收，回收前可以把变量快照保存下来。
- `execute(code) → RunResult`：捕获 stdout/stderr、富输出（图片、HTML）、异常。
- `summarize.py`：把输出压缩成模型能看的摘要。例如 DataFrame 只给 shape、dtypes、head 和 describe；AnnData 只给维度和 obs/var 的列名；图片存为产物，同时给模型一张缩略图。
- **血缘 hook**：在 kernel 启动时注入，包装 pandas、anndata、numpy、matplotlib 的读写函数，把输入输出的路径和 hash 记录到当前 Run。

### 6.3 溯源层 `provenance/`

- **CAS 存储**：`~/.bioquorum/store/ab/cdef...`，按内容 hash 寻址，天然去重。
- 保存产物时，自动关联当前的 Run、环境锁文件 hash、输入的 DataRef 和对应的事件区间。
- `rerun(artifact_version)`：用同一份锁文件重建环境，重新执行相关 Run 链，对比输出 hash；数值型结果允许设置容差。
- **研究包**（见 `bundle-spec/`）：
```
bundle/
├── manifest.json        # 规范版本、工具版本、用到的模型、创建时间、各文件 hash
├── README.md            # 自动生成：做了什么、怎么重跑、Methods 草稿
├── conversation.jsonl   # 事件日志（可选择脱敏）
├── runs/<id>/code.py    # 按顺序排列的代码
├── env/pixi.lock
├── data/manifest.json   # 输入数据的来源 URI 和 hash；可以选择把数据也打包进来
├── artifacts/
├── review/findings.json
└── rerun.sh             # 不装 BioQuorum 也能重跑（只需要 pixi）
```

### 6.4 审查层 `review/`

审查流程：
1. **抽取结论**：从回答里抽出可检查的结论，分成数值、引用、"我计算了…"这类执行声明、因果性结论。
2. **检查器**（`checks/`，每个都是独立插件）：
   - `execution_evidence`：声称算过的东西，事件日志里有没有对应的 Run
   - `value_match`：结论里的数值和 Run 输出、源文件是否一致
   - `citation_resolves`：DOI/PMID 能否解析，标题和作者是否匹配
   - `citation_supports`：用 reviewer 模型读摘要，判断它是否支持这条结论
   - `plan_complete`：已批准计划中的步骤是否都完成了
   - `method_sanity`：常见统计错误，比如多重检验没做校正、样本量太小
3. **重跑**（可选）：关键 Run 在全新 kernel 里重新执行，对比结果。
4. **输出**：ReviewFinding 写入事件流，UI 显示为审查卡片；主 agent 下一轮必须对每条发现给出回应。
5. **共识模式**（`consensus.py`）：两个不同家族的模型独立完成同一个任务，再自动对比结论的分歧。

### 6.5 编排层 `agent/`

- 主循环：接收用户消息 → 规划（复杂任务先给出 Plan 卡片等待批准）→ 循环执行 tool call → 回答 → 触发审查。
- **Compaction**：上下文接近上限时，用 summarizer 模型压缩较早的历史。被压缩的只是模型看到的上下文，原始事件全部保留。
- **Prompt 结构要对缓存友好**：系统提示、工具列表、skill 目录保持固定顺序；时间戳之类的易变内容放在末尾。
- **Specialists**（v0.6 以后）：用 markdown 定义的子 agent，每个可以单独配置模型和工具权限，支持并行。

### 6.6 策略层 `policy/`

- 权限卡片：文件目录访问、代码执行、网络访问、调用连接器、外部 MCP 工具。授权范围分为一次、本会话、本项目、全局，在设置里可以查看和撤销。
- 数据分级和模型路由联动（见 6.1）。
- 审计日志：只追加，可以导出 CSV/JSON。

## 7. 扩展点（对开源贡献者开放）

| 扩展 | 方式 | 难度 |
|---|---|---|
| Skill | 一个 SKILL.md 文件夹（Agent Skills 标准） | 最低：会写 markdown 就行 |
| Connector | 继承 `connectors.base.Connector` 并注册 entry point；或者直接接入任意 MCP Server | 低 |
| Review check | 实现 `Check` 接口并注册 entry point | 中 |
| Specialist | markdown + frontmatter | 低 |
| Model provider | 任何 OpenAI 兼容接口直接配置即可；特殊协议走 Pydantic AI 的 provider | 中 |

## 8. 安全模型（摘要，完整内容见 SECURITY.md）

- 威胁：模型生成的代码误删或篡改数据；prompt injection（来自网页、文献、数据库返回内容）导致数据外泄；第三方依赖或 MCP 服务器被投毒。
- v0.1–v0.4 的防护：权限卡片 + 目录授权 + 审计；**明确告知用户：此时还没有 OS 沙箱**。
- v0.5 起：kernel 运行在 OS 沙箱里，网络默认拒绝、按白名单放行；外部 MCP 服务器以独立进程运行。
