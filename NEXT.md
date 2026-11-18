# 下一步（给回来的作者和任何 AI 协作者）

> 更新：2026-11-18。每次会话结束时更新本文件，让它始终回答"现在做到哪了、下一步干什么"。
> 接手的 AI（Claude Code、ChatGPT / Codex……）先读 `AGENTS.md`，再读 `docs/DESIGN.md`、本文件、`docs/ARCHITECTURE.md`、`docs/adr/`。

## 一句话现状
**v0.1.0 加上 v0.2 的第一步**：`bq run` 提问 → kernel 和数据库工具 → 回答 → 逐条审查；`bq mcp serve`；没有 Web UI。**kernel 已经在 bubblewrap 沙箱里运行**（2026-11-18，ADR-017）。
**设计在 2026-11-17 重新定了方向**：作者把项目交给 AI 当 owner，作者已读过并认可 `docs/DESIGN.md`。决定记为 ADR-014 至 018，ARCHITECTURE / PRODUCT 升到 v0.4，ROADMAP 从 v0.2 起重排。
**已合进 `main` 并 push（2026-11-18）。** 以后的 commit / push 仍然只在作者明确要求时做。

方向的要点（细节看 DESIGN.md）：
1. 结论在写出时绑定证据，数字从 run 输出里取，引用带逐字原话（ADR-014）。
2. 可信度五级：断言 → 有据 → 已审查 → 已复现 → 已独立重复（ADR-015）。
3. Web 工作台提前到 v0.3，以研究记录为中心（ADR-016，取代 012）。
4. kernel 用 bubblewrap 隔离，网络默认关闭（ADR-017）。原先留给作者选的方案已由 owner 选定为方案 1。
5. harness 的机制可单独开关，用评测提升说话（ADR-018）。

## 下一步：v0.2 Grounded（按这个顺序）

### 1. kernel 隔离（ADR-017）✅ 2026-11-18，剩一项
- [x] `runtime/sandbox.py`：bwrap 参数、可用性探测、分级决定；`JupyterKernel(sandbox=...)`；`Kernel.isolation` 记入 `run_started`。
- [x] internal / confidential 在隔离不可用时拒绝；public 带警告运行；`[kernel] network = true` 仅 public。
- [x] 测试：单元 12 个（参数、策略、workspace），集成 8 个（联网失败、项目外文件不可见、数据目录只读、`.bioquorum/` 不可见、配置只读、无密钥、打断和超时后 kernel 存活、pandas 读写）。
- [x] `SECURITY.md`；worker 系统提示说明沙箱。
- [x] **数据下载工具** `download_file`（2026-11-18，`tools/builtin/download.py`）：存到 `downloads/` 和 CAS，URL / hash / 大小进事件；只限 public 项目；拒绝私有和本机地址；流式大小上限。单元测试 22 个；实测从 NCBI GEO 下了 9.4 MB 的文件，沙箱 kernel 能读。
  - 局限：internal / confidential 项目现在取不了公开数据。等真有需要时再定（候选：按主机白名单放开，URL 本身算不算泄露要想清楚），需要新 ADR。
- [ ] 用真实模型跑一次 `bq run`，确认模型看到"不能联网"后会改用工具（需要作者的 key；见下面"需要作者做的事"）。

### 2. 证据绑定（ADR-014）
- 先写一页设计笔记放进 ADR-014 的"可调"部分：别名规则、数值格式化、`bq.value` 的输出怎么进 `run_output` 事件（自定义 MIME）。
- 顺序：`bq.value` 捕获 → 解析器（`{{name}}`、`[@来源 "原话"]`）→ `grounded` 状态（`core/status.py` 转移表 + 测试）→ 解析失败退回 → CLI 渲染。
- 新事件类型或载荷字段落地时，同步改 ARCHITECTURE §5.3（有测试检查）和 `bundle-spec/`。

### 3. 循环按预算运行（ADR-018）
- 预算取代 12 步上限；压缩；CLI 流式输出；费用显示。每项一个配置开关。

### 4. 评测
- `evals/` 加 harness 提升评测（裸跑对比带 harness）。
- 第 3 家 provider（Anthropic 需要作者运行 `! uv add "pydantic-ai-slim[openai,anthropic]"`，或换一个 OpenAI 兼容的开源模型服务）。
- 报告：绑定遵从率、编造数字检出率（对比 v0.1 `value_match`）、绑定前后 rubric 得分；留出集分开写。

### 5. 场景与小债
- 场景 C 完整；场景 B 用 pbmc3k。
- 外部 MCP 断线重连；改表前接 Alembic；`citation_resolves` 在线解析。

## 需要作者做的事
1. ~~读 `docs/DESIGN.md`~~ 已读，已认可（2026-11-18）。
2. ~~commit / push~~ 已完成（2026-11-18）。
2a. 在自己的终端里跑一次 `docs/demo-v0.1.md` 第 2 步的 `bq run`（要花几美分），看模型在沙箱里是否正常工作，把看到的问题告诉 AI。
3. v0.1 遗留的一项验收（可选，5 分钟）：在 Claude Code 里 `claude mcp add bioquorum -- …/bq mcp serve -C ~/bq-demo`，步骤在 `docs/demo-v0.1.md` 第 4 步。终端录屏那一项取消了。
4. 决定第 3 家模型用哪个（有 Anthropic key，还是用开源模型）。
5. API key 只在自己的终端里 export；Claude Code 要从那个终端启动才能读到。加依赖时自己运行安装命令。
6. 注册 PyPI 名 `bioquorum`；查商标（v0.3 之前）。

## 怎么验证一切正常（接手第一件事）
```bash
uv sync --all-groups
uv run pytest && uv run lint-imports && uv run ruff check . && uv run pyright   # 244 单元测试，8 条契约
uv run pytest -m integration                                                    # 真实 Jupyter kernel，含沙箱（14 个）
uv run pytest -m live          # 需要 OPENAI_API_KEY / DEEPSEEK_API_KEY 和 biomcp（4 个，几美分）
uv run python evals/review_eval.py                                              # 审查检查：21/24，0 误报
```

## 回来后对 AI 说的话
> 继续 BioQuorum。先读 AGENTS.md、docs/DESIGN.md 和 NEXT.md，再读 docs/ARCHITECTURE.md 和 docs/adr/，然后继续 v0.2 第 2 步（证据绑定，ADR-014）。

## 文档地图
| 文件 | 内容 |
|---|---|
| `AGENTS.md`（`CLAUDE.md` 引用它） | AI 协作约定、环境约束、踩过的坑、评测纪律 |
| `docs/DESIGN.md` | 设计理念：为什么这样做（四份文档冲突时它优先） |
| `docs/ARCHITECTURE.md` v0.4 | 最高技术约束（承重 / 可调） |
| `docs/adr/001-018` | 已做出的决定（011 被 013 取代，012 被 016 取代） |
| `ROADMAP.md` / `docs/PRODUCT.md` v0.4 | 版本范围 / 产品定义 |
| `CHANGELOG.md` | 版本变更 |
| `docs/research/reviewer-experiment.md` | 跨家族审查假设的对照实验（结论：不成立） |
| `docs/research/review-checks.md` | 确定性审查检查的规则与实测 |
| `docs/demo-v0.1.md` | v0.1 演示与验收步骤 |
| `docs/archive/` | 文档旧版本 |
