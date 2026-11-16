# ADR-012 Web UI 推后，CLI 与 MCP Server 为首批入口

状态：已接受（2026-11-13）

## 背景
React 三栏工作台是最耗时、最不差异化的部分，且是唯一的非 Python 工具链。科学层已作为 MCP Server 暴露，Claude Code / opencode 可以直接充当 UI。

## 决定
v0.1 至审查层和研究包稳定前，不写前端。入口为 `bq` CLI 和 MCP Server 模式。Web UI 的设计保留在 ARCHITECTURE §10 作为方向。

## 后果
- 首次公开发布面向开发者和会用 Claude Code 的研究者。
- 不写代码的湿实验用户要等 Web UI 版本。
