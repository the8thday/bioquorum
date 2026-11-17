# BioQuorum 架构设计

> 状态：**v0.2（2026-11-13）**。v0.1 存档于 [archive/ARCHITECTURE-v0.1-2026-11-11.md](archive/ARCHITECTURE-v0.1-2026-11-11.md)。
> 产品定义见 [PRODUCT.md](PRODUCT.md)，调研见 [research/landscape-2026-11.md](research/landscape-2026-11.md)，路线图见 [../ROADMAP.md](../ROADMAP.md)。
>
> 本文是项目的最高约束。代码和文档冲突时，先改文档再改代码；改动承重决定必须新增一条 ADR（见 §11）。

---

## 0. 怎么读这份文档

架构里的决定分两类，文中用标记区分：

- **【承重】**：改动代价极高，必须从第一个 commit 起遵守，由工具（import-linter、pytest、pre-commit）强制。改动需要新增 ADR。
- **【可调】**：现阶段的合理选择，实现中发现更好的方案可以直接改，改完更新本文即可。

没有标记的段落是说明性内容。§10 "未来方向"不构成任何约束。

---

## 1. 设计原则

1. 【承重】**科学层和 agent 层分离。** 科学层（runtime、provenance、connectors、skills、mcp、tools）不依赖 agent 层，可以单独作为 MCP Server 运行。换掉 agent 内核，科学层的投入不丢。
2. 【承重】**事件日志是唯一写入路径。** 会话中发生的每件事都先写成只追加的事件；Run、Artifact、Claim、ModelCall 等表全部是事件的投影，可以删掉重建。UI 推送、审计、溯源、重放、模型上下文，都从同一份事件派生。
3. 【承重】**自有的规范消息格式。** 事件日志里存的是 BioQuorum 自己定义的消息和工具调用模式，provider 的原始内容只作为不透明附件保存。Pydantic AI 的类型只出现在 `models/` 内部。
4. 【承重】**一份工具定义，两个出口。** 每个工具只在 `tools/` 注册一次，agent 工具列表和 MCP Server 的工具列表都从同一个注册表生成。
5. 【承重】**工具层是唯一的执行咽喉。** 权限检查、数据分级检查、审计记录、事件写入，都发生在 `tools.invoke()` 这一处；agent 层不能绕过它直接执行任何有副作用的操作。
6. 【承重】**工具返回的内容是不可信输入。** 网页、文献摘要、数据库返回、文件内容，进入模型上下文前都标记为 untrusted，并与系统指令明确隔离。
7. 【承重】**大数据留在 kernel。** 模型只看摘要（形状、表头、统计量、缩略图），不看原始数据。
8. 【承重】**后端只用 Python。** 前端是唯一例外，且在路线图上推后；见 §10。
9. 【可调】**依赖少而稳。** 每引入一个依赖都写明理由，核心依赖锁定版本。
10. 【承重】**默认安全。** 零遥测；敏感操作需要授权；密钥永远不进入模型上下文；违反分级策略的调用直接拒绝并记审计，不静默降级。
11. 【承重】**可测试优先。** 每一层都要能在不联网、不调模型、不启动真实 kernel 的情况下测试（见 §6.8）。做不到的设计不接受。

---

## 2. 分层架构

```
┌──────────── 入口层 ─────────────────────────────────────────────────┐
│  cli/ (bq 命令)     mcp server 模式     [web/ 推后]     [sdk 推后]   │
└──────────▲──────────────────▲──────────────────────────────────────┘
           │                  │
┌──────────┴──────────────────┼───────┐
│ server/  FastAPI · SSE 推送（v0.2+） │
├─────────────────────────────┼───────┤
│ agent/   编排：主循环 · plan · compaction                          │
│ review/  审查：claim 抽取 · 检查器 · 重跑 · 共识                    │
│ models/  模型：provider · 角色 · 能力矩阵 · 路由 · 成本 · 消息转换   │
╞═════════════════════════════╪═══ 科学层（可独立运行）══════════════╡
│ tools/      工具注册表 + invoke() 咽喉                              │
│ runtime/    kernel 管理 · 输出摘要 · shell · [沙箱]                 │
│ provenance/ 内容寻址存储 · 血缘 · 环境锁 · 研究包                     │
│ connectors/ 数据库连接器     skills/ SKILL.md 加载     mcp/ 客户端+服务端 │
├──────────────────────────────────────────────────────────────────────┤
│ policy/  权限 · 分级 · 审计 · 密钥          storage/  事件存储 · 投影  │
│ core/    领域模型 + 规范消息格式（纯 Pydantic，无外部依赖）           │
└──────────────────────────────────────────────────────────────────────┘
```

【承重】**依赖方向**，由 import-linter 在 pre-commit 和 CI 中检查：

```
server, cli  →  agent  →  review  →  models  →  tools
tools  →  runtime, provenance, connectors, skills, mcp  →  policy, storage  →  core
```

规则：
- 科学层（`tools` 及其下层）禁止 import `agent`、`review`、`models`、`server`、`cli`。
- `core` 不 import 项目内任何其他包，也不 import Pydantic AI、FastAPI、SQLAlchemy。
- `models` 是唯一允许 import `pydantic_ai` 的包。
- `storage` 是唯一允许 import `sqlalchemy` 的包。
- `runtime` 是唯一允许 import `jupyter_client` 的包。

---

## 3. 目录结构

```
bioquorum/
├── pyproject.toml            # uv；一个 Python 包；import-linter 契约写在这里
│   （用户的项目目录：bioquorum.toml 配置 + .bioquorum/{project.json, bioquorum.db, cas/}，数据跟着项目走）
├── uv.lock
├── CLAUDE.md                 # 给 AI 协作者的约束，指向本文和 docs/adr/
├── src/bioquorum/
│   ├── core/                 # ids.py, events.py, messages.py, models.py, hashing.py, status.py
│   ├── storage/              # 事件表 + 投影表（SQLAlchemy + Alembic）；projections/
│   ├── policy/               # permissions.py, classification.py, audit.py, secrets.py
│   ├── runtime/              # kernel.py（接口）, jupyter.py, fake.py, summarize.py, shell.py
│   ├── provenance/           # cas.py, lineage.py, env.py, bundle/
│   ├── connectors/           # base.py + 各连接器
│   ├── skills/               # loader.py + builtin/
│   ├── mcp/                  # client.py, server.py
│   ├── tools/                # registry.py, invoke.py, spec.py, builtin/
│   ├── models/               # registry.py, roles.py, capabilities.py, routing.py, cost.py, convert.py
│   ├── review/               # claims.py, checks/, rerun.py, consensus.py
│   ├── agent/                # loop.py, plan.py, compaction.py, prompts/
│   ├── server/               # v0.2+
│   └── cli/                  # main.py (typer)
├── bundle-spec/              # 研究包规范 + JSON Schema（从 v0.1 起有 manifest v0）
├── evals/                    # 评测任务；injected-errors/ 注入错误语料
├── tests/                    # unit/ integration/ live/ e2e/；cassettes/ 录制的模型响应
├── docs/                     # 本文、PRODUCT、adr/、mkdocs
└── .github/workflows/
```

【可调】包的边界比文件数重要。一个包一开始可以只有 `__init__.py` 和一个模块，不要为了填满目录树写空代码。

---

## 4. 技术栈

| 用途 | 选型 | 标记 | 备注 |
|---|---|---|---|
| 语言 / 包管理 | Python 3.12+ / uv | 承重 | |
| 领域模型 | Pydantic v2 | 承重 | `core/` 只依赖它 |
| Agent 内核 | Pydantic AI v2 | 可调 | 只在 `models/` 出现；跨 provider thinking、FallbackModel、MCP 客户端现成 |
| 模型网关 | 不内置 LiteLLM | 承重 | 网关由用户作为 OpenAI 兼容端点配置（ADR-004） |
| 存储 | SQLite（WAL）+ SQLAlchemy 2 + Alembic | 可调 | 事件表 + 投影表；团队版可换 Postgres |
| 执行 | jupyter_client + ipykernel / IRkernel | 可调 | 标准协议，可导出 ipynb；接口抽象见 §6.2 |
| 环境 | pixi | 可调 | 每个项目一个环境；锁文件 hash 进 Run |
| MCP | 官方 `mcp` Python SDK | 承重 | 客户端和服务端都用 |
| ID | ULID 字符串 | 承重 | 可排序，见 §5.1 |
| 哈希 | SHA-256 | 承重 | 见 §5.6 |
| CLI | typer + rich | 可调 | |
| Web | FastAPI + SSE；前端见 §10 | 可调 | |
| 测试 | pytest、respx / 自录 cassette、FakeKernel | 承重 | 见 §6.8 |
| 质量 | ruff、pyright、import-linter、pre-commit | 承重 | |
| 评测 | Inspect AI | 可调 | |
| 文档 | mkdocs-material | 可调 | |

---

## 5. 核心数据模型 【承重】

`core/` 是本文第 5 节的代码化。以代码为准，本节描述意图和规则。

### 5.1 ID 与时间

- 所有实体 ID 是 ULID 字符串（26 字符，时间有序）。
- 时间戳为 UTC、RFC 3339、毫秒精度。
- 事件在会话内有连续递增的 `seq`，由 storage 分配，用于排序和分叉。

### 5.2 事件信封

```python
Event(
    id: ULID,
    session_id: ULID,
    seq: int,                       # 会话内递增
    ts: datetime,
    type: str,                      # 见 5.3
    schema_version: int,            # 该事件类型的 payload 版本，从 1 开始
    actor: "user" | "agent" | "reviewer" | "system" | "tool",
    causation_id: ULID | None,      # 触发本事件的事件
    payload: <该类型的 Pydantic 模型>,
)
```

规则：
- 事件只追加，不修改、不删除。纠错用新事件。
- 每种事件类型是一个 Pydantic 模型，注册在 `core/events.py`；`schema_version` 升级时保留旧模型并提供升级函数。
- 流式输出的 token 增量不是事件。它们通过 SSE 的瞬时帧推送，完整消息完成后才写 `assistant_message` 事件。

### 5.3 事件类型（v0.1 起）

| 类别 | 类型 |
|---|---|
| 项目 | `project_created`（唯一一个 session_id 为空的事件类型） |
| 会话 | `session_created`, `session_forked`, `session_title_set` |
| 对话 | `user_message`, `assistant_message` |
| 模型 | `model_call_started`, `model_call_finished`, `model_call_failed` |
| 工具 | `tool_call_requested`, `tool_call_finished`, `tool_call_failed` |
| 计划 | `plan_proposed`, `plan_approved`, `plan_step_done`, `plan_abandoned` |
| 权限 | `permission_requested`, `permission_decided` |
| 执行 | `run_started`, `run_output`, `run_finished`, `run_failed`, `run_cancelled`, `run_timed_out` |
| 产物 | `artifact_version_created`, `artifact_status_changed` |
| 结论 | `claim_extracted`, `claim_status_changed` |
| 审查 | `review_started`, `review_finding`, `review_finished`, `finding_resolved` |
| 复现 | `rerun_started`, `rerun_finished`, `bundle_exported` |
| 压缩 | `context_compacted` |
| 系统 | `error` |

### 5.4 规范消息格式

模型上下文和事件日志共用这一套类型，定义在 `core/messages.py`：

```python
Message(role: "system" | "user" | "assistant" | "tool", parts: list[Part])

Part =
  | TextPart(text)
  | ThinkingPart(provider: str, opaque: bytes | None, text: str | None)   # 跨 provider 只保留 text 或丢弃
  | ToolCallPart(call_id: str, name: str, args: dict)
  | ToolResultPart(call_id: str, content: list[TextPart | ImageRefPart | DataRefPart],
                   trust: "trusted" | "untrusted", is_error: bool)
  | ImageRefPart(ref: DataRef, mime: str, thumbnail_ref: DataRef | None)
  | DataRefPart(ref: DataRef, summary: str)
```

- `ToolCallPart.call_id` 是 BioQuorum 分配的 ULID；到各 provider 的 ID 映射在 `models/convert.py` 内部维护，不进入事件。
- `assistant_message` 和 `model_call_finished` 事件可携带 `provider_raw: bytes`，只用于调试和审计，任何逻辑都不得依赖它。
- 发给模型的上下文 = 事件投影出的 Message 列表 + `context_compacted` 事件里的摘要。不存在第二份"对话历史"。

### 5.5 实体（均为事件的投影）

```python
Project(id, name, root_dir, data_dirs[], classification: public|internal|confidential,
        model_policy, env_id, created_at)

Session(id, project_id, title, parent_session_id?, forked_at_seq?, status)

Run(id, session_id, kernel_id, language, code, code_hash, env_lock_hash,
    inputs: [DataRef], outputs: [DataRef], stdout_ref, run_key,
    status: running|succeeded|failed|cancelled|timed_out, started_at, ended_at)

DataRef(uri, content_hash, size, kind: file|dataframe|anndata|figure|other)

Artifact(id, project_id, name, kind: figure|table|report|dataset|structure|notebook)
ArtifactVersion(id, artifact_id, version, parent_version_id?, content_hash,
                run_id, event_range: (seq_from, seq_to), description,
                status: VerificationStatus)

Claim(id, session_id, source_event_id, text,
      kind: numeric|citation|execution|causal|plan,
      refs: [RunRef | ArtifactRef | CitationRef],
      status: VerificationStatus)

ReviewFinding(id, session_id, claim_id?, artifact_version_id?, check: str,
              severity: info|warning|error, evidence: str,
              reviewer_model: str, status: open|addressed|dismissed)

ModelCall(id, event_id, provider, model, role, classification,
          input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
          cost_usd, latency_ms)

AuditEntry(id, ts, actor, action, target, classification, detail)
```

所有投影表都可以用 `bq rebuild` 从事件重建；重建结果必须与原表一致，这是 storage 层的核心测试。

### 5.6 哈希与身份规则

- 算法统一为 SHA-256，十六进制小写。
- **文件**：对字节流哈希。
- **内存对象**：先序列化为规范形式再哈希。DataFrame 用 parquet（固定压缩参数、按列名排序），AnnData 用 h5ad，matplotlib 图用 PNG 加嵌入的源数据 hash；其他对象用 pickle 协议 5 并标记为"弱哈希"。
- **代码**：去掉行尾空白、统一换行后哈希，得到 `code_hash`。
- **环境**：`env_lock_hash = sha256(pixi.lock)`。
- **Run 身份**：`run_key = sha256(code_hash + env_lock_hash + sorted(input content_hash))`。两次执行 `run_key` 相同而输出 hash 不同，就是复现失败；数值型输出允许按容差比较，容差记录在比较结果里。
- 内容寻址存储路径：`<store>/<hash[:2]>/<hash>`。

### 5.7 验证状态机

```
unverified ──review 通过──▶ reviewed ──rerun 一致──▶ reproduced
     │                         │                         │
     └──── 任一检查 error ─────┴──── rerun 不一致 ────────┴──▶ disputed
                                                            │
disputed ──finding 全部 addressed 后重新 review──▶ reviewed
```

- Claim 和 ArtifactVersion 共用这套状态。
- 状态只能通过 `claim_status_changed` / `artifact_status_changed` 事件改变，事件里写明原因和触发的 finding 或 rerun。
- 一个回答的整体状态取其所有 Claim 的最低状态。

---

## 6. 关键模块设计

### 6.1 模型层 `models/`

配置（`~/.bioquorum/config.toml` 或项目内 `bioquorum.toml`）：
```toml
[providers.anthropic]
api_key = "keyring:anthropic"           # 密钥在系统钥匙串，这里只写引用
[providers.local]
type = "openai-compatible"
base_url = "http://localhost:8000/v1"
allowed_classifications = ["public", "internal", "confidential"]

[roles]
worker   = "anthropic:<model>"
reviewer = "local:<model>"              # 按审查评测成绩选择，可与 worker 同家族（ADR-013）
# planner 和 summarizer 可选，缺省回退到 worker
```

- 【承重】角色集合为 `worker`、`reviewer`、`planner`、`summarizer`；v0.1 只要求前两个。`reviewer` 默认取审查评测成绩最好的模型，缺省回退到 worker；可选 `prefer_different_family = true`，成绩相近时优先不同家族（ADR-013，取代 ADR-011）。
- 【可调】能力矩阵 `capabilities.py`：上下文长度、并行 tool call、thinking、视觉、缓存方式、单价。初始数据取自 models.dev 快照，用户可覆盖。
- 【承重】路由 `routing.py`：先按项目分级过滤 provider，再取角色配置，不可用时走回退链。违反分级的调用抛出 `PolicyViolation` 并写审计，不降级。
- 【承重】`convert.py` 是规范消息格式与 Pydantic AI 类型之间唯一的转换点。
- 【可调】tool call JSON 出错时修复或重试一次；重试仍失败则写 `model_call_failed`。
- 实现要点（v0.1）：
  - 只用 Pydantic AI 的底层 `Model.request()` 作 provider 适配器，不用它的 `Agent`：循环、事件、`invoke()` 都是我们自己的。依赖用 `pydantic-ai-slim[按需 provider]`，不装遥测组件。
  - 工具调用 ID：响应进来时给每个调用分配 ULID，后续轮次直接把 ULID 当 provider 的 tool_call_id 发回去（DeepSeek chat、OpenAI Responses 实测可行，前提是不回传服务端条目 ID），所以映射不需要持久化。
  - Thinking：按产生它的**家族**保存在 `ThinkingPart.provider`，`opaque` 存 provider 回放所需的元数据，只回放给同一家族，其余丢弃。DeepSeek 思考模式缺少上一轮 reasoning_content 时会直接拒绝请求。
  - OpenAI 新模型带工具必须走 Responses API（`api = "responses"`，openai 的默认值）；其他 OpenAI 兼容服务走 chat。
  - provider 默认分级：远端 provider 只允许 `public`、`internal`；`base_url` 指向本机的允许全部分级；可用 `allowed_classifications` 覆盖。密钥只写引用 `env:NAME`，配置里写明文密钥会被拒绝；解析后的值登记到 `policy/secrets` 以便打码。
  - 配置来源：`~/.bioquorum/config.toml`（或 `$BQ_CONFIG`）与项目 `bioquorum.toml` 合并，项目覆盖全局。`bq models` 显示当前分级下每个角色的模型链。
  - `ModelClient.complete()` 写 `model_call_started/finished/failed`，沿回退链重试；它不写 `assistant_message`，那是调用方（agent / review）的事。

### 6.2 执行层 `runtime/`

【承重】接口：
```python
class Kernel(Protocol):
    kernel_id: str; language: str
    def execute(self, code: str, *, timeout: float | None) -> AsyncIterator[KernelOutput | ExecutionEnd]
    async def interrupt(self) -> None
    async def restart(self) -> None
    async def snapshot_variables(self) -> dict[str, DataRef]
    async def shutdown(self) -> None
```
`execute` 产出若干 `KernelOutput`，最后**恰好一个** `ExecutionEnd(status=ok|error|cancelled|timed_out)`；超时和中断也必须正常结束流。把一次执行记成 Run 事件（`run_started` … `run_finished`）的是 `tools/runs.py`，因为它同时需要 runtime、provenance 和 storage，而这三者是同层兄弟，互不 import。
两个实现：`JupyterKernel`（生产）和 `FakeKernel`（测试，按预设脚本返回输出）。除 `runtime/` 自身的集成测试外，所有测试用 `FakeKernel`。

- `KernelManager`：每个 Session 一个 Python kernel，按需 R kernel；跑在项目的 pixi 环境里；空闲超时回收，回收前可快照变量。
- 【承重】并发模型：单进程 asyncio；同一 Session 内的 Run 串行；不同 Session 并行。`interrupt()` 产生 `run_cancelled`，超时产生 `run_timed_out`，两者都正常结束 Run，不留悬挂状态。
- `summarize.py`：输出压缩成模型可看的摘要。DataFrame 给 shape、dtypes、head、describe；AnnData 给维度和 obs/var 列名；图存为产物并给缩略图。摘要的大小有上限，超出截断并注明。
- 【可调】血缘 hook：kernel 启动时注入，包装 pandas、anndata、numpy、matplotlib 的读写函数，把路径和 hash 记到当前 Run。hook 失效时 Run 仍然成功，只是 inputs/outputs 为空并打标记，不能因为 hook 让分析失败。

### 6.3 溯源层 `provenance/`

- `cas.py`：按 §5.6 的规则存取；写入时校验 hash。
- `env.py`：读取 pixi.lock，算 `env_lock_hash`；重跑时按锁文件重建环境。
- 【承重】**研究包 manifest 从 v0.1 起存在。** 最小字段：`spec_version`、`tool_version`、`created_at`、`project.classification`、`runs[]`（code、code_hash、env_lock_hash、inputs、outputs）、`artifacts[]`、`models_used[]`、`files{path: hash}`。字段只增不删，`bundle-spec/` 里的 JSON Schema 与代码同步。
- 研究包目录：
```
bundle/
├── manifest.json
├── README.md            # 自动生成：做了什么、怎么重跑、Methods 草稿
├── conversation.jsonl   # 事件日志，可选脱敏
├── runs/<id>/code.py
├── env/pixi.lock
├── data/manifest.json   # 输入数据来源 URI 和 hash；可选把数据打包
├── artifacts/
├── review/findings.json
└── rerun.sh             # 只需要 pixi，不需要 BioQuorum
```
- `rerun(artifact_version)`：重建环境，按 run_key 链重新执行，比较输出 hash，写 `rerun_finished` 并触发状态机。

### 6.4 工具层 `tools/`

【承重】工具定义：
```python
ToolSpec(
    name: str,
    description: str,
    input_schema: dict,                       # JSON Schema
    handler: Callable[..., Awaitable[ToolResult]],
    permission: "read_fs" | "write_fs" | "execute" | "network" | "connector" | "external_mcp",
    output_trust: "trusted" | "untrusted",   # 只有纯本地计算结果可以是 trusted
    classification_floor: "public" | "internal" | "confidential" | None,  # 该工具会把数据发到哪一级
)
```
- `classification_floor` 的含义：该工具的目的地**最多能接收哪一级项目的数据**。`None` 表示数据不出本机，任何项目都可用；`public` 表示只有 public 项目可用；以此类推。项目分级高于它即拒绝。
- `target(args, ctx)` 给出权限针对的对象（解析后的绝对路径、主机名等），必须与 handler 实际触碰的对象一致；文件工具先 `resolve()`（跟随符号链接）再做权限检查。
- `registry.py` 持有所有 ToolSpec。agent 的工具列表和 `mcp/server.py` 的工具列表都由它生成，不允许手写第二份。
- 【承重】`invoke(spec, args, ctx)` 是唯一执行入口，顺序固定：分级检查 → 权限检查（可能挂起等待用户决定）→ 写 `tool_call_requested` → 执行 → 写审计 → 包装为 `ToolResultPart(trust=spec.output_trust)` → 写 `tool_call_finished` 或 `tool_call_failed`。
  - 参数校验在分级检查之后、权限检查之前，失败写 `tool_call_failed`。
  - 审计由 `tool_call_finished` / `tool_call_failed` / `error(policy_violation)` 事件投影到 `audit_entries`，不另写。
  - `invoke` 对工具层面的问题（拒绝、参数错、handler 抛异常）不抛异常，一律返回 `is_error=True` 的 `ToolResultPart`；拒绝文本是 BioQuorum 自己生成的，标 `trusted`。错误文本写入事件前经 `policy/secrets.redact()`。
  - 输出可能来自远端（`network` / `connector` / `external_mcp`）的工具，`ToolSpec` 构造时禁止声明 `output_trust=trusted`。
- 外部 MCP 服务器的工具自动包装为 ToolSpec，`permission=external_mcp`，`output_trust=untrusted`。
  - 名字加命名空间 `<server>__<tool>`；`classification_floor` 取该服务器在配置里的 `clearance`，远端服务器缺省 `public`，只有标为 `local` 的才对所有分级可用；权限目标是 `mcp:<server>`。
  - 分层：`mcp/` 在 `tools/` 之下，不能 import 工具层。所以 `mcp/client.py` 和 `mcp/server.py` 只做协议转换，输入输出都是中立类型；包装成 ToolSpec 在 `tools/external.py`，MCP 出口接到 `invoke()` 在 `tools/exits.py`，组装在 `cli/`。`mcp` SDK 只允许在 `mcp/` 内 import（import-linter）。
  - MCP 出口返回的 untrusted 文本用 `core.messages.fence_untrusted()` 包裹，agent 出口用同一套分隔符；服务器的 `instructions` 说明分隔符的含义。
  - 录制与回放（`mcp/replay.py`）：`bq mcp record` 把真实服务器的工具列表和调用结果存为 cassette，配置里写 `replay = "<path>"` 就改用进程内回放，不联网。测试默认用回放，`live` 测试每晚检查上游是否漂移。

### 6.5 审查层 `review/`

1. **抽取 Claim**：从 `assistant_message` 里抽出可检查的结论，写 `claim_extracted`。抽取用 reviewer 模型；抽不出 Claim 的回答整体保持 `unverified`。
2. **检查器**（`checks/`，每个实现 `Check` 接口，独立注册）：

| 检查 | 需要模型 | 说明 |
|---|---|---|
| `execution_evidence` | 否 | 声称算过的东西，事件里有没有成功的 Run |
| `value_match` | 否 | 结论里的数值与 Run 输出或源文件是否一致 |
| `citation_resolves` | 否 | DOI / PMID 能否解析，标题作者是否匹配 |
| `plan_complete` | 否 | 已批准计划的步骤是否都完成 |
| `citation_supports` | **是** | reviewer 模型读摘要，判断是否支持结论 |
| `method_sanity` | **是** | 常见统计错误，如未做多重检验校正、样本量过小 |

   - 检查只看 `review/evidence.py` 的中立结构（`Evidence`：runs、citations、plan、answer；`ClaimInput`：文本、类型、显式 run 引用），不碰 storage 和 models。`Evidence.from_events()` 从会话事件构造（引用来自文献类工具返回里的 PMID/DOI），`Evidence.from_case()` 从评测语料构造，所以同一套检查在两处行为一致。
   - 规则写成通用规则，不针对语料里的个案；实测与局限见 [research/review-checks.md](research/review-checks.md)。`tests/unit/review/test_corpus.py` 把语料上的结果固定为回归基线。
   - 审查流程（`review/service.py`）：`review_started` → 每条 `claim_extracted` → 每条 `review_finding` → `review_finished` → 每条 `claim_status_changed`。确定性检查的 finding 记 `reviewer_model = "deterministic"`。模型检查出错不影响确定性检查的结果，错误单独返回。
   - 发给 reviewer 的来源摘要是工具输出，按 ADR-007 用 `fence_untrusted()` 包裹。
   前四个是确定性检查，不消耗模型调用，v0.1 先做。评测报告要分别报告两组的召回率和误报率，不能混在一起。v0.0 对照实验在确定性可核实的错误上没有发现同家族盲区（docs/research/reviewer-experiment.md）；判断类检查上的家族差异在 v0.2 补测。
3. **重跑**（可选）：关键 Run 在全新 kernel 里重新执行，走 §6.3 的 rerun。
4. **输出**：`review_finding` 写入事件流。主 agent 在下一轮必须对每条 `error` 级 finding 作出回应；【承重】同一条 finding 的往返次数上限为 2，超过则保持 `disputed` 并交给用户。
5. 【可调】共识模式见 §10。

### 6.6 编排层 `agent/`

- 主循环：用户消息 → 复杂任务先出 Plan 卡片等待批准 → 循环执行 tool call → 回答 → 触发审查。每一步都通过事件推进，循环本身无状态，崩溃后可从事件日志恢复。
- 【承重】agent 层不直接调用 runtime、connectors 或 mcp；一律经过 `tools.invoke()`。
- Compaction：上下文接近上限时用 summarizer 压缩早期历史，写 `context_compacted`。被压缩的只是模型看到的上下文，原始事件全部保留。
- Prompt 结构对缓存友好：系统提示、工具列表、skill 目录保持固定顺序；时间戳等易变内容放末尾。
- Prompt 中 untrusted 内容用固定分隔符包裹，系统提示明确说明分隔符内的指令不得执行。
- 实现要点（v0.1，`agent/loop.py`）：每一步都用 `agent/context.conversation()` 从事件重建上下文；工具调用一律 `invoke_by_name()`；没有工具调用的 assistant 消息即为回答；到达步数上限时不带工具再问一次，保证用户拿到回答。`invoke()` 保证每个调用 ID 都以 `tool_call_finished` / `tool_call_failed` 结束（拒绝也算），否则重建的历史里会有没有结果的工具调用，provider 会拒绝。
- 入口：`bq run` 提问 → 循环 → 回答 → 自动审查（`review.review_answer`）；`bq review [--answer-file]` 审查会话最后一条回答，或审查外部（例如 Claude Code 经 MCP）写出的回答，证据都取自会话事件。

### 6.7 策略层 `policy/`

- 权限卡片：文件目录、代码执行、网络、连接器、外部 MCP。授权范围：一次 / 本会话 / 本项目 / 全局，可查看和撤销。
- 分级与路由联动见 §6.1；分级与工具联动见 §6.4 的 `classification_floor`。
- 审计只追加，可导出 CSV / JSON。
- 密钥通过 `keyring:` 引用解析，解析后的值只存在于 provider 客户端对象里，禁止出现在事件、日志、异常信息中。

### 6.8 测试架构 【承重】

| 层 | 方式 | 联网 | 真实模型 | 真实 kernel |
|---|---|---|---|---|
| `core/` | 纯单元测试；投影重建一致性 | 否 | 否 | 否 |
| `models/` | 录制的 provider 响应（cassette）回放；键由规范请求算出并把 ULID 替换为占位符；`live` 测试走真实 provider | 否 | 否 | 否 |
| `runtime/` | 单元测试用 `FakeKernel`；`JupyterKernel` 测试标记 `integration` | 否 | 否 | 标记后是 |
| `connectors/` | 固定的真实响应样本；`live` 标记的测试每晚跑一次 | 标记后是 | 否 | 否 |
| `review/` | `evals/injected-errors/` 注入错误语料；确定性检查必须 100% 通过 | 否 | cassette | 否 |
| `agent/` | 端到端脚本，模型走 cassette，kernel 用 Fake | 否 | 否 | 否 |
| 评测 | Inspect AI 任务，真实模型，手动或定期触发 | 是 | 是 | 是 |

CI 默认只跑不联网、不调模型、不启 kernel 的测试。任何新模块合入前必须有对应层的测试。

---

## 7. 扩展点

| 扩展 | 方式 | 难度 |
|---|---|---|
| Skill | 一个 SKILL.md 文件夹（Agent Skills 标准） | 最低 |
| Connector | 继承 `connectors.base.Connector` 并注册 entry point；或直接接任意 MCP Server | 低 |
| Review check | 实现 `Check` 接口并注册 entry point | 中 |
| Model provider | 任何 OpenAI 兼容接口直接配置；特殊协议走 Pydantic AI provider | 中 |

扩展接口在 v1.0 前允许变动，但变动必须记入 CHANGELOG。

---

## 8. 安全模型（摘要，完整内容见 SECURITY.md）

- 威胁：模型生成的代码误删或篡改数据；prompt injection 经工具返回内容导致数据外泄；第三方依赖或 MCP 服务器被投毒。
- 从 v0.1 起的防护：权限卡片 + 目录授权 + 审计 + untrusted 标记 + 分级路由硬拒绝。**明确告知用户此时没有 OS 沙箱。**
  - v0.1 验收中实测到：worker 会在 `python_execute` 里用 `requests` 自己联网（下载全文）。kernel 里的代码不经过 `invoke()` 的网络权限和分级检查，所以 **confidential 项目在沙箱就位前不能视为隔离**。目前只有提示词层面的软约束（要求联网走工具），它不是安全控制。
- 沙箱（bubblewrap / sandbox-exec / Docker，网络白名单代理，外部 MCP 进程隔离）在路线图上单列版本；它是 `runtime/` 内部的实现替换，不改变其他层的接口。

---

## 9. 让架构可执行的机制

架构只写在文档里是守不住的，以下机制和代码一起进第一个 commit：

1. **`CLAUDE.md`**：指向本文和 `docs/adr/`；列出 §1 的全部承重原则；要求新模块先写测试；要求改动承重决定先写 ADR。
2. **import-linter 契约**写在 `pyproject.toml`，覆盖 §2 的全部规则；pre-commit 和 CI 都跑。
3. **`core/` 先于一切功能实现**，§5 的每个模型都有测试；投影重建一致性测试从第一天起存在。
4. **`docs/adr/NNN-title.md`**：每条不超过一页，格式为"背景 / 决定 / 后果"。已决定的事不在会话中重新争论，要改就新增 ADR。
5. **pre-commit**：ruff、pyright、import-linter、pytest 的快速子集。

---

## 10. 未来方向（备注，不构成约束）

- **Web UI**：React + Vite + TS + shadcn/ui，三栏布局，构建后作为静态文件随 Python 包发布。推后到科学层和审查层稳定之后；在那之前 CLI 和 MCP Server 模式是唯一入口，Claude Code / opencode 充当 UI。
- **Specialists**：markdown 定义的子 agent，可单独配模型和权限，支持并行。
- **共识模式**：两个不同家族的模型独立完成同一任务，自动对比结论分歧。
- **code-action 降级**：对 tool call 能力弱的模型，改为直接生成 Python 代码调用工具。
- **查看器**：Mol*（3D 结构）、RDKit.js（分子）、igv.js（基因组）。
- **远程算力**：SSH、Slurm、Modal；**桌面应用**：Tauri；**团队版**：Postgres、SSO。

---

## 11. 架构决策记录（ADR）索引

详见 `docs/adr/`。初始条目：

| 编号 | 决定 |
|---|---|
| 001 | 后端只用 Python |
| 002 | 事件日志是唯一写入路径，其余表为投影 |
| 003 | 自有规范消息格式，Pydantic AI 类型只在 `models/` 内出现 |
| 004 | 不内置 LiteLLM |
| 005 | 一份工具定义同时生成 agent 工具和 MCP Server |
| 006 | `tools.invoke()` 是唯一执行咽喉 |
| 007 | 工具返回内容默认 untrusted |
| 008 | ULID 作为 ID，SHA-256 作为哈希，Run 身份由 code、env、inputs 三者决定 |
| 009 | Jupyter 协议作为 kernel 实现，但以 `Kernel` 接口隔离 |
| 010 | 研究包 manifest 从 v0.1 起存在，字段只增不删 |
| 011 | ~~reviewer 默认与 worker 不同家族~~（被 013 取代） |
| 012 | Web UI 推后，CLI 与 MCP Server 为首批入口 |
| 013 | reviewer 按审查能力选择，家族多样性改为可选 |
