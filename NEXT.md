# 下一步（给回来的自己和 Claude Code）

> 更新：2026-11-13。每次会话结束时更新本文件，让它始终回答"现在做到哪了、下一步干什么"。

## 现在的状态：v0.0 代码已写完，等作者验收
- 文档：`docs/ARCHITECTURE.md` v0.2（最高约束）、`docs/adr/001-012`、`ROADMAP.md`、`docs/PRODUCT.md` v0.2、`CLAUDE.md`。
- 代码：`core/`（ID、事件、消息、实体、哈希、状态机、研究包 manifest）、`storage/`（事件表、9 张投影表、重建）、`cli/`（`bq version`、`bq db init|rebuild|events`）。
- 质量：51 个单元测试全绿；ruff、pyright、import-linter 六条契约全部通过；pre-commit 已安装；CI 工作流已写。
- 评测：`evals/questions/` 15 道题；`evals/injected-errors/` 24 个案例（20 个含 36 处注入错误 + 4 个干净对照）；`evals/reviewer_experiment.py` 对照实验脚本（未运行，需要 API key）。
- 仓库已 `git init`，**还没有任何 commit**。

## 作者验收 v0.0 的三步
```bash
uv sync --all-groups
uv run pytest && uv run lint-imports && uv run ruff check . && uv run pyright
```
然后通读 `src/bioquorum/core/`，对照 `docs/ARCHITECTURE.md §5`。

## 需要作者做的事
1. 提供两家不同家族模型的 API key（例如 `ANTHROPIC_API_KEY` + `DEEPSEEK_API_KEY`，或本地 Ollama），然后运行对照实验：
   ```bash
   uv sync --all-groups --extra eval
   uv run python evals/reviewer_experiment.py self --worker anthropic:claude-opus-5-5 \
       --reviewer anthropic:claude-opus-5-5 --reviewer deepseek:deepseek-chat \
       --report docs/research/reviewer-experiment.md
   ```
   结果决定 ADR-011 是否成立。
2. 决定是否做第一次 commit（Claude 不会自行 commit）。
3. 注册 GitHub 组织和 PyPI 名 `bioquorum`。

## 回来后对 Claude Code 说的话
> 继续 BioQuorum。先读 NEXT.md、docs/ARCHITECTURE.md 和 docs/adr/，然后开始 ROADMAP 的 v0.1。

## 已知的偏差和待办（v0.1 要处理）
- `storage/` 目前用 `metadata.create_all`，还没接 Alembic 迁移；第一次改表结构前补上（ARCHITECTURE §4 标为可调）。
- `evals/injected-errors/` 里 A 场景的代码调用了假想的 `bq.tools.*` 接口，v0.1 工具层定名后要同步改。
- 对照实验的 `self` 模式只注入三类错误（数值、假引用、无执行证据）；`static` 模式覆盖全部七类。
- `core/hashing.py` 的 DataFrame / AnnData 规范序列化器要在 `provenance/` 里注册（v0.1）。
