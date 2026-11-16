# ADR-003 自有规范消息格式

状态：已接受（2026-11-13）

## 背景
Pydantic AI 提供了跨 provider 的消息类型，直接把它存进事件日志最省事。但这样事件日志与框架版本绑定，换内核或框架大版本升级时历史读不回来。各家 thinking、tool call ID 格式也互不兼容。

## 决定
`core/messages.py` 定义 BioQuorum 自己的 Message / Part 类型（ARCHITECTURE §5.4）。事件日志只存这套类型；provider 原始响应作为不透明 `provider_raw` 附件保存，逻辑不得依赖它。`models/convert.py` 是与 Pydantic AI 类型之间唯一的转换点。

## 后果
- 多写一层转换，换 agent 内核时只改 `models/`。
- `tool call_id` 由 BioQuorum 分配 ULID，provider ID 映射不进事件。
- 跨 provider 切换时 ThinkingPart 降级为文本或丢弃。
