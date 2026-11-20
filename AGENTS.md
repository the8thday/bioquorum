# AGENTS.md — 给 AI 协作者的项目约定

你在为 BioQuorum 写代码（Claude Code、ChatGPT / Codex 或其他 AI 协作者都适用）。作者是单人开发者，所有代码由 AI 生成，所以**架构纪律由你负责守住**。

**AI 是这个项目的 owner**（作者 2026-11-17 的决定）：设计和技术上的取舍由你来定，不要把技术选项列给作者选；定了就写下来（理由进 `docs/DESIGN.md`，决定进 `docs/adr/`），再用白话告诉作者定了什么、为什么。留给作者的只有：判断科学结论对不对、界面的观感验收、安装依赖、commit / push、花真钱的操作。owner 不等于可以破例：改承重决定照样先写 ADR。

## 先读什么
1. `docs/DESIGN.md`：为什么这样做。十条判断决定了取舍，拿不准时回到这里。
2. `NEXT.md`：现在做到哪、下一步是什么。
3. `docs/ARCHITECTURE.md`：最高技术约束。标【承重】的条目不能为了省事破例。
4. `docs/adr/`：已做出的决定。不要在会话里重新争论；要改就先新增一条 ADR。
5. `ROADMAP.md`：v0.7 到 v1.0 的逐步任务（编号如 `0.7.3`）和每一步的完成标准。按编号顺序做，不要提前做后面版本的东西。

## 不可违反的规则（来自 ARCHITECTURE.md §1）
- 分层依赖方向由 `pyproject.toml` 里的 import-linter 契约强制，`uv run lint-imports` 必须通过。
- 事件日志是唯一写入路径。任何投影表只能在 `storage/projections/` 里被写。需要新状态就加新事件类型，并同步更新 ARCHITECTURE.md §5.3 的表格（有测试检查两者一致）。
- 规范消息格式在 `core/messages.py`。Pydantic AI 的类型只允许出现在 `models/`。
- 一切有副作用的操作走 `tools.invoke()`。agent 层不直接碰 runtime / connectors / mcp。
- 工具返回内容默认 `untrusted`。
- 密钥不进事件、日志、异常信息。
- 哈希只用 `core/hashing.py`，ID 只用 `core/ids.new_id()`。
- 读者看到的数字来自证据库，不来自模型输出的字符；不为了让回答"看起来已验证"放宽任何一级状态的定义（ADR-014、015）。
- 界面不持有不能从事件推出的状态；`server/` 不写投影、不绕过 `invoke()`（ADR-016）。
- agent 层的新机制必须有独立开关，合入时附带 harness 提升评测的前后对比（ADR-018）。

## 工作方式
- 先写测试再写实现；新模块合入前必须有对应层的测试（ARCHITECTURE.md §6.8）。
- CI 默认不联网、不调模型、不启真实 kernel。需要这些的测试打 `integration` / `live` / `e2e` 标记。
- 每次改完跑：`uv run pytest && uv run lint-imports && uv run ruff check . && uv run pyright`。
- 改了承重决定：新增 `docs/adr/NNN-*.md`，更新 ARCHITECTURE.md §11 索引。
- 会话结束前更新 `NEXT.md`。
- 用中文和作者交流；代码、注释、文档字符串用英文。

## 目录速查
`src/bioquorum/core` 领域模型 · `storage` 事件表与投影 · `policy` 权限分级审计 · `runtime` kernel · `provenance` 溯源与研究包 · `connectors` 数据库 · `skills` SKILL.md · `mcp` 客户端与服务端 · `tools` 工具注册表与 invoke · `models` provider 与路由 · `review` 审查 · `agent` 编排 · `application` CLI 与工作台共用的组装和控制 · `server` 工作台服务 · `cli` 命令行 · `web/`（仓库根，v0.7 起）前端源码 · `evals/` 评测与对照实验

## 环境与权限（这台机器上的实际情况）
- 依赖用 uv 管理。**不要自己安装依赖或下载二进制**：请作者在终端里运行（Claude Code 里是 `! uv add ...`）。改 `pyproject.toml` 后 `uv run` 会自动同步环境，这一点要告诉作者。
- API key 只在作者自己的终端里 `export`（`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`），不写进仓库、配置文件或 `.env`。配置里只写 `env:NAME` 引用。检查时只报告 set / missing，不打印值。
- 需要调真实模型做测试或评测时，**优先用 OpenAI**（worker 和 reviewer 都用，例如 `openai:gpt-6.1-sol`）。只有测试本身针对 DeepSeek（例如思考内容回放）或者要做跨家族对照时才用 DeepSeek。
- 提交前确认 diff 里没有用户名路径、公司邮箱和密钥；commit 身份是 `the8thday <the8thday@users.noreply.github.com>`。只有作者明确要求时才 commit / push。
- 公开仓库：https://github.com/the8thday/bioquorum。
- kernel 在 bubblewrap 沙箱里运行（ADR-017，本机可用）：不能联网、不能装包、只能写项目目录。需要外部数据就加工具，不要放宽沙箱。

## 已经踩过的坑（不要再踩）
- **DeepSeek 思考模式**要求下一轮把上一轮的 reasoning 原样传回，否则 400。`models/convert.py` 按家族保存并回放 thinking。
- **OpenAI 新模型（gpt-6.x）带工具必须走 Responses API**，Chat Completions 会 400；`api = "responses"` 是 openai 的默认值。
- 我们的 ULID 可以直接当 provider 的 tool_call_id 发回去，但**不要回传服务端条目 ID**（OpenAI 的 `fc_...`）。
- **BioMCP 0.9 在 MCP SDK 默认的 `auto` 协商下空闲后会崩**，stdio 服务器默认 `protocol = "legacy"`。BioMCP 在本机运行但查询发往公共 API，分级必须标 `public`。
- MCP SDK 会隐藏服务端异常的文本，测试里不要断言错误内容。
- Jupyter 的 IPC socket 默认是相对路径；kernel 和客户端工作目录不同时会连不上（已改为私有目录里的绝对路径）。
- 每个工具调用都必须以结果事件结束（拒绝也算），否则从事件重建的历史会被 provider 拒绝。
- 完整版 `pydantic-ai` 会带进 logfire / OpenTelemetry：只用 `pydantic-ai-slim[按需 provider]`。
- 录制的 cassette 里只记命令名，不记绝对路径（会泄露用户名）。
- **沙箱里的 kernel 不能用信号打断**：SIGINT 会打到 bwrap 进程，kernel 跟着结束。`runtime/jupyter.py` 改成经控制通道中断（`interrupt_mode = "message"`）。
- **venv 的解释器是一串符号链接**（uv 的 `cpython-3.12-…` 目录本身也是链接）。沙箱里只挂 venv 会报 `execvp … No such file`；`runtime/sandbox.interpreter_mounts()` 沿链路挂目录并重建中间链接。
- **Python kernel 早已可用**（ipykernel，Jupyter 协议）；路线图里单提 R 是因为只缺 R（v0.9 接 IRkernel）。两种语言并列，不要把 R 当成替换。
- 开发环境里没有 matplotlib：需要画图的测试别放进默认依赖能跑的集合里。
- HTTP 库是 **`httpx2`**（`import httpx2`），不是 `httpx`；API 与 httpx 相同。`MockTransport` 配异步客户端时，流式响应体要用异步生成器，同步迭代器会触发 `AssertionError`。

## 评测纪律
- 改审查规则时，**开发集（手写语料）上的满分不说明问题**：必须在模型写的、没参与调参的答案上复测（`evals/review_eval.py --heldout`），报告里分开写"看过的"和"没看过的"数据。
- 不为在语料上拿分写针对单题的规则；抓不到的如实列出，交给模型检查。
- 语料的标准答案只在逻辑上站得住时才改（例如 `also_affects` 连带结论），并写进语料 README。
- README 只写已经实现并测过的东西；PRODUCT.md 写目标产品，两者不要混。对外结论只写有数据支撑的话。ADR-011 就是因为对照实验不支持而被 ADR-013 取代。
