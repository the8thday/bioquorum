# v0.12 工作台演示

## 离线复走后台研究

在仓库根目录，使用已配置好的开发环境、Playwright 和本地 Chrome：

```sh
node tests/e2e/background.cjs
```

这个演示创建临时项目，在工作台提问，随后关闭整个浏览器。两个子任务各读取一份含样本数的档案，主任务调用真实 Python 内核计算合计。脚本在计算结果落盘后强制终止服务，启动同一项目，重开浏览器并点击 **Resume research**。验收会检查只有一个研究轮次、两份通过引文检查的子任务报告、一次成功计算，以及绑定到该计算的结果 8。模型使用固定响应。要重复完整的一小时离开情形，可在命令前设置 `BQ_AWAY_SECONDS=3600`。

在自己的项目用 `bq workbench --project <目录>` 打开工作台。**Research tasks** 显示本项目各轮次、当前状态和子任务用量。**Inbox** 可定位权限、计划批准、未回答问题、待讨论的分歧、材料更正后的结论与研究决定复核及可继续的中断任务。已进入执行、结果却未写回的操作显示工具名和调用 ID，核对实际结果后才可继续。浏览器通知在工作台偏好中启用，仅在页面打开且已获得通知权限时生效。

## 启用并行与项目专家

在 **Project settings → Mechanisms** 中启用 **Parallel research tasks**，填写允许子任务使用的已登记工具名，选择并行上限并保存。项目配置示例：

```toml
[agent]
subtasks = true
subtask_tools = ["read_file"]
subtask_parallelism = 2
specialists = true
```

将以下内容保存为项目的 `.bioquorum/agents/archive-reader.md`。省略 `model` 时使用项目的研究模型。指定模型时必须使用已配置、符合项目数据分级的模型名。

```markdown
---
name: archive-reader
description: Check exact passages in the project archive.
tools: [read_file]
permissions: [read_fs]
---
Read the assigned record and return a verbatim passage with its source alias.
```

在父会话已有读取权限的项目放入两份文本材料。在问题中给出文件名，请研究模型将两次读取放在同一批子任务，分别报告原文和出处。子任务通过同一条委派工具调用启动。打开 **Research tasks** 查看每项的专家、状态、token 数与计价覆盖，点击最终回答中的来源查看支持该句的原文。

## 计划、模型和长记录

- 勾选 **Review a plan first**，填写问题，按 **Draft steps with planner**，然后修改或提交步骤。在 **Inbox** 打开该计划并批准后才开始研究。
- `bq models --project <目录>` 显示按数据分级过滤后的角色路由。**Project settings → Models and roles** 可为各角色保存首选模型和有序备用模型。
- 手动运行 `bq models eval --project <目录>` 会向每个允许的模型提交八道固定题。使用按量计费的 provider 时会产生费用。随后在同一设置页查看得分、失败数与并列建议。
- 长会话从最近 30 轮开始显示。**Show earlier research** 每次增加 30 轮。在任务列表中点早期问题可以直接定位到原轮次。审计页与 SSE 使用事件游标。

[工程验收](release/v0.12-acceptance.md) · [真实模型对照](research/v12-scale.md)
