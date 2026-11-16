# CLAUDE.md — 给 AI 协作者的项目约定

你在为 BioQuorum 写代码。作者是单人开发者，所有代码由你生成，所以**架构纪律由你负责守住**。

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
