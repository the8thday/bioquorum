# v0.5 研究流程使用说明

在已有 BioQuorum 项目的 `bioquorum.toml` 里，选择启用其中一项或两项：

```toml
[agent]
skills = true
project_notes = true
```

### 放入项目 Skill

在项目目录建立 `.bioquorum/skills/calibration/SKILL.md`。例如：

```markdown
---
name: calibration
description: Consult this project’s instrument calibration workflow.
---

Before comparing runs, look up the instrument's calibration date and the
acceptance band in the approved study plan. Report a missing record explicitly.
```

`uv run bq skills -C /path/to/project` 列出名称和描述。使用项目的正常 `bq run`
或工作台提问时，模型只会先看到这些目录信息。它需要这项方法才调用 `open_skill`，
正文和文件哈希会记入本次工具事件。在同一次提问中打开正文后，还可以单独读取
`references/` 和 `assets/` 下的文本文件。

此入口支持 [Agent Skills 基础格式](https://agentskills.io/specification)，仅发现
这个项目目录的资料。Skill 提供方法参考，工具权限和数据分级仍各自判定。脚本不会
自动执行。把目录中的 Skill 替换为新版本后，下一次提问会使用新目录。

### 写入跨会话项目笔记

1. 运行 `uv run bq workbench -C /path/to/project`，打开或新建一个会话。
2. 点 **Project note**，写入项目惯例、已经确认的方法选择或不做的范围，然后保存。
3. 换一个会话，笔记仍在。提问时可以明确让模型查项目笔记，模型用
   `read_project_note` 读取经作者保存的版本。
4. 模型可提交草稿供你修改和保存。草稿只在它所在的会话可见。两个窗口编辑同一版本时，
   先保存的版本保留，后保存的窗口会收到提示并可重新加载。

也可以运行 `uv run bq note SESSION -C /path/to/project` 读取笔记和版本。
要保存，从文件中读入编辑后的内容，并传 `--revision` 为读取时的版本；第一次保存
省略版本。两项开关独立，默认都关。关闭后禁用后续加载和草稿，历史研究记录仍可回看。若要让模型使用空白历史，请新建会话。

Skill 和笔记适用于项目工作背景。研究结论中的数字、引文仍须从计算和来源绑定证据。
请把凭据留在配置引用的环境变量里，不要写入 Skill、笔记或问答。

[本地模型消融及适用边界](research/v05-project-context.md)。

### 用独立上下文检索文献

在现有的 `[agent]` 配置中加上下面两项，把示例名称替换为项目已配置的只读文献搜索或原文读取工具。MCP 工具使用 `服务器名__工具名` 格式，服务器配置见 [连接 MCP](demo-v0.1.md)。

```toml
[agent]
literature_subtasks = true
literature_tools = ["archive__search", "archive__get"]
```

在工作台用直接提问模式输入 `/literature <问题>`，或在终端运行：

```bash
uv run bq run -C /path/to/project '/literature 查找支持该结论的原始研究，并给出原文引句。'
```

子任务只收到这一条问题，使用项目设置的 worker 模型、选定的工具和会话已有的权限。主会话收到简短摘要与通过逐字核对的原文摘录。在工作台的回答中打开有效引用，可查看对应的原始工具结果。摘要只能作线索。工具不可用、权限不足或找不到可核对的摘录时，活动中会记录结果。

默认关闭。开启后，普通提问仍可以直接检索，由模型自行判断是否使用委派。需要确定触发时使用 `/literature`。子任务计入主轮探索预算，每轮最多一次，最多两次检索工具调用。

[文献子任务本地模型对照](research/v05-literature-subtasks.md)。

### 用检索结果做 Python 计算

在 `[agent]` 中启用 `source_to_kernel = true`。让模型对已检索的来源别名（例如 `s1`）调用 `stage_tool_source`，再把返回的相对路径放进 `python_execute.input_files`。源文本是 UTF-8 数据，格式取决于原工具。BioQuorum 会核对暂存记录和文件哈希，随后把这份输入带进研究包。该开关默认关闭。

### 把运行图片交给模型

```toml
[agent]
vision = true

[models."local:your-vision-model"]
vision = true
```

把 `local:your-vision-model` 换成已在 `[roles]` 配置的图像模型。支持接收本轮最近两张 PNG 或 JPEG，每张至多 2 MiB。图片发送沿用项目数据分级。关闭任一开关时，使用原有的文字图片引用。

### 导出与复跑

工作台的 **Export** 或 `uv run bq export SESSION -C /path/to/project` 会在 ZIP 中生成 `Methods.md`。文献档案可用 `sh rerun.sh --verify-only` 校验。含计算的公开档案也可用 `sh rerun.sh` 重跑，对于没有环境锁的示例，设置 `PYTHON` 指向已能运行项目分析的解释器。

场景 A 用 IL23R／IBD 验收公开来源、语义审查和纯证据导出。场景 B 用 GSE96583 验收实测计数、分析图、供者配对和 QC 阈值复跑。 [验收记录与结果](research/v05-scenarios.md)
