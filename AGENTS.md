# AGENTS.md — 给 AI 协作者的项目约定

你在为 BioQuorum 写代码（Claude Code、ChatGPT / Codex 或其他 AI 协作者都适用）。作者是单人开发者，Python 不熟，所有代码由 AI 生成，所以**架构纪律由你负责守住**。

## 先读什么
1. `NEXT.md`：现在做到哪、下一步是什么。
2. `docs/ARCHITECTURE.md`：最高约束。标【承重】的条目不能为了省事破例。
3. `docs/adr/`：已做出的决定。不要在会话里重新争论；要改就先新增一条 ADR。
4. `ROADMAP.md`：当前版本的范围。不要提前做后面版本的东西。

## 不可违反的规则（来自 ARCHITECTURE.md §1）
- 分层依赖方向由 `pyproject.toml` 里的 import-linter 契约强制，`uv run lint-imports` 必须通过。
- 事件日志是唯一写入路径。任何投影表只能在 `storage/projections/` 里被写。需要新状态就加新事件类型，并同步更新 ARCHITECTURE.md §5.3 的表格（有测试检查两者一致）。
- 规范消息格式在 `core/messages.py`。Pydantic AI 的类型只允许出现在 `models/`。
- 一切有副作用的操作走 `tools.invoke()`。agent 层不直接碰 runtime / connectors / mcp。
- 工具返回内容默认 `untrusted`。
- 密钥不进事件、日志、异常信息。
- 哈希只用 `core/hashing.py`，ID 只用 `core/ids.new_id()`。

## 工作方式
- 先写测试再写实现；新模块合入前必须有对应层的测试（ARCHITECTURE.md §6.8）。
- CI 默认不联网、不调模型、不启真实 kernel。需要这些的测试打 `integration` / `live` / `e2e` 标记。
- 每次改完跑：`uv run pytest && uv run lint-imports && uv run ruff check . && uv run pyright`。
- 改了承重决定：新增 `docs/adr/NNN-*.md`，更新 ARCHITECTURE.md §11 索引。
- 会话结束前更新 `NEXT.md`。
- 用中文和作者交流；代码、注释、文档字符串用英文。

## 目录速查
`src/bioquorum/core` 领域模型 · `storage` 事件表与投影 · `policy` 权限分级审计 · `runtime` kernel · `provenance` 溯源与研究包 · `connectors` 数据库 · `skills` SKILL.md · `mcp` 客户端与服务端 · `tools` 工具注册表与 invoke · `models` provider 与路由 · `review` 审查 · `agent` 编排 · `cli` 命令行 · `evals/` 评测与对照实验

## 环境与权限（这台机器上的实际情况）
- 依赖用 uv 管理。**不要自己安装依赖或下载二进制**：请作者在终端里运行（Claude Code 里是 `! uv add ...`）。改 `pyproject.toml` 后 `uv run` 会自动同步环境，这一点要告诉作者。
- API key 只在作者自己的终端里 `export`（`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`），不写进仓库、配置文件或 `.env`。配置里只写 `env:NAME` 引用。检查时只报告 set / missing，不打印值。
- 提交前确认 diff 里没有用户名路径、公司邮箱和密钥；commit 身份是 `the8thday <the8thday@users.noreply.github.com>`。只有作者明确要求时才 commit / push。
- 公开仓库：https://github.com/the8thday/bioquorum。

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

## 评测纪律
- 改审查规则时，**开发集（手写语料）上的满分不说明问题**：必须在模型写的、没参与调参的答案上复测（`evals/review_eval.py --heldout`），报告里分开写"看过的"和"没看过的"数据。
- 不为在语料上拿分写针对单题的规则；抓不到的如实列出，交给模型检查。
- 语料的标准答案只在逻辑上站得住时才改（例如 `also_affects` 连带结论），并写进语料 README。
- 对外结论（README、PRODUCT）只写有数据支撑的话。ADR-011 就是因为对照实验不支持而被 ADR-013 取代。
