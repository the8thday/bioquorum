# 下一步（给回来的作者和任何 AI 协作者）

> 更新：2026-11-20（0.7 收尾，下次从 0.8.1 开始）。每次会话结束时更新本文件，让它始终回答"现在做到哪了、下一步干什么"。
> 接手的 AI（Claude Code、ChatGPT / Codex……）先读 `AGENTS.md`，再读 `docs/DESIGN.md`、本文件、`ROADMAP.md`、`docs/ARCHITECTURE.md`、`docs/adr/`。

## 当前交接

作者接受现有界面作为 0.7 的交付版本，工作台以 `0.7.0.dev0` 开发预览收尾。阅读[演示](docs/demo-v0.7.md)可复走流程，[验收记录](docs/release/v0.7-acceptance.md)列有测试结果和覆盖范围。左右栏调宽与图表并排阅读的设计见 [ADR-037](docs/adr/037-result-reader.md)。

**下一步是 0.8.1 评测基建。** 从 [路线图的 v0.8 部分](ROADMAP.md) 的三个研究场景开始整理题目、评分标准和预期证据类别，固定开发集与留出集，请作者分批确认科学内容。随后用统一入口比较 `minimal` 和当前默认配置，把留出集基线写进 `docs/research/v08-baseline.md`。

## 此前各步记录

v0.0 至 v0.6 的代码已完成。2026-11-20 做了设计复核，只改了文档，没有改代码：

- DESIGN.md：判断⑤改为"交得出去，接得回来"；新增⑨"覆盖先于结论"和⑩"记录要比对话活得久"；③补充推荐配置和对照。"抗体推到 PCC"定为宣传语和长期试金石，不限定产品范围。
- 新增 ADR-028 至 032：Agent 先调查、前端改为组件化工程（取代 ADR-021 的前端部分）、覆盖台账、研究线、推荐配置与两天对照。
- ROADMAP.md 重写：v0.7 工作台重做 → v0.8 覆盖 → v0.9 Python 与 R → v0.10 研究线 → v0.11 查看器与领域工具 → v0.12 长任务与并行 → v1.0。每一步有编号、涉及的包、新事件和验收；第 0 节是执行规则。
- PRODUCT.md 升到 v0.5，ARCHITECTURE §6.9、§10、§11 同步。

**0.7.0 已验收。** React 工作台的 5 项组件测试、TypeScript 构建和 3 条浏览器流程通过（metformin、项目笔记冲突、真实计算重跑与下载）。旧入口已删除。两个空目录构建结果逐字节一致；wheel 解包后在无 Node 的 PATH 下能启动并读取界面和离线项目。后端 432 项通过，26 项按标记排除，9 条导入契约、Ruff、Pyright 通过。构建产物已在工作区，提交由作者决定。

**0.7.1 已验收。** 浏览器连续三轮追问、停止等待权限的轮次、改问、刷新一致、改名、按问题搜索、归档后读取原记录通过。后端 433 项通过，9 条契约、Ruff、Pyright、5 项组件测试和 4 条浏览器流程通过。

**0.7.2 已验收。** 输入框选择本轮 worker，按项目分级显示可选项；选项随请求写入事件，v1 事件有升级函数。回答按成功调用的 ID 显示实际模型。离线测试覆盖跨家族 thinking 的保留与丢弃、不可选模型的请求拒绝、计划中的模型选择、旧事件读取。浏览器已在同一会话切换模拟 OpenAI → DeepSeek → OpenAI，并核对回答上的模型。后端 435 项、9 条导入契约、Ruff、Pyright、5 项组件测试均通过。

作者在已配置的终端实测 OpenAI → DeepSeek → OpenAI，报告 `1 passed, 2 deselected in 33.52s`。0.7.3 已验收：三层用量按成功调用计数，未知价格单列，子任务计入所属轮次。CLI 与 HTTP 数据一致。后端 436 项、6 项前端测试、4 条浏览器流程和全部静态检查通过。0.7.4 已验收：实测手动压缩后对话估算下降，下一轮读取摘要，同一旧结论的证据原文完整；选中模型窗口、摘要和分叉入口已接入。437 项后端测试、6 项组件测试、4 条浏览器流程、静态检查通过。

**0.7.5 已验收。** 项目设置可从侧栏打开。浏览器实测开关保存并刷新，CLI 使用相同事件覆盖 TOML，重建后保留。模型角色、费率、窗口、预算和权限撤销经过重开项目测试。页面只显示环境变量名称及工作台进程看到的 set / missing。439 项默认后端测试、6 项前端测试、4 条浏览器流程、9 条导入契约、Ruff、Pyright 通过。连接器重连的外部可用性取决于本机配置。下一步是 0.7.6 选中内容继续问。

**0.7.6 已验收。** 真实 OpenAI 对照中，打开引用后开发集 1/1、留出集 2/2 同时指出正确算法与列名，关闭时三题均回答无法判断。默认测试、四项后端检查、六项前端测试和五条浏览器流程通过。见 [追问对照](docs/research/v07-references.md)。下一步是 0.7.7 文件与产物。

**0.7.7 已验收。** 工作台上传 CSV 后，真实 Jupyter kernel 读两行计数得到 7；从答案查看计算时，输入哈希与上传字节一致。已验证分页、版本、项目分级、会话作用域和 notebook 导出。四项后端检查、组件测试及六条浏览器流程通过。下一步是 0.7.8 首次使用。

**0.7.8 已验收。** 浏览器已覆盖没有 worker、kernel 隔离不可用、MCP 重连失败三种状态。从未配置模型的项目进入离线示例可得到带证据的结果；示例记录与原项目独立。下一步是 0.7.9 验收与文档。

v0.3 至 v0.6 欠的验收列在 ROADMAP 第 1 节，统一在 `1.0.1` 收口，能提前做的随时做。下面是此前各次会话的记录，保留备查。

v0.6 当时以 `0.6.0.dev0` 开发预览为入口，列出实测研究场景，并链接到[私有部署验收清单](docs/release/v0.6-acceptance.md)。

## 2026-11-20 Linux 服务器验收（Ubuntu 24.04，bubblewrap、pixi 0.84.2、Docker 29.1）

作者把 v0.2–v0.6 的代码 push 后，在服务器上做了留给 Linux 的验收。实际模型调用都用 OpenAI（AGENTS.md 已写明优先 OpenAI）。

- **v0.4**：在真实 bwrap kernel 和锁定的 pixi 环境里，同一结论走完 `unverified → grounded → reviewed → reproduced → replicated`。研究包在独立目录和不含 BioQuorum 的 Debian 12 容器里都重跑成功。公开基准四题实跑 4/4（只说明流程跑得通）。独立物理机器和跨操作系统的研究包重跑还没做。见 [v0.4 验收](docs/release/v0.4-acceptance.md)。
- **v0.5**：远程视觉模型通过（开图 3/3 对，关图 3/3 回答看不到图）。四道靶点题用 GPT-6.1-sol 复测，检索、绑定和审查都明显改善，科学覆盖仍不完整（AI 初查各约 3/5）。见 [v0.5 验收](docs/release/v0.5-acceptance.md)。
- **v0.6**：Linux 上的隔离 MCP 进程、HTTPS 代理真实往返、Docker 镜像构建和烟测都通过。容器里没有 kernel 隔离（宿主机限制了非特权用户命名空间），confidential 项目会正确拒绝。见 [v0.6 验收](docs/release/v0.6-acceptance.md)。
- 实跑发现并已修复：`import bq` 报错；盲重复的 run 没记锁摘要；v0.4 示例环境缺 pandas（失败的 import 让 kernel 链不能用于重复，这条规则没放宽）。新增 Linux MCP 隔离集成测试、远程视觉评测、公开基准执行脚本；TableQA 改为上游的选择题形式。

## 本次独立审查修复

CLI 与工作台共用项目锁，重放继承隔离策略，审查限制和导出材料缺口可追溯。GSE96583 两个文库的 MT UMI 均为零，已按文库标记不可评估。实现决策见 [ADR-027](docs/adr/027-review-and-replay-integrity.md)。Linux/Docker、第二台机器、外部视觉语言模型和作者发布验收继续按对应版本清单推进。

工作台改用自然史期刊版式，入口为原创放射虫风格插画，证据抽屉保留原文和计算回溯。可用离线 metformin 示例验收桌面和手机观感。

## 目前进度

**v0.5 主功能已经实现。** 第一批是项目 Skills 按需加载和跨会话笔记。Skill 只先加载目录描述，
需要时才经工具读正文。共享笔记由作者保存，模型草稿只留在提出它的会话。
两项机制默认关闭，独立配置。见 [v0.5 使用说明](docs/demo-v0.5.md)
和 [本地模型对照](docs/research/v05-project-context.md)。

文献子任务支持显式 `/literature`。三道合成题全部触发，两个回答带有效引用。场景 A 用 IL23R／IBD 跑通公开检索、语义审查、引用回溯与证据型导出。场景 B 用 GSE96583 的真实计数跑通 QC、探索性聚类、配对供者差异与研究包重跑。两个场景的研究包都带自动 Methods 草稿。场景 B 已换两组 QC 选择再跑。见 [本轮验收记录](docs/research/v05-scenarios.md)。

工具来源可通过独立开关成为带哈希的 kernel 输入。四道靶点扩展题已实跑，本地文本模型的自主检索覆盖未达标。实际 QC 图通过 Apple Vision 识读标签。通用视觉语言模型与真实外部端点待测。见 [v0.5 发布验收](docs/release/v0.5-acceptance.md)。

**v0.6 已生成开发包 `0.6.0.dev0`。** 数据分级和审计页经过浏览器检查，macOS 内核和离线 stdio MCP 通过真实进程测试，HTTPS 白名单代理已实现。Docker 定义和部署说明已补齐。需在有 Python 容器基础镜像、Linux 系统依赖和可解析公开地址的环境完成部署烟测。见 [v0.6 验收](docs/release/v0.6-acceptance.md)。

**v0.4 仍是候选版。** 本机已经用真实 Jupyter、Chromium 和解包脚本验证重跑及研究包，
候选 wheel 与样例研究包保留。暂缓的三项验收单独记录：
**Linux 端到端独立重复、第二台机器重跑研究包、公开基准实跑**。
完成条件见 [v0.4 验收清单](docs/release/v0.4-acceptance.md)。

## 接下来

- **作者**：科学验收 v0.5 四道靶点题的回答（`evals/results/v05/target-expansion-remote/*/rendered.md`，只在服务器本地），以及公开样例和发布。
- v0.4：把研究包拿到另一台物理机器上重跑（例如 Mac 导出的包到这台服务器，或反过来），补上最后一项。
- v0.5：靶点题的漏项（TYK2 的 JH2 选择性、PCSK9 的结局试验、NOD2 的三个主要变体）是检索路由问题还是合成问题，先看原始记录再决定，不为单题写规则。
- v0.6：本机模型（Ollama）在服务器上的端到端演练，需要先装 Ollama。
- 观察到但没处理：沙箱 kernel 启动时仍往终端打印 ipykernel 的提示文字；思考模式的 DeepSeek reviewer 很慢，长回答的审查可能要几分钟。

**设计在 2026-11-17 重新定了方向**：作者把项目交给 AI 当 owner，作者已读过并认可 `docs/DESIGN.md`。决定记为 ADR-014 至 018，ARCHITECTURE / PRODUCT 升到 v0.4，ROADMAP 从 v0.2 起重排。
沙箱、下载工具和证据绑定都已合进 `main` 并 push（2026-11-18）。以后的 commit / push 仍然只在作者明确要求时做。

方向的要点（细节看 DESIGN.md）：
1. 结论在写出时绑定证据，数字从 run 输出里取，引用带逐字原话（ADR-014）。
2. 可信度五级：断言 → 有据 → 已审查 → 已复现 → 已独立重复（ADR-015）。
3. Web 工作台提前到 v0.3，以研究记录为中心（ADR-016，取代 012）。
4. kernel 用 bubblewrap 隔离，网络默认关闭（ADR-017）。原先留给作者选的方案已由 owner 选定为方案 1。
5. harness 的机制可单独开关，用评测提升说话（ADR-018）。

## v0.2 Grounded 的已完成记录

### 1. kernel 隔离（ADR-017）✅ 2026-11-18
- [x] `runtime/sandbox.py`：bwrap 参数、可用性探测、分级决定；`JupyterKernel(sandbox=...)`；`Kernel.isolation` 记入 `run_started`。
- [x] internal / confidential 在隔离不可用时拒绝；public 带警告运行；`[kernel] network = true` 仅 public。
- [x] 测试：单元 12 个（参数、策略、workspace），集成 8 个（联网失败、项目外文件不可见、数据目录只读、`.bioquorum/` 不可见、配置只读、无密钥、打断和超时后 kernel 存活、pandas 读写）。
- [x] `SECURITY.md`；worker 系统提示说明沙箱。
- [x] **数据下载工具** `download_file`（2026-11-18，`tools/builtin/download.py`）：存到 `downloads/` 和 CAS，URL / hash / 大小进事件；只限 public 项目；拒绝私有和本机地址；流式大小上限。单元测试 22 个；实测从 NCBI GEO 下了 9.4 MB 的文件，沙箱 kernel 能读。
  - 局限：internal / confidential 项目现在取不了公开数据。等真有需要时再定（候选：按主机白名单放开，URL 本身算不算泄露要想清楚），需要新 ADR。
- [x] 用真实模型跑一次 `bq run`，确认模型看到"不能联网"后会改用工具（2026-11-18，见第 2 步的冒烟测试）。

### 2. 证据绑定（ADR-014）✅ 2026-11-18（代码和离线测试）
- [x] 设计笔记写进 ADR-014（语法、别名、格式化、结论何时算 `grounded`）。
- [x] `runtime/values.py`：kernel 启动 / 重启时注入 `bq`；`bq.value()` 发自定义 MIME；`tools/runs.py` 写进 `run_finished.values`（失败的 run 不记）。
- [x] `review/binding.py`：解析 `{{name}}`、`{{name:spec}}`、`[@s3 "原话"]`、`[@PMID:123 "原话"]`；渲染；`ground_claim()`。`Evidence` 加 `values`、`sources`；来源别名 `s1, s2…` 由事件顺序决定，agent 上下文里同一规则加 `[source sN]` 行。
- [x] 状态机加 `grounded`；**级别不能跳**：没绑定的结论通过审查仍是 `unverified`（v0.1 是 `reviewed`）。绑定失败 = `binding` error finding → `disputed`。
- [x] agent 循环退回失败的绑定，最多 2 轮；开关 `[agent] binding`、`[agent] binding_retries`。
- [x] CLI：回答里填入记录的值，失败的绑定标出来，下面列出每个绑定指向哪个 run / 工具调用。
- [x] 测试：单元 328 个（原 244），集成 16 个（含沙箱里的 `bq.value`）；审查基线不变（21/24，0 误报）。
- [x] **真实模型冒烟测试**（2026-11-18，GPT-6.1-sol 当 worker，DeepSeek 审查，项目 `~/bq-demo`，三道题，几美分）：
  - 二甲双胍文献题：模型自己用了 11 处原话绑定，全部逐字核对通过，退回 0 轮；18 条结论 5 条 `reviewed`，其余是没带原话的句子和文献条目，显示为"仅为断言"。
  - Pima 糖尿病数据题：模型用 `download_file` 取数据（没在 kernel 里联网，ADR-017 那一项也算验证了），用 `bq.value` 声明了 5 个量，回答里的数字全部是填进去的。修正后三条数值结论都是 `reviewed`。
  - TYK2 留出题（规则没见过）：引用全部核对通过，包括 Open Targets 的 JSON；暴露了带直引号的原话解析不了的问题。
  - 由此修了 4 处（见 CHANGELOG）。这些是冒烟测试，**不是遵从率评测**：题少，而且前两题参与了修规则，算"看过的"。
  - 观察到但没处理：模型爱把原话堆在段尾，抽结论时数字和原话被拆开（提示词已加一句，效果待评测）；kernel 环境里没有 scipy，模型白跑了两次 run（等 v0.2 的 pixi 环境，或在提示词里说明可用的包）；沙箱 kernel 启动时往终端打印一段 ipykernel 的 "NOTE: When using the `ipython kernel` entry point…"，应改为收进日志。
- 已知局限：原话的来源只能是本会话的工具结果，kernel 里读的本地文件（例如用户给的 PDF 转出的文本）不能当原话来源；需要时加一个读文件的工具路径，而不是放宽规则。`bq.value` 只收标量。投影表里还没有命名值（v0.3 界面要用时再加，届时先接 Alembic）。

### 3. 评测（提前到预算之前）
ADR-018 要求用配对结果决定默认值。初测后保留绑定提示，自动退回默认设为 0，保留显式开启。
- [x] `evals/binding_eval.py` 对比关绑定、只开绑定提示、同时开绑定提示和退回。在冻结的引用题、计算题上报告有据占比、rubric、费用、耗时和退回轮数，并从模型写出的留出结论重放数字错误注入。
- [x] DeepSeek V4 Pro、Qwen3 4B Instruct、Llama 3.2 3B 共 54 次真实运行和数字注入见 [绑定评测](docs/research/binding-eval.md)。Linux OrbStack 环境的 16 项集成测试全部通过。
- [x] 单独测试绑定失败反馈。V4 Pro 和 Flash 在留出集一轮分别修复 8/8 和 7/8，按人工核对计。原本有据的邻近结论也可能被撤回。本轮没有第二轮事件。暂保持退回默认 0，保留显式开启。详见 [修复对照](docs/research/binding-repair-eval.md)。
- [x] 完整 harness 与最短提示加 kernel 的真实研究数据对照。两套模型共 30 个组合，留出题新增有据结论，未观察到一致的准确率增益。退回在 10 个启用组合中触发 1 次，增加 4 次 worker 调用，默认继续关闭。DeepSeek Chat 的推理字段往返缺口已修复并通过回归。详见 [真实研究数据对照](docs/research/harness-research-eval.md)。

### 4. 循环按预算运行（ADR-018、019）✅
- [x] 独立配置 token、估算美元和时间预算。达到阈值即停止启动新工具，保留一次无工具收尾；审查独立计量。
- [x] 压缩只替换已结束的上下文前缀，证据仍从原始事件取得；CLI 流式模式显示即时生成进度，最终绑定完成后呈现研究正文。
- [x] TOML 可配置模型费率。CLI 区分估算费用、显式零价格和未知价格。
- [x] 本地 Qwen 的 12 个配对组合测完，按开发题与留出题分开报告。预算触发一次并停住后续工具，压缩增加十次摘要调用，流式进度都在完成前收到增量。三个开关均按需开启。 [运行控制对照](docs/research/v02-runtime-controls.md)

### 5. 场景与小债 ✅
- [x] B 使用真实 PBMC 3k，在 bubblewrap 内由 Python 计算，R Matrix 独立复算。过滤后为 2,638 个细胞和 13,656 个基因，数值和散点图回溯到输入哈希和运行。
- [x] C 使用真实 PubMed 摘要完成二甲双胍证据表，五条引用逐字绑定并通过公开标识符在线核验。演示用预定义模型回复，科学结论待作者判断。
- [x] MCP 传输断开后在下一次请求重连。Alembic 自动升级旧库并保留事件。在线文献标识符核验经工具权限和审计执行。
- [x] 验收产物在本地 `evals/results/v02-acceptance/`，开发与本地模型留出审查回归见 [评测记录](docs/research/v02-runtime-controls.md)。

## 需要作者做的事
1. ~~读 `docs/DESIGN.md`~~ 已读，已认可（2026-11-18）。
2. ~~commit / push~~ 已完成（2026-11-18）。
2a. ~~跑一次真实的 `bq run`~~ AI 已用作者的 key 跑了三道题（2026-11-18）。
2b. ~~commit 证据绑定的改动~~ 已完成（2026-11-18）。
3. v0.1 遗留的一项验收（可选，5 分钟）：在 Claude Code 里 `claude mcp add bioquorum -- …/bq mcp serve -C ~/bq-demo`，步骤在 `docs/demo-v0.1.md` 第 4 步。终端录屏那一项取消了。
4. API key 只在自己的终端里 export；Claude Code 要从那个终端启动才能读到。加依赖时自己运行安装命令。
5. 公开发布前确认 PyPI 名 `bioquorum` 和商标。
6. v0.2 新增 Alembic，依赖已写入锁文件。由作者运行 `uv sync --locked`，常规 `uv run` 也会同步环境。请在提交前读 B 或 C 的一份回答，完成科学验收。

## 怎么验证一切正常（接手第一件事）
```bash
uv sync --all-groups
uv run pytest && uv run lint-imports && uv run ruff check . && uv run pyright   # 单元测试，9 条分层契约
uv run pytest -m integration                                                    # 真实 Jupyter kernel，含沙箱（16 个）
uv run pytest -m live          # 需要 OPENAI_API_KEY / DEEPSEEK_API_KEY 和 biomcp（4 个，几美分）
uv run python evals/review_eval.py                                              # 审查检查：21/24，0 误报
```

## 回来后对 AI 说的话
> 继续 BioQuorum。先读 AGENTS.md 和 NEXT.md，再读 docs/DESIGN.md、ROADMAP.md、docs/ARCHITECTURE.md 和相关 ADR。从 NEXT.md 写的那一步（例如 0.7.0）开始，按 ROADMAP 第 0 节的完成标准做完一步再做下一步，做完更新 ROADMAP 的勾选和 NEXT.md。

## 文档地图
| 文件 | 内容 |
|---|---|
| `AGENTS.md`（`CLAUDE.md` 引用它） | AI 协作约定、环境约束、踩过的坑、评测纪律 |
| `docs/DESIGN.md` | 设计理念：为什么这样做（四份文档冲突时它优先） |
| `docs/ARCHITECTURE.md` v0.4 | 最高技术约束（承重 / 可调） |
| [架构决策 001–032](docs/adr/) | 011 由 013 取代，012 由 016 取代。工具来源进入计算内核见 [025](docs/adr/025-research-record-handoffs.md)，私有部署边界见 [026](docs/adr/026-private-runtime.md)，审查与重放的一致性见 [027](docs/adr/027-review-and-replay-integrity.md)。 |
| [v0.6 部署验收](docs/release/v0.6-acceptance.md) | 对照 ARCHITECTURE §8 阅读，可查各项的本机实测结果及部署环境待验收项。 |
| `ROADMAP.md` / `docs/PRODUCT.md` v0.5 | v0.7 到 v1.0 的逐步任务 / 产品定义 |
| `CHANGELOG.md` | 版本变更 |
| `docs/research/reviewer-experiment.md` | 跨家族审查假设的对照实验（结论：不成立） |
| `docs/research/review-checks.md` | 确定性审查检查的规则与实测 |
| `docs/demo-v0.1.md` / `docs/demo-v0.3.md` | CLI 和工作台演示与验收步骤 |
| `docs/archive/` | 文档旧版本 |

## 2026-11-20 README 文案更正

已按作者原意更正 README 开头对其抗体药物研发参与经历的中英文表述。
