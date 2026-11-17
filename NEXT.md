# 下一步（给回来的作者和任何 AI 协作者）

> 更新：2026-11-17。每次会话结束时更新本文件，让它始终回答"现在做到哪了、下一步干什么"。
> 接手的 AI（Claude Code、ChatGPT / Codex……）先读 `AGENTS.md`，再读本文件、`docs/ARCHITECTURE.md`、`docs/adr/`。

## 一句话现状
**v0.1.0 完成**（代码已推送到 `main`）：`bq run` 能用真实模型提问 → 调 Python kernel 和数据库工具 → 回答 → 逐条审查；`bq mcp serve` 能把同一套工具交给 Claude Code 等 MCP 客户端。没有 Web UI，**没有沙箱**。

## 作者还欠的两项 v0.1 验收（各 5 分钟，步骤在 `docs/demo-v0.1.md`）
- [ ] 第 4 步：在 Claude Code 里 `claude mcp add bioquorum -- …/bq mcp serve -C ~/bq-demo`，用 BioQuorum 的工具答一个问题，再 `bq review --answer-file` 审它。
- [ ] 第 5 步：录一段终端演示（asciinema）。

## v0.2 开发者预览：建议顺序
ROADMAP 里 v0.2 的完整范围见 `ROADMAP.md`。建议按下面顺序做，**开工前先和作者确认第 1 步的方案**（它需要一条新 ADR）。

### 1. 安全：kernel 的网络与文件隔离（最小版本）【先定方案，需 ADR-014】
- 问题（v0.1 验收实测）：worker 在 `python_execute` 里 `import requests` 自己联网下载了全文，绕过了 `invoke()` 的网络权限和分级检查。kernel 也能读写项目外的文件。
- 目标：project 分级为 internal / confidential 时，kernel 默认不能联网；联网只能走工具（有权限、有审计、有分级）。
- 候选方案（让作者选）：
  1. Linux：用 `unshare --net`（或 bubblewrap）启动 kernel，没有网络命名空间就拒绝启动 confidential 项目；macOS 用 `sandbox-exec`。最小、真实有效，但平台相关。
  2. 只在 kernel 进程里设置无效代理 + monkeypatch socket：容易实现，但**可被绕过**，只能算提示，不能算控制。
  3. 直接把 v0.5 的沙箱提前：工作量大。
- 无论选哪个：写 `SECURITY.md`，如实说明边界；`runtime/` 内部替换，不改上层接口（ARCHITECTURE §8）。

### 2. 审查可信度（卖点）
- 证据纳入 kernel 里读到的文献：run 输出中出现的 PMID/DOI 及其上下文也算证据（或要求全文走工具）。现在这类结论会被判 disputed。
- `value_match` 降低巧合命中：数字需与结论里的实体在同一句 / 同一行共现（验收时编造的 "48 trials"、"62%" 没被单独标出）。改完必须用 `evals/review_eval.py --heldout deepseek:deepseek-flash openai:gpt-5.4-mini` 复测，见 AGENTS.md 的评测纪律。
- `citation_resolves` 在线解析（经 BioMCP / PubMed 工具，走 `invoke()`）。
- `method_sanity` 模型检查；补测判断类错误上同家族 vs 跨家族的差异（ADR-013 承诺，每类 ≥ 15 例）。

### 3. ROADMAP v0.2 原有范围
- 场景 A（靶点档案）和 C（证据综述）完整；场景 B 用 pbmc3k 规模数据。用 `evals/questions/` 跑一轮，作者判断科学结论。
- Plan 模式（`plan_proposed` / `plan_approved` 事件已存在）；compaction；CLI 权限卡片完善。
- 第 3 家 provider（Anthropic 需要 `uv add "pydantic-ai-slim[openai,anthropic]"`，由作者运行）；能力矩阵；费用显示（现在 `cost_usd` 默认 0）。
- 研究包导出 v0 + `rerun.sh`（manifest v0 已有：`provenance/manifest.py`）。
- `pip install bioquorum` / `uvx bioquorum`；README 首屏演示；快速开始；评测报告（确定性检查与模型检查分开报告）。

### 4. 小的技术债
- 外部 MCP 服务器断线不重连。
- `storage/` 仍用 `create_all`：第一次改表结构前接 Alembic。
- 图片给模型时是占位文字（还没用视觉能力）。
- `evals/injected-errors/` 的 A 场景代码用的是假想的 `bq.tools.*` 接口（只影响可读性）。

## 怎么验证一切正常（接手第一件事）
```bash
uv sync --all-groups
uv run pytest && uv run lint-imports && uv run ruff check . && uv run pyright   # 210 单元测试，8 条契约
uv run pytest -m integration                                                    # 真实 Jupyter kernel（6 个）
uv run pytest -m live          # 需要 OPENAI_API_KEY / DEEPSEEK_API_KEY 和 biomcp（4 个，几美分）
uv run python evals/review_eval.py                                              # 审查检查：21/24，0 误报
```

## 需要作者做的事
1. 上面两项 v0.1 验收。
2. 决定 v0.2 第 1 步（kernel 隔离）用哪个方案。
3. API key 只在自己的终端里 export；Claude Code 要从那个终端启动（`claude --continue`）才能读到。
4. 加依赖时自己运行安装命令（AI 的安装命令会被拦截）。
5. 注册 PyPI 名 `bioquorum`；查商标。

## 回来后对 AI 说的话
> 继续 BioQuorum。先读 AGENTS.md 和 NEXT.md，再读 docs/ARCHITECTURE.md 和 docs/adr/，然后开始 v0.2（先和我确认 kernel 隔离方案）。

## 文档地图
| 文件 | 内容 |
|---|---|
| `AGENTS.md`（`CLAUDE.md` 引用它） | AI 协作约定、环境约束、踩过的坑、评测纪律 |
| `docs/ARCHITECTURE.md` v0.3 | 最高约束（承重 / 可调） |
| `docs/adr/001-013` | 已做出的决定（ADR-011 已被 ADR-013 取代） |
| `ROADMAP.md` / `docs/PRODUCT.md` | 版本范围 / 产品定义 |
| `CHANGELOG.md` | 版本变更 |
| `docs/research/reviewer-experiment.md` | 跨家族审查假设的对照实验（结论：不成立） |
| `docs/research/review-checks.md` | 确定性审查检查的规则与实测 |
| `docs/demo-v0.1.md` | 演示与验收步骤 |
| `docs/archive/` | 文档旧版本 |
