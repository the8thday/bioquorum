# v0.10 工作台演示：更正样本标签

这个合成例子有两个样本。周四的原始分析报告对照组均值为 **3 AU**，周五交付的更正表交换了两个样本的组别。示例已保留周四的材料、代码、值和绑定结论。可在仓库根目录打开本机准备好的项目。

```sh
uv run --no-sync bq workbench -C evals/results/v10/demo
```

新检出或想从头演练时，用下列命令创建独立项目。脚本要求目标目录为空或尚未创建，需要可用的隔离 Python 内核。建档和原单元重算均无需模型。

```sh
uv run --no-sync python evals/v10_scenario_d.py --project evals/results/v10/demo-fresh
uv run --no-sync bq workbench -C evals/results/v10/demo-fresh
```

1. 在 **Research lines** 打开 *Control signal after label correction*。选择 **Record decision**，写下自己的处理决定，勾选周四的 3 AU 结论并保存。此时即可下载第一份研究线包，稍后在影响报告里查看它。
2. 在 **Materials** 找到 `sample-labels.csv` 的第 1 版。点击 **Replace version**，上传演示项目内的 `deliveries/corrected/sample-labels.csv`。填写来源和替换原因，写明交换了两行样本的组别。
3. 查看旧版的 **View impact**，确认受影响的运行、测量、结论、决定和已导出的包，以及模型费用估计。点击 **Recompute with replacement**。新会话执行原代码，表内显示 **3 AU → 8 AU**。原会话的结论可跳转到修订结果。
4. 返回 **Research lines**。之前保存的决定仍显示 **Needs review**，可打开它引用的旧证据。使用 **Revise decision** 更新说明并选择 8 AU 的新结论，然后查看 **Previous decision versions**。可下载 JSON 接手摘要和包含成员会话包、材料版本的 ZIP。
5. 如需体验失败恢复，用 `deliveries/renamed-column/sample-labels.csv` 替换第 2 版。原代码依赖的 `group` 列在新文件中改名为 `arm`。打开影响报告重算可看到失败记录；**Discuss a repair** 将问题放入新会话输入框。点击发送时才使用项目配置的模型。

所有 CSV 均来自仓库的[合成演示材料](../evals/assets/v10-scenario-d/)。正式第二天对照使用另一道第一天研究题及独立接手者，参加者应按[对照协议](research/v10-day2-comparison.md)领取对应材料。
