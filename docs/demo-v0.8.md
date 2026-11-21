# 在工作台检查覆盖情况

新项目在 **Project settings → Research profile** 显示 **Recommended**，默认开启证据绑定。选择 **Minimal** 并保存可关闭绑定；手动调整研究开关后显示 **Custom**。

在项目设置的 Mechanisms 中按需启用 Evidence coverage、First-pass judgment 和 Evidence category skills，保存后开始一个新的研究问题。连接器可使用项目已有的 MCP。公开研究项目还可在项目配置中启用固定的公共检索来源：

```toml
[connectors]
public_sources = true
```

第一次发送公开查询时按当前项目权限决定是否允许。在回答下展开 **Evidence coverage**，核对每个子问题实际查过的来源、检索词、时间和返回数量。无返回记录、请求失败、条数未知及尚未查询分别显示。阅读末尾的缺口与可能改变判断的证据。导出的研究包内 `review/coverage.json` 以回答事件 ID 区分各轮台账。

首轮判断启用后，点 **Discuss disagreement** 或 **Discuss next step** 将原句加入下一轮草稿。若研究者需要补充开始时的歧义，点 **Answer this question**，输入答案并发送。研究者没有回复时，Agent 依照已声明的假设继续。

收费评测的科学口径与完成状态见[基线记录](research/v08-baseline.md)。离线查看逐场景汇总用 `uv run --no-sync python evals/v08_report.py`。
