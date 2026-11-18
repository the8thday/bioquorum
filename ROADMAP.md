# BioQuorum 路线图

> 2026-11-17 重排（v0.0、v0.1 完成；v0.2 起按 [docs/DESIGN.md](docs/DESIGN.md) 调整）· 架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（最高约束）· 产品定义见 [docs/PRODUCT.md](docs/PRODUCT.md) · 上一版存档于 docs/archive/
>
> 分工：Claude Code 负责设计、写代码、写测试、跑测试和写文档，以较高自主权限运行；作者负责定方向、每天跑一次演示场景、判断科学结论是否正确、验收。
> 版本按顺序做，v0.2 起不再写天数。**每个版本结束时都必须能跑起来、能演示。** 路线图服从架构：顺序可以调，架构的承重决定不能为赶进度破例。

## v0.0 · 地基 + 假设验证（第 1 到 2 天）✅ 2026-11-17
- [x] 仓库骨架：uv、ruff、pyright、pytest、import-linter 契约、pre-commit、CI、Apache-2.0、CLAUDE.md、docs/adr/
- [x] `core/`：ID、事件信封、事件类型、规范消息格式、实体、哈希规则、状态机，全部带测试
- [x] `storage/`：事件表 + 投影 + `bq rebuild` 一致性测试
- [x] **对照实验**：20 份注入错误的分析记录，同家族 vs 跨家族 reviewer 的召回率和误报率；结果决定 ADR-011 是否成立 → 未发现同家族盲区，ADR-011 被 ADR-013 取代（docs/research/reviewer-experiment.md）
- [x] 三个北极星场景各 5 道评测题

## v0.1 · Demo：kernel + 审查 + MCP（第 3 到 7 天）★ 一周 Demo ✅ 代码完成 2026-11-17（两项验收待作者）
- [x] `runtime/`：`Kernel` 接口、`FakeKernel`、`JupyterKernel`、输出摘要
- [x] `tools/`：注册表、`invoke()` 咽喉、内置工具（kernel、文件、连接器）——数据库连接器全部经外部 MCP 接入，`connectors/` 暂空
- [x] `provenance/`：CAS、Run 记录、manifest v0
- [x] `mcp/`：客户端接 2 个现成数据库 MCP（Open Targets 官方、BioMCP 或 ToolUniverse）；服务端暴露科学层——另有录制回放
- [x] `models/`：2 家 provider（1 个 Anthropic 或 OpenAI，1 个 OpenAI 兼容本地或 DeepSeek/Qwen），worker 和 reviewer 两个角色，cassette 录制回放——OpenAI（Responses）+ DeepSeek
- [x] `review/`：Claim 抽取 + 4 个确定性检查 + `citation_supports`——实测见 docs/research/review-checks.md
- [x] `agent/` 最小循环；CLI：`bq run`、`bq review`（另有 `bq sessions`、`bq models`、`bq project`、`bq mcp serve|record`）
- **验收**：`bq run` 跑通简化版场景 C；`bq review` 抓出一条假引用；在 Claude Code 里通过 MCP 用同一套工具跑通；录一段终端演示
  - [x] `bq run` 跑通简化版场景 C（2026-11-17，GPT-6.1-sol + BioMCP，9 步 13 次工具调用，DeepSeek 审查 25 条结论）
  - [x] `bq review` 抓出一条假引用（`citation_resolves`，`--answer-file`）
  - [ ] 在 Claude Code 里通过 MCP 跑通——`bq mcp serve` 已用官方 MCP 客户端经 stdio 验证；需作者在 Claude Code 里确认（docs/demo-v0.1.md 第 4 步）
  - [x] ~~录终端演示~~——取消：正式发布改用 v0.4 的工作台演示（DESIGN.md 第 4 节）

## v0.2 · Grounded：有据（CLI）
目标：`bq run` 的回答里，每个数字和引用要么绑定到证据，要么明确标为"仅为断言"；kernel 不能自己联网。
- [x] **kernel 隔离**（ADR-017，2026-11-18）：bubblewrap，网络默认关闭，文件范围限定；隔离不可用时的分级行为；`SECURITY.md`
- [x] 数据下载工具（结果进 CAS，带来源）：kernel 不能联网之后，取外部数据的唯一途径（2026-11-18，`download_file`）
- [x] **证据绑定**（ADR-014，2026-11-18）：kernel 里的 `bq.value()`；`{{name}}` 与 `[@来源 "原话"]` 的解析；`grounded` 状态（ADR-015 的第一级）；解析失败退回；全文经工具获取——代码与离线测试完成，真实模型的遵从率在评测项里测
- [ ] **循环按预算运行**（ADR-018）：token / 费用 / 时间限额取代 12 步上限；压缩；CLI 流式输出；费用显示（现在 `cost_usd` 为 0）
- [ ] **评测**：harness 提升评测的第一版（裸跑对比带 harness）；第 3 家 provider；三家模型的绑定遵从率、编造数字检出率（对比 v0.1 `value_match`）、绑定前后的 rubric 得分，留出集单独报告，写进 `docs/research/`
- [ ] 场景 C 完整；场景 B 用 pbmc3k 规模的小数据集
- [ ] 小债：外部 MCP 断线重连；改表前接 Alembic；`citation_resolves` 在线解析
- **验收**：场景 B、C 各一个回答，所有显示的数字和引用都是"有据"或"仅为断言"，没有第三种；集成测试证明隔离的 kernel 联网失败；作者读一份回答，判断科学结论

## v0.3 · Workbench：工作台 ★ 可安装
目标：不碰命令行完成一次研究，并且看得见证据。
- [ ] `server/`：FastAPI + SSE，只读事件与投影、只转发与 CLI 相同的调用（ADR-016）；import-linter 契约
- [ ] `web/`：研究记录、证据抽屉（数字 → 代码与输出，引用 → 原文与原话）、活动流与输入框、计划 / 权限 / 审查卡片、会话列表；构建产物随包发布
- [ ] 计划模式：计划即预先登记的分析方案，偏离要写明（`plan_proposed` / `plan_approved` 事件已存在）
- [ ] 打断与改方向；会话分叉
- [ ] `uvx bioquorum` / `pip install bioquorum`；快速开始；`CONTRIBUTING.md`、`THIRD_PARTY.md`
- **验收**：PRODUCT.md §10"可安装"清单；作者在浏览器里完成场景 C，并按 DESIGN.md 第 4 节的前五步走一遍，决定布局是否要改

## v0.4 · Quorum：复现与独立重复 ★ 正式发布
目标：60 秒演示的七步全部是真的。
- [ ] 重跑 + 结果比对 → `reproduced`；血缘 hook（pandas / anndata / matplotlib），失效不影响分析
- [ ] 独立重复 → `replicated`（ADR-015）：盲重做、按容差比较、差异并排显示、触发前显示预估费用
- [ ] 研究包导出 + `rerun.sh`，带绑定关系；bundle-spec v1.0；pixi 环境锁定
- [ ] `method_sanity`；补测判断类错误上同家族对跨家族的差异，每类至少 15 例（ADR-013）
- [ ] 接入公开基准子集（BixBench、LAB-Bench）；发布评测报告
- [ ] README 首屏演示录屏；三个场景的示例项目；文档站；发布流程自动化
- **验收**：PRODUCT.md §10"正式发布"清单；研究包在干净机器上重跑成功；在 HN、X 和相关社区发布

## v0.5 · Depth：把 agent 做深
每一项合入时附带 harness 提升评测的前后对比（ADR-018），没有提升的不留。
- [ ] 子任务（全新上下文的子 agent，先做文献检索）
- [ ] Skills（SKILL.md 渐进加载）；项目笔记（跨会话）
- [ ] 场景 A 完整；真实 GEO 数据的场景 B；自动 Methods 草稿
- [ ] 工具结果进 kernel；图片交给有视觉能力的模型
- [ ] 稳健性检查：换一组合理的分析选择重跑

## v0.6 · Private：私有部署
- [ ] 数据分级与审计日志的界面
- [ ] 出站白名单代理；外部 MCP 进程隔离；macOS 隔离
- [ ] Docker 镜像；服务器部署文档；本地模型部署指南；`SECURITY.md` 完整版

## 之后
- Specialists 并行、更多连接器和 skills、R kernel
- 结构、分子、基因组查看器
- 基于评测的自动模型路由；插件 API 稳定化；Python SDK
- v1.0：bundle-spec、插件 API、配置格式承诺向后兼容
- v1.0 之后：SSH / Slurm / Modal、团队版（Postgres、SSO）、Tauri 桌面

## 风险与提前量
- ~~对照实验若显示跨家族审查无明显优势~~：已发生（2026-11-17）。ADR-011 被 ADR-013 取代，宣传不再说"跨家族更准"；验证的卖点落在证据绑定、结论级确定性检查、可重跑和独立重复（ADR-014、015）。
- pixi 与 bioconda 环境解析耗时：v0.1 的 kernel 先用项目已有的 Python 环境，pixi 锁定从 v0.2 起强制。
- 外部 MCP 服务器不稳定：每个连接器保留固定响应样本，演示可离线。
- 较弱的模型不按绑定语法写：v0.2 先测遵从率；不够就加"补绑定"步骤或对该模型降级，不为它放宽"有据"的定义。
- 前端由 AI 独立维护：组件和依赖从严控制；端到端测试跟着第一版一起写。
- 独立重复的假不一致：先在评测里量出比例，再决定界面上怎么表述。
