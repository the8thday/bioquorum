# ADR-001 后端只用 Python

状态：已接受（2026-11-13）

## 背景
单人项目，作者只熟悉少量 Python，所有代码由 Claude Code 生成。kernel 管理、数据血缘 hook、生信工具链都在 Python 生态。fork opencode（TS）能更快出 MVP，但科学层仍要另写 Python 进程，且会引入作者无法维护的第二套工具链。

## 决定
后端、CLI、MCP Server、测试、评测全部用 Python。前端是唯一例外，且推后（见 ADR-012）。

## 后果
- 放弃 opencode 等 TS 底座的现成 UI 和 provider 生态，用 Pydantic AI 补 provider 层。
- 单一工具链（uv、ruff、pyright、pytest），Claude Code 的产出容易被作者验证。
