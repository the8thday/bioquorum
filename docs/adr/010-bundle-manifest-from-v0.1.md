# ADR-010 研究包 manifest 从 v0.1 起存在

状态：已接受（2026-11-13）

## 背景
研究包导出排在后续版本，但溯源信息必须从第一次 Run 起就被记录，否则早期会话无法导出。

## 决定
`bundle-spec/` 自 v0.1 起有 manifest v0 的 JSON Schema，最小字段见 ARCHITECTURE §6.3。字段只增不删。每条 Run 写入时就填满 manifest 所需的信息。

## 后果
- 导出功能可以晚做，但数据从第一天就齐。
- Schema 变更要带版本号和迁移说明。
