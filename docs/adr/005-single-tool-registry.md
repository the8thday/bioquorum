# ADR-005 一份工具定义，两个出口

状态：已接受（2026-11-13）

## 背景
科学层既要给自家 agent 用，又要作为 MCP Server 给 Claude Code / opencode 用。手写两份工具列表必然漂移。

## 决定
所有工具在 `tools/registry.py` 以 ToolSpec 注册一次（ARCHITECTURE §6.4）。agent 工具列表和 `mcp/server.py` 的工具列表都从注册表生成。外部 MCP 服务器的工具自动包装为 ToolSpec。

## 后果
- ToolSpec 必须携带权限类别、输出可信度、分级下限，否则 MCP 出口无法做策略检查。
- 新增工具只改一处。
