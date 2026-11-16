# ADR-006 tools.invoke() 是唯一执行咽喉

状态：已接受（2026-11-13）

## 背景
权限、分级、审计若分散在 agent 层各处，容易漏，也无法保证 MCP 出口与 agent 出口行为一致。

## 决定
任何有副作用的操作都经 `tools.invoke()` 执行，顺序固定：分级检查 → 权限检查 → 事件 → 执行 → 审计 → 包装结果 → 事件。agent 层禁止直接 import runtime、connectors、mcp 客户端；import-linter 强制。

## 后果
- 权限卡片挂起等待用户时，invoke 挂起，agent 循环无需知道细节。
- 审计日志天然完整。
