# 下一步（给回来的自己和 Claude Code）

> 更新：2026-11-17。每次会话结束时更新本文件，让它始终回答"现在做到哪了、下一步干什么"。

## 现在的状态：v0.1 进行中，科学层地基已完成
- v0.0 已 commit 并推送（`846bc9e`）。
- **对照实验已完成（2026-11-17）**：GPT-5.5 / GPT-6.1-sol / DeepSeek-v4-pro 两轮交叉，可核实错误上同家族 89.5% vs 跨家族 88.7%，没有发现同家族盲区。ADR-011 被 **ADR-013** 取代：reviewer 按审查能力选择，`prefer_different_family` 可选，同家族不再警告。报告见 `docs/research/reviewer-experiment.md`。实验脚本修了两处：OpenAI 兼容模型看不到 JSON schema；删 run 产生的连带结论被误算成误报。
- 2026-11-16/17 会话新增的 v0.1 代码：
  - `runtime/`：`Kernel` Protocol（`kernel.py`）、`FakeKernel`（`fake.py`，可脚本化超时/中断/报错）、`JupyterKernel`（`jupyter.py`）、`KernelManager`（每会话一个 kernel，会话内串行）、`summarize.py`（截断、图像占位、DataFrame/AnnData 摘要）。
  - `provenance/`：`ContentStore`（CAS，写入/读取都校验 hash）、DataFrame/AnnData 规范序列化器注册进 `core.hashing`、`build_manifest()`（从事件纯函数生成 manifest v0）。
  - `policy/`：`PermissionPolicy`（按 once/session/project/global 记住授权，无 asker 时默认拒绝）、`classification.check()` + `PolicyViolation`、`secrets.redact()`。
  - `tools/`：`ToolSpec`、`ToolRegistry`（`definitions()` 是 agent 和 MCP 两个出口的唯一来源）、`invoke()` 咽喉、`runs.py`（一次执行 → Run 事件 + 图进 CAS）、内置工具 `python_execute` / `read_file` / `list_files`。
  - 新增 import-linter 契约：agent/review/models 不得直接 import runtime/connectors/mcp（落实 ADR-006）。共 7 条。
  - 新依赖：`jupyter_client`、`ipykernel`（已写入 pyproject 和 uv.lock）。
- 质量：116 个单元测试 + 5 个真实 kernel 集成测试（`uv run pytest -m integration`）全绿；ruff、pyright、lint-imports 通过。
- ARCHITECTURE §6.2、§6.4 补充了接口细节（`ExecutionEnd` 终止帧、`shutdown()`、`classification_floor` 的确切含义、invoke 不抛异常、远端工具禁止 trusted）。没有新增事件类型。

## 下一步（v0.1 剩余，按顺序）
1. `connectors/` + `mcp/client.py`：接 Open Targets 官方 MCP 和 BioMCP（或 ToolUniverse），外部工具自动包装为 `ToolSpec(permission=external_mcp, output_trust=untrusted)`；每个连接器存固定响应样本，测试离线。需要依赖 `mcp`。
2. `mcp/server.py`：从 `ToolRegistry.definitions()` 生成 MCP Server，`bq mcp serve`；在 Claude Code 里验证。
3. `models/`：Pydantic AI，2 家 provider（作者手上有 OpenAI + DeepSeek 的 key），worker/reviewer 角色，reviewer 按配置里的审查成绩选择（ADR-013），`convert.py`，cassette 录制回放。需要依赖 `pydantic-ai`。
4. `review/`：Claim 抽取 + 4 个确定性检查（`execution_evidence`、`value_match`、`citation_resolves`、`plan_complete`）+ `citation_supports`；用 `evals/injected-errors/` 验证确定性检查 100%。
5. `agent/` 最小循环；CLI `bq run`、`bq review`；权限卡片的 CLI 版 Asker。
6. 验收：ROADMAP v0.1 的四条。

## 需要作者做的事
1. API key 只在作者自己的终端里 export；从那个终端用 `claude --continue` 启动，Claude Code 才能读到。
2. 下一步要加 `mcp`、`pydantic-ai` 依赖：自动模式会拦截安装命令，届时请用 `! uv add ...` 自己运行。
3. 注册 PyPI 名 `bioquorum`。

## 回来后对 Claude Code 说的话
> 继续 BioQuorum。先读 NEXT.md、docs/ARCHITECTURE.md 和 docs/adr/，然后接着做 v0.1 的下一步。

## 已知的偏差和待办
- `storage/` 仍用 `metadata.create_all`，还没接 Alembic；第一次改表结构前补上。
- `evals/injected-errors/` 里 A 场景代码调用了假想的 `bq.tools.*` 接口；工具名现已定为 `python_execute` / `read_file` / `list_files`，连接器定名后一起改。
- 对照实验 `self` 模式只注入三类机械错误；判断类错误的补测排在 v0.2（ADR-013）。
- 权限授权目前只在内存里；project/global 级授权的持久化随配置文件一起做。
- Run 的 `inputs` 目前为空（`run_key` 只含 code + env），血缘 hook 在 v0.3。
- AnnData 的 h5ad 规范序列化未验证字节级确定性（环境里没装 anndata）；场景 B 前补测试。
- `env_lock_hash` 为 None，pixi 锁定从 v0.2 起强制。
