# ADR-007 工具返回内容默认不可信

状态：已接受（2026-11-13）

## 背景
网页、文献摘要、数据库响应、用户文件都可能含 prompt injection。OS 沙箱在后续版本才有，但 injection 防护不能等。

## 决定
ToolSpec 必须声明 `output_trust`；只有纯本地计算结果可为 trusted。ToolResultPart 携带 trust 标记，进入 prompt 时用固定分隔符包裹，系统提示明确分隔符内指令不得执行。外部 MCP 工具一律 untrusted。

## 后果
- 不能完全防住 injection，但把它变成显式、可审计的边界。
- 后续沙箱是 runtime 内部替换，不改此规则。
