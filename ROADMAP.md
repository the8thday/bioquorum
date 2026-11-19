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
- [x] **循环按预算运行**（ADR-018、019）：独立配置 token、费用和时间预算，压缩历史，流式显示生成进度，按配置费率展示估算费用。
- [x] **评测**：裸跑和完整 harness 对照，三套模型的绑定评测，留出题上的数字错误注入，与本地 Ollama 的运行控制对照。OpenAI、DeepSeek 和 Ollama 均有实际运行记录。结果见 `docs/research/`。
- [x] 场景 C 以二甲双胍证据表验收，场景 B 以 PBMC 3k QC 验收。见 [v0.2 演示](docs/demo-v0.2.md)。
- [x] 外部 MCP 断线重连，Alembic 旧库升级，`citation_resolves` 在线解析。
- **验收**：B、C 的 CLI 输出、事件日志和隔离集成测试已通过。科学内容待作者验收。

## v0.3 · Workbench：工作台 ★ 可安装候选版
目标：不碰命令行完成一次研究，并且看得见证据。
- [x] `server/`：FastAPI + SSE，从断点读取事件，转发 CLI 共用的研究调用。新增分层契约。
- [x] `web/`：研究记录、证据抽屉、活动流、输入框、计划 / 权限 / 审查卡片和会话列表。静态前端随包分发。
- [x] 计划模式：批准前不执行，审批与偏离原因写入事件，计划按轮启用。
- [x] 打断后可改方向；会话在固定历史位置分叉。
- [x] 加入 `bioquorum` 和 `bq workbench` 入口，提供候选 wheel、快速开始、`CONTRIBUTING.md` 和 `THIRD_PARTY.md`。
- [x] 离线场景 C 浏览器自动验收。见 [v0.3 演示](docs/demo-v0.3.md)。
- [ ] 作者验收科学内容和布局，批准后再发布到 PyPI 验证公开安装入口。

## v0.4 · Quorum：复现与独立重复 ★ 开发候选版
目标：60 秒演示的七步全部是真的。
- [x] 从 CAS 输入恢复 kernel 单元链并比对；等级升级门槛；可选 pandas / AnnData / Matplotlib 事后血缘。
- [x] 盲重复的新会话、输入隔离、预登记容差、费用估计和差异并排显示。真实锁定环境下的验收见下条。
- [x] 研究包 v1、绑定关系、`rerun.sh` 和已预配 pixi 环境的启动入口。
- [x] 可选 `method_sanity`。两种本地模型已初测各 15 个构造场景，结果和局限见 [评测记录](docs/research/v04-verification.md)。
- [x] 接入 BixBench v1.5 / LAB-Bench TableQA 题目子集，保存上游版本与哈希；模型实跑得分待补。
- [x] 准备文档入口、三个场景入口和手动触发的发布工作流。
- [ ] Linux 独立重复、第二台机器重跑研究包、公开基准实跑。暂停项及完成证据见 [v0.4 验收清单](docs/release/v0.4-acceptance.md)。
- [ ] 完整七步录屏放到 README。
- [ ] 作者验收、配置 PyPI 发布权限与正式发布。

## v0.5 · Depth：把 agent 做深
每一项合入时附带 harness 提升评测的前后对比（ADR-018），没有提升的不留。
- [x] 独立上下文的文献检索子任务，默认关闭。显式 `/literature` 已通过合成对照和场景 A 的真实文献检索。见 [评测报告](docs/research/v05-literature-subtasks.md)。
- [x] 首批：Skills（SKILL.md 渐进加载）与跨会话项目笔记。默认关闭，见 [v0.5 使用说明](docs/demo-v0.5.md)。
- [x] 场景 A 的 IL23R 样例端到端、场景 B 的 GSE96583 主流程、自动 Methods 草稿。[验收记录](docs/research/v05-scenarios.md)
- [x] 工具结果作为审计过的 kernel 输入，默认关闭。[使用说明](docs/demo-v0.5.md)
- [x] 按项目和模型选项发送图片，默认关闭。已验证 OpenAI 兼容 Chat 请求体。
- [x] 场景 B 两组预先指定的 QC 选择重跑，比较供者与差异结果。
- [x] 场景 A 其余四题已实跑；Apple Vision 在真实图上读标签通过，记录自主检索覆盖问题。[扩展结果](docs/research/v05-target-expansion.md)
- [ ] 通用视觉语言模型与真实远端提供方验收，作者确认科学内容和发布。[v0.5 验收清单](docs/release/v0.5-acceptance.md)

## v0.6 · Private：私有部署
- [x] 数据分级与审计日志界面，本机浏览器回归通过。
- [x] HTTPS 出站白名单代理、离线 stdio MCP 隔离、macOS 内核隔离已实现。本机运行与边界证据见验收清单。
- [x] 编写 Docker CLI 镜像定义、服务器和本机模型部署说明及 `SECURITY.md`，构建开发 wheel。
- [ ] 在部署环境构建 Docker 并验证 Linux 隔离、实际 HTTPS 出站和远程工作台。[v0.6 验收清单](docs/release/v0.6-acceptance.md)

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
