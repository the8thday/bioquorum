# BioQuorum 路线图

> 2026-11-13 · 架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（最高约束）· 产品定义见 [docs/PRODUCT.md](docs/PRODUCT.md) · 上一版存档于 docs/archive/
>
> 分工：Claude Code 负责设计、写代码、写测试、跑测试和写文档，以较高自主权限运行；作者负责定方向、每天跑一次演示场景、判断科学结论是否正确、验收。
> 时间按作者全天在场估算，单位是工作日。**每个版本结束时都必须能跑起来、能演示。** 路线图服从架构：顺序可以调，架构的承重决定不能为赶进度破例。

## v0.0 · 地基 + 假设验证（第 1 到 2 天）
- [x] 仓库骨架：uv、ruff、pyright、pytest、import-linter 契约、pre-commit、CI、Apache-2.0、CLAUDE.md、docs/adr/
- [x] `core/`：ID、事件信封、事件类型、规范消息格式、实体、哈希规则、状态机，全部带测试
- [x] `storage/`：事件表 + 投影 + `bq rebuild` 一致性测试
- [x] **对照实验**：20 份注入错误的分析记录，同家族 vs 跨家族 reviewer 的召回率和误报率；结果决定 ADR-011 是否成立 → 未发现同家族盲区，ADR-011 被 ADR-013 取代（docs/research/reviewer-experiment.md）
- [x] 三个北极星场景各 5 道评测题

## v0.1 · Demo：kernel + 审查 + MCP（第 3 到 7 天）★ 一周 Demo
- [x] `runtime/`：`Kernel` 接口、`FakeKernel`、`JupyterKernel`、输出摘要
- [x] `tools/`：注册表、`invoke()` 咽喉、内置工具（kernel、文件、连接器）——数据库连接器全部经外部 MCP 接入，`connectors/` 暂空
- [x] `provenance/`：CAS、Run 记录、manifest v0
- [x] `mcp/`：客户端接 2 个现成数据库 MCP（Open Targets 官方、BioMCP 或 ToolUniverse）；服务端暴露科学层——另有录制回放
- [x] `models/`：2 家 provider（1 个 Anthropic 或 OpenAI，1 个 OpenAI 兼容本地或 DeepSeek/Qwen），worker 和 reviewer 两个角色，cassette 录制回放——OpenAI（Responses）+ DeepSeek
- [x] `review/`：Claim 抽取 + 4 个确定性检查 + `citation_supports`——实测见 docs/research/review-checks.md
- [ ] `agent/` 最小循环；CLI：`bq run`、`bq review`
- **验收**：`bq run` 跑通简化版场景 C；`bq review` 抓出一条假引用；在 Claude Code 里通过 MCP 用同一套工具跑通；录一段终端演示

## v0.2 · 开发者预览（第 8 到 20 天）★ 第一次公开发布
- [ ] 场景 A 和 C 完整；场景 B 用 pbmc3k 规模的小数据集
- [ ] Plan 模式；compaction；权限卡片（CLI 交互版）
- [ ] 第 3 家 provider；能力矩阵；费用显示
- [ ] 研究包导出 v0 + `rerun.sh`
- [ ] `pip install bioquorum` / `uvx bioquorum`；README 首屏演示；快速开始文档；SECURITY.md 说明无沙箱
- [ ] 发布评测报告：各模型在三个场景的表现；reviewer 确定性检查与模型检查分开报告召回率和误报率
- [ ] 补测判断类错误（`citation_supports`、`method_sanity`、因果过度）上同家族 vs 跨家族的差异，每类至少 15 例（ADR-013）
- **验收**：PRODUCT.md §10 清单中除 Web UI 相关项外全部满足；在 HN、X 和相关社区发布

## v0.3 · Reproduce（第 21 到 35 天）
- [ ] 血缘 hook（pandas / anndata / matplotlib），失效不影响分析
- [ ] 一键重跑 + 结果比对 + 状态机推进到 `reproduced`
- [ ] bundle-spec v1.0；自动 Methods 草稿
- [ ] 真实 GEO 数据的场景 B
- **验收**：导出的研究包在干净机器上用 `rerun.sh` 重跑成功

## v0.4 · Workbench（第 36 到 60 天）
- [ ] `server/`：FastAPI + SSE
- [ ] `web/`：第一版只做一页，对话流 + 产物列表 + 审查卡片；构建产物随 Python 包发布
- [ ] 项目与会话管理；模型与角色设置页
- **验收**：不碰命令行完成场景 C

## v0.5 · Private
- [ ] 数据分级 + 路由强制执行 + 审计日志 UI
- [ ] OS 沙箱 + 网络白名单代理；外部 MCP 进程隔离
- [ ] Docker 镜像；服务器部署文档；本地模型部署指南
- [ ] SECURITY.md 完整版

## v0.6 及以后
- 共识模式、Specialists 并行子任务、更多连接器和 skills、R kernel
- 结构、分子、基因组查看器；三栏完整布局
- 基于评测的自动模型路由；插件 API 稳定化
- v1.0：bundle-spec、插件 API、配置格式承诺向后兼容
- v1.0 之后：SSH / Slurm / Modal、团队版（Postgres、SSO）、Tauri 桌面

## 风险与提前量
- ~~对照实验若显示跨家族审查无明显优势~~：已发生（2026-11-17）。ADR-011 被 ADR-013 取代，宣传不再说"跨家族更准"；交叉验证的卖点落在结论级确定性检查、可重跑和共识模式。
- pixi 与 bioconda 环境解析耗时：v0.1 的 kernel 先用项目已有的 Python 环境，pixi 锁定从 v0.2 起强制。
- 外部 MCP 服务器不稳定：每个连接器保留固定响应样本，演示可离线。
