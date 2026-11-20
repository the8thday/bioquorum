# Workbench 0.7 浏览器验收

## 打开工作台

**只看界面或检查一条已有证据**：在仓库目录运行 `uv run --no-sync bioquorum`。点击 **Try the metformin example → Submit plan → Approve and run**。这里使用录制好的结果，可以点开引用、数字、图和用量，无需 API key。右侧 Activity 顶部显示当前会话的状态；Session idle 表示此会话没有运行中的研究轮次。下方保留过去的工具请求及完成记录。终端中的服务会持续运行以便浏览器访问，关闭服务用 Ctrl+C。

**体验自选问题、文献开关和 CSV 分析**：使用随仓库提供的公开练习项目。当前开发机已初始化；新检出的仓库需执行一次：

```sh
uv run --no-sync bq project init -C examples/workbench-v07 --name "Workbench 0.7" --classification public
```

在已导出 `OPENAI_API_KEY` 的终端启动：

```sh
uv run --no-sync bioquorum -C examples/workbench-v07
```

工作台自动打开本地地址。这个项目的 worker、reviewer 均使用 OpenAI，会产生真实 API 用量。样本只有演示用的两行计数。公开练习项目的基因查询发往 Open Targets，文献检索发往 Anthropic 托管的 PubMed 连接器。可用「查人 IL11，给我基因记录和一篇有 PMID 的论文」验证。若启动的是旧版本，请重启练习项目以载入新的连接器配置。项目要求隔离 kernel；设置中的 Runtime and project 显示实际隔离方式。首次安装请按 README 准备环境。此处的 `--no-sync` 复用现有环境。

## 从界面新建项目

左栏当前项目名称下的 **New project** 创建独立项目。输入名称、选择数据分级，点 **Create project**。项目保存在 `~/.bioquorum/projects/`，用左栏的项目名称下拉框切换。**Sessions** 旁的加号在当前项目内建立会话。手机上先点左上角菜单。

## 管理 Skills 和 MCP

侧栏 **Home** 可以直接输入研究问题。点 **Skills** 或 **MCP connectors** 即可进入当前项目的扩展列表，也可从顶栏齿轮切换到相同页面。

- **Skills → Install skill** 接受单个 `SKILL.md`、`.skill` 或 ZIP 包。点 **Install and enable** 后，下一轮能看到名称与描述，按需打开正文以及 references、assets 下的文本。脚本不会被执行。
- **MCP connectors → Add connector** 注册 HTTPS 服务端地址，或者本机已安装的 stdio 程序。HTTPS 入口适用于公开项目数据，本地程序使用离线沙箱。打开一个会话后可点 **Test connection**，查看可发现的工具数。
- 可停用和重新启用项目扩展；界面安装的扩展还可移除。已有 TOML 配置或手工 Skill 保存在原位置。离线示例提供创建真实项目的入口。

## 走一遍

1. 左栏点 **New session**（加号），输入会话名。在输入框旁选 `openai:gpt-6.1-sol`。问「用 Python 对 2 和 3 求和，用 bq.value 声明结果，再在答案中绑定它」。下一轮问「说一下刚才用了哪两个输入」。
2. 点回答里可点击的 **5**，查看代码，再点 **Quote value in question**。问「你刚才选中的这个数是怎么算的？」。引用可以在发送前移除。点右栏 **Evidence** 可回到原始计算。
3. 在右栏 **Usage** 切换本轮、会话和项目。当前示例未配置价格，会显示价格未知。点 **Context → Compact now**，看已保存的摘要，再点旧数字确认原代码仍然可读。至少需要两条可压缩的 assistant 消息。
4. 左栏 **Project settings → Mechanisms** 勾选 **Literature subtasks** 并保存。本练习项目已指定允许的工具。这里验收开关生效；执行真实文献调查会另外调用模型或检索服务。
5. 点击对话输入框内的回形针 **Attach a file**，在弹窗内选择 [counts.csv](../examples/workbench-v07/counts.csv)，确认分级并点击 **Attach file**。文件名会出现在输入框内。问「用 Python 读取附件，把 count 列求和。在 input_files 声明该文件，用 bq.value 声明总数并在答案中绑定」。预期是 **7**。点 7 查看运行代码和输入哈希。
6. 在右栏 **Artifacts → Preview table** 看两行原始数据，下载 kernel notebook。点会话标题旁 **⋯ → Export research bundle** 下载 ZIP。可刷新页面，再次打开原会话的文件和证据。

## 阅读、引用与设置

- **Assertion only** 表示该段陈述尚未关联可核实的证据。审查完成后，回答另列已评估的陈述数。展开 **Review by claim** 查看提取出的陈述以及各自的状态。**Run review** 可再次审查已保存的回答。
- 在回答中选中原句，浮动的 **Quote selection** 紧贴选区。点它，在原句旁写追问，再点 **Add to message**。主输入框会把原句和追问放在一起，同时保留已写的草稿。仅有这条追问时也能发送。
- 研究执行时，问题和输入框显示旋转指示；需要授权时显示 **Awaiting permission**。系统启用减少动态效果时保留静态指示。
- **Usage** 按所选范围显示输入与输出 token 占比。缓存仍列在明细。**Context** 显示当前上下文中的各角色消息条数；配置了模型窗口时，另显示估算占用进度。
- 输入框旁 **Chat settings** 可调整当前项目后续问题使用的引用、笔记、计划和文献子任务。模型、连接器、权限和预算仍可在侧栏 **Project settings** 查看。回放连接器标注 **Recorded responses only**。公开练习项目默认使用上述实时连接器。
- 顶栏齿轮 **Workbench settings** 设置各项目共用的对话字号、打开会话时的默认面板。配置保存在本机用户目录，更换服务端口后仍然生效。

## 并排查看结果

桌面端拖动左侧项目栏或右侧阅读区与正文之间的分隔线。用 Tab 聚焦分隔线后，方向键微调，Shift 加方向键大幅调整，Home 和 End 跳到最窄或最宽。顶栏左侧按钮收起或展开导航，右侧 Inspector 按钮收起或恢复阅读区。

在对话中点图、表格的打开按钮、或回答下方的结果文件名。右侧显示该版本的图、分页 CSV/TSV、回答表格或 Markdown/文本报告。报告里可以点开同一次运行保存的图片。打开多个结果后用文件标签切换，点 × 或在阅读区按 Esc 关闭当前结果。对话和草稿可继续使用。手机上结果优先占满可用宽度，可关闭阅读区回到对话。

新 Python kernel 会记录当前单元在项目内写出的常用图、表和报告文件。此前未记录的本地文件须重新生成或上传。工作台也能直接打开已有记录中的结果。参考交互见 [ADR-037](adr/037-result-reader.md)。

## 布局验收

桌面按项目与会话、研究记录、右侧面板三栏排列；顶部按钮收起或展开右栏。窄屏保留记录优先，通过左上菜单打开项目和会话，右上 **Inspector** 打开研究面板。用 Tab 进入设置，Enter 打开、Esc 关闭，应回到触发按钮。作者需实际确认这套布局、操作距离和阅读宽度是否合适。
