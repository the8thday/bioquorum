# ADR-052 · 输入区权限模式分级（Ask / Auto / Full）与授权记录收拢

接受（修订 ADR-051 中"不提供全部自动允许模式"的决定）。

## 背景

在 Public 项目（例如 Target Validation）的真实科研对话中，Agent 需要连续检索多个公共数据库（Europe PMC、ClinicalTrials.gov、GWAS Catalog、HPA、OpenFDA）、在线核验 PMID/DOI 并下载公开数据集。仅依赖"逐次弹窗 + Allow for this session"时，研究者在每一类新数据库或每一个新主机上都会被频繁打断，无法践行 **Agent first**（把研究交给 Agent 独立推进，回来验收记录与分歧）。同时，输入区原有的 `Permissions` 按钮仅展示授权记录，占用主工具栏位置却不能直接调节本轮的自主权限级别。

## 决定

1. **三档权限模式（`permission_mode`）**：
   - `ask`（**Ask** · 每次询问）：沿用原有保守行为，除项目默认只读目录、本地 Python/R 内核与已配置 MCP 外，所有联网、公共数据库、写入与计划偏离均弹窗确认。
   - `auto`（**Auto** · 自动放行安全只读与公共检索，**工作台新建会话默认**）：在 Public 项目中自动放行只读公共数据库（`connector`）与受控公网请求（`network`，含文献核验与 `download_file`），在 Internal / Confidential 项目中仍由数据分级硬墙拒绝外部连接；计划偏离（`request_plan_deviation`）与额外写入操作仍弹窗确认。
   - `full`（**Full** · 全权交由 Agent）：在本轮自动放行所有常规工具权限类别（等价于 CLI `--yes`），用于研究者希望无人值守跑完全程的场景。
2. **四条不可绕过的硬底线**：任何模式（包括 `full`）均**不放宽**以下安全边界：
   - 数据分级硬拒绝（`classification.check`）；
   - OS 级 kernel 沙箱隔离（`bwrap` / `seatbelt`）；
   - `download_file` 的私网/本机 IP 拒绝、公网 IP 绑定与出站代理白名单；
   - 项目设置中显式撤销（`permissions.revoked`）的权限（`PermissionPolicy.revoke()` 记录显式撤销集合，即使存在通配符授权也会拒绝匹配项）。
3. **事件模型与状态重建**：
   - `turn_requested` 升至 v6，新增可选字段 `permission_mode: "ask" | "auto" | "full" | None = None`。v5 升级为 `None`（后端按 `ask` 处理以保持既有兼容）。
   - 工作台新建会话默认选中 `auto`，提交时写入 `turn_requested.permission_mode`；同一会话的后续轮次自动沿用最近一轮的 `last_permission_mode`（ADR-016）。
   - 所有自动放行的工具调用照常经过 `tools.invoke()` 写入事件与审计日志。
4. **界面收拢**：
   - 输入区工具栏的权限控件改为直接切换 `Ask / Auto / Full` 的紧凑选择器。
   - 原有的权限记录面板（本会话已允许项、项目默认授权与一键重置会话授权）收拢至会话顶部 `⋯`（Session actions）菜单下的 `Permissions` 弹窗中。

## 后果

- Public 项目的靶点评估与文献调研默认可一气呵成跑完，不再被联网确认卡片打断，兑现 Agent first 的体验承诺。
- 审计与安全边界保持完整：每条调用仍由 `tools.invoke()` 记录，数据分级、沙箱与项目级显式撤销对所有模式强制生效。
