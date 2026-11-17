# 下一步（给回来的自己和 Claude Code）

> 更新：2026-11-17。每次会话结束时更新本文件，让它始终回答"现在做到哪了、下一步干什么"。

## 现在的状态：v0.1 代码完成，等作者做两项验收
v0.1 的全部模块都已实现并测试（单元 210、集成 6、live 4，ruff / pyright / 8 条 import-linter 契约全绿）：

| 层 | 内容 |
|---|---|
| `runtime/` | `Kernel` 接口、`FakeKernel`、`JupyterKernel`（私有目录 IPC socket，工作目录 = 项目根）、`KernelManager`、输出摘要 |
| `provenance/` | CAS、DataFrame/AnnData 规范哈希、manifest v0 |
| `policy/` | 权限（once/session/project/global，无 asker 默认拒绝）、分级检查、密钥打码与 `env:` 引用解析 |
| `tools/` | `ToolSpec`、注册表、`invoke()` 咽喉（每个调用 ID 必有结果事件）、`python_execute` / `read_file` / `list_files`、外部 MCP 包装、MCP 出口 |
| `mcp/` | 客户端、服务端、cassette 录制回放；Open Targets 官方远程 + BioMCP 已接通，样本在 `tests/fixtures/mcp/` |
| `models/` | `pydantic-ai-slim[openai]`；配置、按分级路由 + 回退链、唯一转换点、录制回放、`ModelClient`；OpenAI（Responses）+ DeepSeek |
| `review/` | 4 个确定性检查 + `citation_supports` + Claim 抽取 + 审查事件；实测见 `docs/research/review-checks.md` |
| `agent/` | 无状态循环，上下文每步从事件重建 |
| `cli/` | `bq project init/show`、`bq models`、`bq tools`、`bq run`、`bq review [--answer-file]`、`bq sessions`、`bq mcp serve/record`、`bq db ...` |

ROADMAP v0.1 验收：
- [x] `bq run` 跑通简化版场景 C（真实 GPT-6.1-sol + BioMCP + Jupyter，DeepSeek 审查）
- [x] `bq review` 抓出假引用
- [ ] **作者**：在 Claude Code 里通过 MCP 跑通（`docs/demo-v0.1.md` 第 4 步）
- [ ] **作者**：录终端演示（`docs/demo-v0.1.md` 第 5 步）

## v0.1 验收中发现、尚未解决的问题（v0.2 优先处理）
1. **没有沙箱，kernel 可以自己联网**：worker 在 `python_execute` 里用 `requests` 下载了全文，绕过了网络权限和分级检查。confidential 项目在沙箱（v0.5）之前不能视为隔离。现在只有提示词软约束。v0.2 的 SECURITY.md 必须写清楚；可考虑提前做"kernel 禁网"的最小版本（例如启动 kernel 时去掉代理 / 用网络命名空间），需要一个 ADR。
2. **审查只看得到会话检索到的内容**：worker 在 kernel 里读的全文 reviewer 看不到，相关结论会被判 disputed。方案：把 run 输出里出现的 PMID/DOI 文本也纳入证据，或者要求全文走工具。
3. **数值"出现即通过"**：证据很长时，编造的数字可能碰巧在别处出现（验收时 "48 trials"、"62%" 没被单独标出）。方案：数字需与结论中的实体同句/同行共现。
4. 外部 MCP 服务器中途断开不会自动重连。
5. `citation_resolves` 还没有在线解析（PubMed / Crossref）；`method_sanity` 模型检查未做；费用表需手工配置；图片给模型时是占位文字。
6. `storage/` 仍用 `create_all`，第一次改表结构前补 Alembic。
7. `evals/injected-errors/` 里 A 场景代码用的是假想的 `bq.tools.*` 接口（只影响语料可读性，不影响评测）。

## 下一步：v0.2 开发者预览（见 ROADMAP）
建议顺序：先处理上面第 1–3 条（安全与审查可信度是卖点），再做 ROADMAP v0.2 的场景 A/C 完整、Plan 模式、研究包导出、`pip install bioquorum`、README 与评测报告。开工前和作者确认顺序。

## 需要作者做的事
1. 完成上面两项验收（`docs/demo-v0.1.md`）。
2. API key 只在自己的终端里 export；从那个终端用 `claude --continue` 启动，Claude Code 才能读到。
3. 加依赖时请用 `! uv add ...` 自己运行（自动模式会拦截安装命令）。
4. 注册 PyPI 名 `bioquorum`。

## 回来后对 Claude Code 说的话
> 继续 BioQuorum。先读 NEXT.md、docs/ARCHITECTURE.md 和 docs/adr/，然后开始 v0.2。

## 文档地图
- 架构（最高约束）：`docs/ARCHITECTURE.md`；决定：`docs/adr/`（ADR-011 已被 ADR-013 取代）
- 路线图：`ROADMAP.md`；产品：`docs/PRODUCT.md`
- 实验与评测：`docs/research/reviewer-experiment.md`（跨家族假设）、`docs/research/review-checks.md`（确定性检查）
- 演示：`docs/demo-v0.1.md`
