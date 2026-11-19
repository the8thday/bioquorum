# BioQuorum v0.2 CLI

安装或更新项目依赖后，在项目的 `bioquorum.toml` 添加需要的开关。旧项目的 SQLite 库会在打开时通过 Alembic 升级。模型和隔离配置的完整示例见 [v0.1 指南](demo-v0.1.md)。

```toml
[agent]
budget_tokens = 120000
budget_seconds = 180
# budget_usd = 0.10
# compact_at_bytes = 40000
# stream = true
show_cost = true

[models."ollama:qwen3:4b-instruct"]
input_usd_per_million = 0
output_usd_per_million = 0
```

每个预算项独立生效。未配置预算时，探索默认最多 12 步，同时也可以显式传入 `--max-steps`。达到预算后，系统停止启动新工具，保留一次无工具的回答。已发出的单次调用、最后的回答和后续审查都会增加总用量。美元预算要求该阶段每条允许的模型路由都有费率，费用按供应商实报 token 和配置费率估算。零价格路由需同时配置 token、时间或步数限制。

```bash
uv run bq run -C /path/to/project --stream "Summarize the evidence in this project"
uv run bq review -C /path/to/project --online-citations
```

`--stream` 在生成过程中更新进度，完整正文解析绑定后显示。`--online-citations` 在 public 项目里核验已从本会话取得的 PMID 和 DOI 存在性。来源是否支持结论由独立的引用支持检查判断。网络故障会留下待核验发现。用户可分别开启压缩和流式进度。压缩只影响模型收到的历史，原始事件仍可重放。

Python 工具可以通过 `input_files` 声明已有读取权限的项目文件，或本会话经 `download_file` 取得的文件。运行记录和 run key 会包含输入哈希。声明代表传入的文件清单。

## 验收演示

下面的脚本使用预定义模型回复，运行真实的源码、Jupyter、数据库和公开注册表。运行 B 需要 Linux 与可用的 bubblewrap。两项都不需要付费模型。

```bash
uv run python -m evals.v02_demo B evals/results/demo-b
uv run python -m evals.v02_demo C evals/results/demo-c
```

如果本机 DNS 代理把公开域名映射成保留地址，下载工具会按策略拒绝。可以先从官方链接取得原始数据，再给演示脚本传入 `--input`。默认演示目录必须是一个新项目目录。每次运行输出 `answer.txt`、`result.json` 和可重放的 SQLite 事件日志。B 另有 `pbmc3k-qc.svg`。

### B · PBMC 3k 质量控制

在 10x Genomics 公开的 PBMC 矩阵上测量每个细胞的检出基因和线粒体 UMI 比例。保留检出基因 200–2,500、线粒体 UMI 低于 5% 的细胞，再保留在至少 3 个留存细胞中检出的基因。矩阵从 2,700 个细胞、32,738 个基因变为 2,638 个细胞、13,656 个基因。Linux bubblewrap 中的 Python 结果和独立 R Matrix 复算一致，CLI 的四条数值结论都通过审查。该演示使用标准库读取矩阵和绘制 QC 散点。 [10x 数据说明](https://www.10xgenomics.com/datasets/3-k-pbm-cs-from-a-healthy-donor-1-standard-1-0-0) · [原始矩阵](https://cf.10xgenomics.com/samples/cell-exp/1.1.0/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz)

### C · 二甲双胍机制证据表

证据表区分人体代谢研究、人原代肝细胞、小鼠与大鼠实验，以及针对肠道途径的延迟释放随机研究。它保留了肝糖异生、AMPK 与能量状态、AMPK 或复合物 I 非依赖效应和肠激素的不同证据。五条主张均通过逐字绑定及在线标识符核验，关于各种机制在患者中的相对贡献保留为 `unverified`。演示中的审查回复是预定义的，科学结论由作者验收。 [人体糖异生](https://pubmed.ncbi.nlm.nih.gov/11118008/) · [能量状态与 AMPK](https://pubmed.ncbi.nlm.nih.gov/21947382/) · [AMPK 非依赖](https://doi.org/10.1172/JCI40671) · [复合物 I 非依赖](https://pubmed.ncbi.nlm.nih.gov/30038219/) · [肠激素](https://pubmed.ncbi.nlm.nih.gov/27216492/)

公开标识符使用 [PubMed E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/) 和 [DOI Foundation API](https://www.doi.org/the-identifier/resources/factsheets/doi-resolution-documentation)。
