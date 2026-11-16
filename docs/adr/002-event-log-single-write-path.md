# ADR-002 事件日志是唯一写入路径

状态：已接受（2026-11-13）

## 背景
UI 推送、审计、溯源、重放、模型上下文都需要会话历史。若 Run、Artifact 等表与事件各自写入，会形成两份事实，重放和审计都不可信。

## 决定
会话内一切变化先写成只追加的事件（ARCHITECTURE §5.2）。Run、Artifact、Claim、ModelCall、AuditEntry 等表都是事件的投影，`bq rebuild` 可从事件重建，重建结果必须与原表一致。流式 token 增量不是事件。

## 后果
- 每个事件类型都是带 `schema_version` 的 Pydantic 模型，升级需提供迁移函数。
- 写投影的代码只能出现在 `storage/projections/`。
- 任何 bug 修复都以"新事件"而非"改旧事件"的方式进行。
