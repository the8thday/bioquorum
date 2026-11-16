# 自研科研 Agent Harness：调研报告与技术路线图

> 日期：2026-11-11
> **注（2026-11-13）**：本文记录的是 2026-11-11 的调研和当时的选型倾向。此后技术选型以 [../ARCHITECTURE.md](../ARCHITECTURE.md) v0.2 和 [../adr/](../adr/) 为准；其中"自托管 LiteLLM Proxy 作网关"、"按人力 6 个月"等结论已被取代。
> 目标：做一个和 Claude Science 同类、**不绑定模型**的科研工作台（Harness），能接入 Claude / GPT / Gemini / DeepSeek / Qwen / 本地模型。

---

## 第一部分：调研

### 1. Claude Science 是什么（标杆）

2026-06-30 发布 beta。它没有发布新模型，只是围绕已有 Claude 模型做了一层 Harness。

| 维度 | 具体做法 |
|---|---|
| 形态 | 桌面客户端，跑在用户自己的机器上（macOS / Linux / Win11）。本地有后台进程，UI 可在浏览器打开；状态存放在 `~/.claude-science` |
| Agent 结构 | 一个总协调 agent，可以并行拆出多条子任务（delegation）；用户也能自定义 Specialist；多步任务先出 Plan，用户批准后再执行 |
| Reviewer | 内置的验证 agent，自动复查：结果是否真的跑出来过、数值和源文件是否一致、引用/DOI 是否支持结论、Plan 步骤是否做完。**它不重跑分析，而且和主 agent 用的是同一个模型** |
| 执行 | Python/R 常驻 kernel（变量留在内存，空闲约 30 分钟回收）+ shell。环境由内置 micromamba 管理（conda-forge/bioconda/PyPI wheels/CRAN/Bioconductor），无 root |
| 算力 | 本地；SSH 主机；Slurm（检测到 sbatch 就自动提交）；Modal（用户自己的账号付费）；BioNeMo NIM（云端或本地 Docker） |
| 沙箱 | OS 级沙箱（Linux 上用 bubblewrap），文件按目录授权，网络默认全部拒绝、经本地代理放行。远程作业不在沙箱内 |
| 权限卡片 | 每类动作都弹卡片，授权范围可选：仅一次 / 本对话 / 本项目 / 全局；可以随时撤销 |
| Skills / Connectors | 基于 Agent Skills（SKILL.md）和 MCP。约 60 个精选数据库（Ensembl、UniProt、gnomAD、ClinVar、GTEx、PDB、ChEMBL、PubChem、GEO、cBioPortal、OpenAlex……），另有 AlphaFold2 / Boltz-2 / Chai-1 / ESM / Evo2 / scvi-tools 等模型 skill |
| 溯源 | 每个产物（artifact）有版本，并附带生成它的代码、环境和完整对话。图可以用自然语言修改（agent 会改自己写的代码）；会话可以分叉 |
| 内部实现（来自第三方拆解，单一来源） | 用自定义 JSON 协议的 kernel worker，不是 Jupyter；MCP 调用只在一个独立的 control REPL 里发生；拦截 pandas/numpy/anndata/matplotlib 的 I/O 来记录数据血缘；存储用 SQLite |

它的弱点，也就是自研的切入点：
- **只能用 Claude**，用户无法自己选择或混用模型。
- 不支持 ZDR，不签 BAA/HIPAA，**文件内容会发送给 Anthropic**。有严格数据合规要求的药企很难放进生产环境。
- Reviewer 和主 agent 是同一个模型，属于"自己检查自己"。
- 调度器只支持 Slurm（不支持 PBS/LSF/K8s）；Modal 没有花费上限；用量受 5 小时和每周额度限制。

### 2. 开源同类：三个都叫 "Open Science" 的项目

| | synthetic-sciences/openscience | aipoch/open-science | ai4s-research/open-science |
|---|---|---|---|
| 技术栈 | Bun + TS（Hono 服务端），SolidJS 浏览器 UI，Electron 壳 | Electron + Node24 + React + Prisma/SQLite | Tauri2 + React，Rust core（`osd-core`） |
| Agent 运行时 | 自研，自称"受 OpenCode 启发" | 自研，也可以切到 Codex / Claude 后端 | **直接内嵌 OpenCode 作 sidecar**，并通过 ACP 接入 Claude Code / Codex / Gemini CLI |
| 模型接入 | models.dev 目录，支持 75+ 家；Ollama/LM Studio；有托管钱包 Ace | 25 个预设；自定义网关（Messages/Chat/Responses）；可用订阅登录 | 继承 OpenCode 的 provider 体系 |
| Skills | 371 个（大量来自 K-Dense 等） | 24 个内置 + 市场里 525 个 | ai4s-skills 流水线（调研→实验→写论文→审计） |
| 溯源 | trace + review pass | **`.science` 包**：导出/导入对话分支、文件版本、notebook 和验证证据，可以重跑 | `.openscience/provenance.jsonl` + SQLite |
| 沙箱 | 只有权限模式，没有 OS 沙箱 | Linux 上 notebook 跑在 bubblewrap 里 | 有审批机制；Docker 沙箱还只是计划 |
| 算力 | 本地 / Modal / BioNeMo | SSH / Slurm / K8s / HF Jobs / Modal / Ray | SSH / Slurm / Modal |
| License / 热度 | Apache-2.0，4.4k★，有 YC 背景 | Apache-2.0，5.5k★，v1.2.1，有 arXiv 论文 | MIT，2.1k★，v0.7.2，beta |
| 注意 | 登录后**默认上传完整模型调用 trace** | 安全文档写得最完整 | 明确把 agent 运行时当作可替换组件 |

结论：
1. "开源版 Claude Science" 这件事，**通用功能已经有三家做到 70–80 分**。再从零做一个通用版，差异化很弱。
2. 三家的共同做法：**agent 运行时外包或者做薄，把力气花在科学层**（kernel、溯源、reviewer、connector、算力）。ai4s 的设计文档原话是 "provenance and review are the real moat"。
3. 三家都没有认真做的地方：**跨模型异构审查**、**企业私有化/合规**（审计、SSO、内网模型、GxP 式的可追溯性）、**基于评测的模型路由**。

### 3. 其他可以直接复用的积木

**通用 Harness 底座（可 fork 或作为内核）**
| 项目 | 语言 / License | 评价 |
|---|---|---|
| opencode | TS / MIT | 最成熟：client/server 架构、75+ provider、子 agent、MCP、Skills、每个 agent 可单独配模型和权限。ai4s 就是基于它做的 |
| pi (pi-mono) | TS / MIT | 内核极小（只有 4 个工具），`pi-ai` 统一了约 41 家 provider，适合自己完全掌控 |
| Pydantic AI v2 + Harness | Python / MIT | 类型安全；跨 provider 的 thinking 回放处理得最好；FallbackModel；能接 Temporal/DBOS 做持久化执行 |
| OpenHands SDK V1 | Python / MIT | 基于 LiteLLM；事件溯源；Docker/远程 workspace；自带评测框架 |
| LangGraph Deep Agents | Python / MIT | 持久化图 + 人在回路（HITL），但抽象层较重 |
| Claude Agent SDK / Codex CLI | — | 分别绑定 Anthropic / OpenAI 自家模型，**不适合做跨模型内核**，只适合当参考设计 |

**模型抽象层**：LiteLLM（覆盖最广，但 **2026 年两次 PyPI 投毒**，必须锁版本并校验 hash）、any-llm（Mozilla，封装各家官方 SDK）、Vercel AI SDK（TS）、OpenRouter（托管）、Pydantic AI models。

**科学层**
- 工具和数据库：**ToolUniverse**（Harvard，2700+ 工具，原生 MCP），Biomni 的工具封装和 Know-How 文档（注意 `commercial_mode`），BioMCP，Open Targets 官方 MCP，BioContextAI 注册表
- Skills：**K-Dense scientific-agent-skills**（177 个，每个 skill 的 license 不同，需要逐个审计）、NVIDIA BioNeMo Agent Toolkit（11 个 NIM）
- 文献：**PaperQA2**（Apache-2.0），可以直接做成文献子 agent
- 设计范式：Google Co-Scientist 的"生成→反思→Elo 排名→进化→元审查"循环；Sakana AI Scientist 的实验树搜索（这一项只借思路，它的代码 license 有使用限制）
- 执行：Jupyter kernel（标准，可导出 ipynb）、E2B（可自托管）、Modal、Docker/Apptainer、bubblewrap/Landlock；HPC 方面有 submitit / Parsl / slurm_mcp
- 环境：**pixi**（conda + PyPI 合用一个锁文件）或 micromamba + conda-lock；每个产物记录锁文件 hash
- 评测：BixBench、LAB-Bench（及 LAB-Bench 2）、Biomni-Eval1 / BiomniBench、BioML-bench、ScienceAgentBench、CORE-Bench（可复现性）、PaperBench、AstaBench（基于 Inspect AI）

### 4. 跨模型的核心技术难点（决定架构）

| 难点 | 对策 |
|---|---|
| tool call 格式各家不同（Anthropic content block / OpenAI tool_calls / Responses function_call / Gemini functionCall），JSON Schema 支持的子集也不同 | 内部定义一套**规范消息格式**，每家写一个 adapter；输出做 JSON 修复和重试；对 tool call 能力弱的模型提供 **code-action 降级**（smolagents 的思路：直接写 Python 调用工具） |
| Reasoning/thinking：Anthropic 的 thinking 带签名，必须原样回传；OpenAI 是加密的 reasoning item；两者都不能跨家使用 | 历史记录里**同时存规范格式和 provider 原始 parts**；会话中途换模型时，把对方的 thinking 丢弃或降级为文本。thinking 会占用 max_tokens 导致 tool call 被截断，要专门检测 |
| Prompt caching 机制不同（Anthropic 显式 breakpoint，OpenAI/Gemini 隐式） | system prompt、工具列表和 skill 目录保持稳定且有序，不在开头放时间戳；易变内容放在末尾；按 provider 插入缓存标记 |
| 上下文窗口从 128K 到 1M+，token 计数方式也不同 | 用 models.dev 等能力表；**大数据留在 kernel 里，只把摘要交给模型**（Claude Science 就是这么做的）；compaction 自己实现，不依赖 provider 托管 |
| 并行 tool call 和 call ID 格式不一致 | 翻译时保证 ID 配对；有副作用的调用串行执行 |
| 不同模型能力差异很大 | 维护一张**模型能力矩阵**，配合自有评测集，按角色路由模型（见下） |

---

## 第二部分：定位与关键决策

### 定位建议

**不做"又一个通用 Open Science"，而是做"面向药企/生物技术、可私有化部署、模型中立、可审计"的科研 Harness。** 差异化放在三点：

1. **异构审查**：Reviewer 默认用和主 agent **不同家族**的模型（例如 Claude 干活、GPT/Gemini 审查，或反过来），并且可以选择重跑关键计算。Claude Science 在结构上做不到这一点。
2. **私有化与合规**：数据不出内网；可以全部用内网模型（vLLM 部署 Qwen/DeepSeek/Llama）；有审计日志、SSO/RBAC；溯源记录满足内部 QA 要求；默认不上传任何 trace。
3. **评测驱动的模型路由**：用自建评测集（BixBench / LAB-Bench 子集 + 公司内部真实任务）持续给各模型打分，按角色和成本自动选模型。

### 关键技术选型（推荐方案）

| 决策 | 推荐 | 理由 / 备选 |
|---|---|---|
| 核心语言 | **Python 后端** | kernel 管理、数据血缘 hook（pandas/anndata）、科学工具和团队技能都在 Python。备选：fork opencode（TS），出 MVP 更快，但科学层仍要另写 Python 侧进程 |
| Agent 内核 | **Pydantic AI v2**（自己写编排层） | 跨 provider 的 thinking 回放、FallbackModel、MCP 和持久化执行都现成；保持内核薄、可替换。备选：OpenHands SDK |
| 模型层 | Pydantic AI 原生 adapter + **自托管 LiteLLM Proxy 作为统一网关**（锁版本） | 网关负责预算、key、审计和内网模型接入 |
| 执行 | **Jupyter kernel**（jupyter_client）+ 每个项目一个 pixi 环境 | 标准，可导出 ipynb；R/Julia 直接使用现有 kernel |
| 沙箱 | 本地用 bubblewrap/Landlock（Linux）；服务端用 Docker/Podman；HPC 用 Apptainer | 网络默认拒绝，经代理白名单放行 |
| Skills | **Agent Skills（SKILL.md）标准** | 可以直接复用 K-Dense、BioNeMo、Anthropic 的 skill 生态 |
| 工具 | **MCP**，MCP server 放在独立进程和独立环境里运行 | 先接 ToolUniverse + BioMCP + PaperQA2 |
| 存储 | 单机用 SQLite，团队版用 Postgres；产物用内容寻址存储（CAS，hash） | 溯源图：artifact ← run ← code + env lock + inputs + message |
| 前端 | Web UI（React），后期再用 **Tauri** 打包桌面版 | 先做 server 模式，方便部署到内网服务器或 HPC 登录节点 |
| 接口 | HTTP + SSE 的 OpenAPI，外加 **ACP** 和 MCP server 模式 | 可以被 Claude Code / Codex 调用，反过来也能调用它们 |

---

## 第三部分：架构

```
┌──────────────────────── Web UI / Tauri 桌面 / CLI / SDK ────────────────────────┐
│  会话 · Plan 审批 · 权限卡片 · Artifact 查看器（结构/基因组轨道/分子/图表）· Review 卡片 │
└───────────────────────────────▲──────────────────────────────────────────────────┘
                                │ HTTP + SSE (OpenAPI) / ACP
┌───────────────────────────────┴───────── Harness Daemon（Python）────────────────┐
│ Orchestrator：Coordinator · Planner · Delegation（并行子任务）· Specialists        │
│ Reviewer（异构模型，只读；可选重跑）· Compaction / Context Budget                  │
│ ───────────────────────────────────────────────────────────────────────────────  │
│ Model Layer：规范消息格式 · Provider Adapters · 能力矩阵 · 路由/回退 · 缓存策略      │
│              ↓ 自托管 LLM Gateway（LiteLLM Proxy）→ Claude/GPT/Gemini/DeepSeek/vLLM │
│ ───────────────────────────────────────────────────────────────────────────────  │
│ Tool Layer：Kernel Manager(Py/R) · Shell · File · MCP Host · Skills Loader · Web    │
│ Provenance：Artifact CAS · Run 记录 · Env Lock · 数据血缘 hook · 导出包              │
│ Policy：权限/授权范围 · 网络代理白名单 · 密钥库 · 审计日志                          │
│ Compute Adapters：Local · SSH · Slurm/PBS/LSF · K8s · Modal · NIM                  │
└──────────────────────────────────────────────────────────────────────────────────┘
       │ MCP（独立进程/环境）                    │ 沙箱内执行
  ToolUniverse · BioMCP · PaperQA2 · 自研内部数据库 MCP     bubblewrap / Docker / Apptainer
```

推荐的模型角色分工：
- **Coordinator/Planner**：最强的模型（Claude Opus / GPT 旗舰 / Gemini Pro）
- **Worker**（写代码、查数据库）：性价比高的模型（Sonnet / Flash / DeepSeek / Qwen）
- **Reviewer**：和 Coordinator 不同家族的强模型
- **敏感数据任务**：强制路由到内网模型

---

## 第四部分：路线图

### Phase 0 · 调研与地基（2 周）
- [ ] 实际试用三个 Open Science 和 Claude Science，用同一组 10 个内部真实任务对比，记录卡点
- [ ] **先搭评测框架**：基于 Inspect AI；选 BixBench、LAB-Bench（DbQA/SeqQA/LitQA2）和 Biomni-Eval1 的子集，加 10–20 个内部任务
- [ ] 确认关键选型（Python vs fork opencode），完成技术 spike：Pydantic AI + 2 家 provider + Jupyter kernel 跑通一个闭环
- [ ] License 审计清单：skills、数据库（部分非商用）、Biomni 工具

**交付**：选型决策记录 + 评测基线（Claude Science 和各开源项目的分数）

### Phase 1 · MVP：能用的单 agent 工作台（6–8 周）
- [ ] Model Layer：规范消息格式；Anthropic / OpenAI(Responses) / Gemini / OpenAI 兼容（DeepSeek、Qwen、vLLM）四类 adapter；thinking 原始 parts 存储；tool call 修复；缓存标记
- [ ] Agent loop：工具调用、流式输出、中断/继续、compaction
- [ ] 工具：Python/R kernel（常驻，大对象只回摘要）、shell、文件读写、web fetch
- [ ] Skills loader（SKILL.md 渐进加载）+ MCP host
- [ ] 权限系统：文件目录授权，执行和网络审批，授权范围分 Once/会话/项目/全局
- [ ] Web UI：会话、代码步骤展开、图表展示
- [ ] 每次请求记录模型、token 和成本

**验收**：在 3 家以上模型上跑通"下载 GEO 数据 → scanpy QC → 出图"；评测分数达到 Phase 0 基线的 80%

### Phase 2 · 科学层：溯源 + Reviewer + Connector（6–8 周）
- [ ] Artifact 存储：内容寻址 + 版本链（parent）+ code + env lock hash + inputs + 消息区间
- [ ] 数据血缘：给 pandas/anndata/numpy/matplotlib 的 I/O 加 hook
- [ ] 环境：每个项目一个 pixi 环境，锁文件随产物保存；"一键重跑某个 artifact"
- [ ] 导出包（类似 `.science`）：对话 + 代码 + 环境 + 产物，可以离线复现
- [ ] Plan 模式（先出计划，用户批准后执行，逐步打勾）
- [ ] **Reviewer v1**：异构模型，只读；检查"声称算过但实际没跑"、数值与文件不一致、引用/DOI 不支持结论、Plan 未完成
- [ ] Connector：ToolUniverse、BioMCP、PaperQA2（文献子 agent）、Open Targets、ChEMBL、UniProt、PubMed
- [ ] Skills 导入：K-Dense 和 BioNeMo（过 license 白名单）
- [ ] Artifact 查看器：3D 结构（Mol*）、分子（RDKit/Ketcher）、基因组轨道（igv.js）

**验收**：CORE-Bench 子集可复现率达标；Reviewer 在人工注入错误的测试集上召回率 ≥ 70%

### Phase 3 · 算力与沙箱（4–6 周）
- [ ] OS 沙箱（bubblewrap/Landlock），网络代理白名单；MCP server 进程隔离
- [ ] SSH 主机探测（CPU/GPU/conda/modules/调度器）+ 每台主机一份 Details 文档
- [ ] 调度器适配：Slurm、**PBS/LSF**、K8s Job（Claude Science 不支持的部分）
- [ ] Modal / 云 GPU，**带花费上限**
- [ ] GPU 模型 skill：ESMFold/Boltz/AlphaFold（本地 MSA 选项，避免把序列发到外部服务器）、BioNeMo NIM
- [ ] 长作业：断线不丢、结果拉回、大文件只记录路径

### Phase 4 · 多 agent 与智能路由（4–6 周）
- [ ] Delegation：并行子任务、子 agent 状态面板、每个子 agent 独立配置模型和权限
- [ ] 用户自定义 Specialist（markdown 定义），actor-critic 写作/审查对
- [ ] 可选：Co-Scientist 式假设生成循环（生成 → 反思 → Elo 排名 → 进化）
- [ ] **模型路由**：基于评测结果和成本，按角色、任务类型和数据敏感级别自动选模型；失败时回退
- [ ] 每次发版自动在所有接入模型上跑评测，生成模型能力矩阵

### Phase 5 · 团队与企业化（持续）
- [ ] 多用户服务端模式：SSO/RBAC、项目共享、Postgres
- [ ] 审计日志、合规导出、数据分级（敏感项目强制用内网模型、禁止外网）
- [ ] 内部数据库/LIMS/ELN 的 MCP connector
- [ ] Skill 市场（团队内部沉淀"把一次会话提炼成 skill"）
- [ ] Tauri 桌面版、移动端查看

### 大致里程碑
| 时间 | 里程碑 |
|---|---|
| 第 2 周 | 选型定稿 + 评测基线 |
| 第 2 个月末 | MVP 内部试用（多模型） |
| 第 4 个月末 | 溯源 + Reviewer + 主要数据库，科研人员小范围试用 |
| 第 5–6 个月 | 接入 HPC/云算力；多 agent；路由 |
| 6 个月以后 | 企业化，开始推广 |

（按 2–3 名工程师估算，其中至少 1 人有生信或计算化学背景）

---

## 第五部分：风险与对策

| 风险 | 对策 |
|---|---|
| 开源竞品迭代很快，通用功能会被追平 | 内核保持薄；集中投入差异化（异构审查、私有化合规、评测路由、内部数据接入） |
| 开源模型 tool calling 不稳定 | code-action 降级 + JSON 修复；路由层把复杂编排交给强模型 |
| 供应链安全（LiteLLM 投毒、MCP stdio 命令执行） | 锁版本并校验 hash；MCP 进程隔离；依赖扫描 |
| License（非商用数据库、Biomni、Sakana、各 skill） | Phase 0 建立 license 白名单；商用模式下自动禁用不合规资源 |
| 数据泄露（外部 MSA 服务器、商用 API） | 数据分级 + 强制路由；默认关闭所有遥测和 trace 上传 |
| LLM 幻觉和错误结论 | Reviewer + 溯源 + 可重跑；UI 明确标注"未验证" |
| 维护多家 provider adapter 的成本 | 统一走网关 + 能力矩阵自动化测试；能力测试纳入 CI |

---

## 参考来源

- Claude Science：[发布文](https://www.anthropic.com/news/claude-science-ai-workbench) · [文档](https://claude.com/docs/claude-science/overview) · [第三方拆解](https://feitong.phd/essays/dissecting-claude-science/) · [MIT Tech Review](https://www.technologyreview.com/2026/06/30/1139987/claude-science-is-anthropics-newest-flagship-product/) · [Forbes](https://www.forbes.com/sites/johndrake/2026/06/30/anthropics-new-ai-workbench-mapped-my-field-for-26-now-imagine-it-aimed-at-the-rest-of-science/)
- 开源同类：[synthetic-sciences/openscience](https://github.com/synthetic-sciences/openscience) · [openscience.sh](https://www.openscience.sh/) · [aipoch/open-science](https://github.com/aipoch/open-science) · [ai4s-research/open-science](https://github.com/ai4s-research/open-science)
- Harness 底座：[opencode](https://github.com/anomalyco/opencode) · [pi](https://github.com/earendil-works/pi) · [Pydantic AI](https://pydantic.dev/docs/ai/overview/) · [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) · [Deep Agents](https://github.com/langchain-ai/deepagents) · [Goose](https://aaif.io/projects/goose) · [Codex](https://github.com/openai/codex) · [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)
- 模型层：[LiteLLM 安全事件](https://docs.litellm.ai/blog/security-incident-oct-21-2026) · [any-llm](https://github.com/mozilla-ai/any-llm) · [AI SDK 6](https://vercel.com/blog/ai-sdk-6) · [OpenRouter reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) · [Pydantic AI thinking](https://ai.pydantic.dev/thinking/)
- 科学层：[Biomni](https://github.com/snap-stanford/Biomni) · [Robin](https://github.com/Future-House/robin) · [PaperQA](https://github.com/Future-House/paper-qa) · [ToolUniverse](https://github.com/mims-harvard/ToolUniverse) · [K-Dense skills](https://github.com/K-Dense-AI/claude-scientific-skills) · [BioNeMo Agent Toolkit](https://github.com/NVIDIA-BioNeMo/bionemo-agent-toolkit) · [Agent Skills](https://agentskills.io/home) · [BioMCP](https://biomcp.org) · [BioContextAI](https://biocontext.ai/registry) · [Co-Scientist](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/) · [AI Scientist v2](https://github.com/SakanaAI/AI-Scientist-v2)
- 执行/环境：[Kernel Gateway](https://github.com/jupyter-server/kernel_gateway) · [E2B](https://github.com/e2b-dev/E2B) · [Modal Sandboxes](https://modal.com/docs/guide/sandboxes) · [pixi lockfile](https://prefix-dev.github.io/pixi/v0.64.0/workspace/lockfile/) · [slurm_mcp](https://github.com/mila-iqia/slurm_mcp)
- 评测：[BixBench](https://arxiv.org/abs/2503.00096) · [LAB-Bench](https://github.com/Future-House/LAB-Bench) · [Biomni-Eval1](https://huggingface.co/datasets/biomni/Eval1) · [ScienceAgentBench@HAL](https://hal.cs.princeton.edu/scienceagentbench) · [AstaBench](https://github.com/allenai/asta-bench) · [BioML-bench](https://www.biorxiv.org/content/10.1101/2025.09.01.673319v3.full)

> 说明：各开源项目的 star 数和 benchmark 分数都是项目自报的；Claude Science 的内部实现细节只来自一篇第三方拆解；Daytona 闭源、Agent Skills 捐赠给 AAIF 等消息未经交叉验证。做决策前请到原始页面复核。
