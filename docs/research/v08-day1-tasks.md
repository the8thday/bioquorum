# 第一天对照 · 六张任务单

每题开一个新的通用 Agent 会话，选 GPT-6.1 Sol，只把该题英文代码块里的内容作为研究问题发给它。开始前把本地 stdio MCP 连接到该题。可执行文件用仓库下的 `.venv/bin/python`，第一个参数是仓库下的 `evals/comparison/day1.py`，第二个参数是该题编号，第三个参数是 `--serve`。前两个文件路径请填绝对路径。关闭其他外网搜索和本机文件工具，保留常规研究提示。B 类题的数据由工具提供。

例如，做第一题时，MCP 的三个参数依次为 `<仓库绝对路径>/evals/comparison/day1.py`、`q-A-29`、`--serve`。下一题只换编号。若会话无法选择同款模型，先登记原因，保留该题的待对照状态。

每题保存回答原文及其来源、实际模型、起止时间和可见用量。费用未知时填“未知”，订阅内用量单列。稍后按冻结的评分表做匿名配对。可以先从第一题开始。

本地收件目录为 `evals/results/v08/day1/general-agent/`，每题一份同名 Markdown 文件（例如 `q-A-29.md`）。将带引用的最终回答原样粘贴到对应文件，填好文件开头的运行信息。六题齐后再整理匿名评分材料。

## q-A-29

```text
Assess HMGCR inhibition as a strategy for secondary prevention.
```

## q-A-30

```text
Evaluate RANKL blockade in postmenopausal osteoporosis, including what happens after withdrawal.
```

## q-B-29

```text
Use cells.csv, a synthetic paired PBMC panel. G1-G3 are raw UMI counts for three genes; total_umi includes other genes. mito_fraction is a fraction from 0 to 1.
What can this dataset tell us about absolute numbers of circulating monocytes?
```

## q-B-30

```text
Use cells.csv, a synthetic paired PBMC panel. G1-G3 are raw UMI counts for three genes; total_umi includes other genes. mito_fraction is a fraction from 0 to 1.
Would treating each cell as an independent replicate change the uncertainty for mean G1?
```

## q-C-29

```text
Review Mediterranean diet interventions for cardiovascular prevention.
```

## q-C-30

```text
Does remote monitoring improve outcomes in chronic heart failure?
```
