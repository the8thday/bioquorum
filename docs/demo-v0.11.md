# 在工作台查看本地文件并引用选区

## 打开曲妥珠单抗–HER2 示例

从 v0.11 工作区根目录打开已准备好的研究线。项目在本机的 `evals/results/v11/1n8z`。

```sh
uv run --no-sync bq workbench -C evals/results/v11/1n8z
```

新检出可在空目录重建同一示例，再用第二条命令打开。需要本机已有可用的隔离 Python 内核。准备脚本读取随仓库分发的公开数据，整个示例无需模型调用。

```sh
uv run --no-sync python examples/v11/prepare.py --project evals/results/v11/1n8z-fresh
uv run --no-sync bq workbench -C evals/results/v11/1n8z-fresh
```

1. 打开会话 **1N8Z interface and primary evidence**。点击数值 **17** 查看计算证据，点击 **[PMID:12610629]** 查看关于 HER2 近膜区域的原文。
2. 打开 **interface.fasta**，点选 **Interface · A ASN 30 (author)** 并引用。在 **1N8Z.cif** 的 Residue 列表中选择 **C · ASP 596**，点 **Quote selection**。两个选区会同时出现在问题框旁。打开 **contacts.tsv** 可以核对该残基对的最短距离。
3. 打开 **Research lines**，进入 **Trastuzumab Fab binding to HER2**，看两条结论和三项材料，点击 **Download handoff summary** 下载交接记录。[科学验收记录](research/v11-scenarios.md)

## 使用自己的公开文件

从本开发分支启动工作台。已有项目可直接使用第二条命令。以下示范项目用于公开文件。

```sh
uv run --no-sync bq project init -C ~/bq-viewer-demo --classification public
uv run --no-sync bq workbench -C ~/bq-viewer-demo
```

1. 打开终端给出的本地地址，进入一个研究会话。在 Project settings 中开启 **Follow-up references**。
2. 在 Artifacts 上传一个自己的公开 PDB 或 mmCIF 文件。打开文件，拖动旋转、滚轮缩放。点一个残基，或在 Residue 列表选择它。切换 Selection 可以选择整条链。点击 **Quote selection**，确认输入框旁出现文件名、链或残基号。
3. 只浏览、旋转和选择无需模型。要测试跨轮回放，在已配置模型的项目里提交带选区的追问，例如“我选了哪个链和残基？这里采用什么编号？”。刷新后点击该轮的 **Reopen structure selection**，可回到同一文件版本和位置。外部模型按项目已有计费设置运行。
4. 上传 FASTA，指定 Start 和 End 并选择 **Quote range**。若分析同时写出 `name.faa` 和 `name.faa.annotations.tsv`，注释区会标明来源运行和哈希。TSV 首行是 `record\tstart\tend\tlabel`（制表符分隔），记录和残基均从 1 开始，终点计入选区。
5. 打开 CSV 或 TSV，排序或筛选后点击单元格。引用显示原始数据行号。打开 Markdown 报告的 **Select report lines** 可以预览并引用原文行段。

## 从图中的样本重算

出图的同一单元可以记录数据与图面的位置。例如，若 `counts.svg` 中样本 a 和 b 的中心分别位于图面宽高的 (0.30, 0.35) 和 (0.70, 0.70)，Python 调用如下。坐标原点是图像左上角，作图代码负责传入该图的真实坐标。

```python
bq.figure_points("counts.svg", ["a", "b"], [0.30, 0.70], [0.35, 0.70])
```

R 对应调用为 `bq_figure_points("counts.svg", c("a", "b"), c(.30, .70), c(.35, .70))`。在工作台打开图，拖框圈选样本，点击 **Quote selected samples**，然后提交排除该样本的重算问题。该轮只产生一张新图时，会自动和原图并排；产生多张时，可以用 **Compare with** 选择。

已安装仓库浏览器测试依赖的开发环境可以离线复走这条流程。测试采用固定响应的工作线程和真实 Python 内核，也会读取 PDB、mmCIF、带注释的 FASTA、SDF 和 BED。

```sh
BQ_TEST_VIEWERS=1 npm test --prefix tests/e2e
```

[工程验收](release/v0.11-acceptance.md) · [领域 Skills 开关对照](research/v11-domain-skills.md)
