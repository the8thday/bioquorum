# BioQuorum 路线图（单人 + AI 协作版）

> 2026-11-11 · 产品定义见 [docs/PRODUCT.md](docs/PRODUCT.md) · 架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · 调研见 [docs/research/landscape-2026-11.md](docs/research/landscape-2026-11.md)
>
> 分工：Claude 负责设计、写代码、写测试和文档；作者负责定方向、跑场景、判断科学结论是否正确、验收。
> 时间是按业余投入估算的，**每个版本结束时都必须能跑起来、能演示**。

## v0.0 · 地基（约 1 周）
- [ ] 确定名称（查商标、注册 GitHub 组织、占 PyPI 名）
- [ ] 仓库骨架：uv、ruff、pyright、pytest、import-linter、pre-commit、CI、Apache-2.0、CLAUDE.md
- [ ] `core/` 领域模型 + `storage/` 事件日志
- [ ] 动手试用 opencode + K-Dense skills + ToolUniverse，记录现有工具的不足（作为对照组）
- [ ] 为三个北极星场景各写 5 道题，作为评测集的第一版

## v0.1 · Kernel：科学层 + CLI（3–4 周）· 开发者预览
- [ ] `runtime/`：常驻 Python kernel，输出摘要，图片存为产物
- [ ] `provenance/`：CAS 存储、Run 记录、产物版本、pixi 环境锁定
- [ ] `connectors/`：Open Targets、ChEMBL、UniProt、PubMed、ClinicalTrials.gov（各自带测试）
- [ ] `mcp/server.py`：把科学层暴露成 MCP，在 Claude Code / opencode 里实测
- [ ] `models/` + `agent/` 最小版本：Pydantic AI 循环，按角色配置模型，接 3 家 provider
- [ ] CLI：`bq run "..."`
- **验收**：用 CLI 在 3 家模型上跑通场景 B 的简化版；用 Claude Code 通过 MCP 也能跑通

## v0.2 · Workbench：Web UI（4–5 周）
- [ ] `server/`：FastAPI + SSE 事件流
- [ ] `web/`：三栏布局、对话流、代码步骤、产物面板、权限卡片
- [ ] 项目和会话管理，模型与角色的设置页，费用显示
- [ ] `uvx bioquorum` 一条命令启动
- **验收**：不碰命令行也能完成场景 B

## v0.3 · Verify：交叉验证 ★ 第一次公开发布（4–5 周）
- [ ] `review/`：结论抽取、六个检查器、审查卡片、验证状态标记
- [ ] Plan 模式
- [ ] 引用核实（Crossref / PubMed）
- [ ] 共识模式 v1
- [ ] 场景 A、C 跑通；示例项目、演示视频、文档站；完成"拿得出手"清单（PRODUCT.md §10）
- [ ] 发布评测报告：各模型在三个场景上的表现，以及 Reviewer 召回率
- **验收**：Reviewer 在人工注入错误的测试集上召回率 ≥ 70%；在 HN、X 和相关社区发布

## v0.4 · Reproduce：研究包（3–4 周）
- [ ] 血缘 hook（pandas / anndata / matplotlib）
- [ ] 研究包导出和导入，`rerun.sh`，`bundle-spec` v1.0
- [ ] 一键重跑 + 结果比对，状态标记为 `已复现`
- [ ] 自动生成 Methods 草稿；Python SDK

## v0.5 · Private：私有化（3–4 周）
- [ ] 数据分级 + 路由强制执行 + 审计日志 UI
- [ ] OS 沙箱 + 网络白名单代理；外部 MCP 进程隔离
- [ ] Docker 镜像，服务器部署文档；本地模型（vLLM / Ollama）部署指南
- [ ] SECURITY.md 完整版

## v0.6–v0.9 · 扩展
- Specialists 和并行子任务；更多连接器和 skills；R kernel 完善
- 结构、分子、基因组查看器
- 基于评测结果的自动模型路由
- 插件 API 稳定化

## v1.0 · 稳定版
- bundle-spec、插件 API、配置格式承诺向后兼容
- 文档完整，评测报告定期更新

## v1.0 之后
- SSH / Slurm / Modal 远程算力
- 多用户团队版（Postgres、SSO）
- 桌面应用（Tauri）
