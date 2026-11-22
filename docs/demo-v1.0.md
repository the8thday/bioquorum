# 四个可离线打开的研究项目

每个 ZIP 都是可继续工作的项目，包括本地事件库及其证据文件。把一个 ZIP 解压到独立空目录，在仓库中执行 `uv run --no-sync bq workbench -C <解压目录>`。打开已有记录无需模型、网络或真实内核。发起新的研究另需配置模型，重算需要相应运行环境。

| 项目 | 在工作台中核对 |
|---|---|
| [靶点评估 · TYK2](../examples/v1/projects/target.zip) | 打开 POETYK PSO-1 研究记录，点击 45.7 查看两组公开四舍五入 PASI 75 比例的未调整百分点差；点击 PMID 查看出处。长期比较安全性的提示保留为断言。数字与图来自本地 Python。这个单试验案例只演示靶点评估中的临床证据环节。[原论文](https://pubmed.ncbi.nlm.nih.gov/35820547/) |
| [组学分析 · GSE96583](../examples/v1/projects/omics.zip) | 点 8 或 5,140 核对供者配对、样本列和预过滤基因数。使用 v0.9 分析中有哈希的完整中间表。本例复核设计和输入，原始差异分析与 FDR 结果见[双语言记录](research/v09-r-kernel.md)。 |
| [证据综述 · 二甲双胍](../examples/v1/projects/review.zip) | 点摘录数量和高亮引用，对照一条缺少支持的断言。本项目由已有随包 cassette 和模拟内核生成，原始演示也可从默认工作台再次运行。[演示来源](demo-v0.3.md) |
| [研究线更正 · 样本标签](../examples/v1/projects/correction.zip) | 在研究线和重算会话查看两版合成标签、原始均值 3 AU 与更正后的 8 AU，以及替代结论的来源。两次运算都使用真实内核。[更正流程](demo-v0.10.md) |

## 工作台录像

[播放候选版 TYK2 工作台实录](media/tyk2-workbench.mp4)。录像使用候选 wheel 中的网页，打开保存的试验资料，依次查看计算和高亮引句、重跑本机计算、导出研究包。导出的同一 ZIP 在本机新目录通过完整性检查，两个 run 的输出和环境均匹配。本段录制的是预先准备的研究记录。完整七步演示还需在符合盲重复认证条件的环境演示同一问题的独立重复，并在第二台物理机器验证同一包。

## 从输入重新生成

```sh
uv run --no-sync python -m examples.v1.prepare target --project build/demo-target
uv run --no-sync python -m examples.v1.prepare omics --project build/demo-omics
uv run --no-sync python -m examples.v1.prepare review --project build/demo-review
uv run --no-sync python -m examples.v1.prepare correction --project build/demo-correction
```

命令需要目标目录为空。靶点和组学的文字使用固定案例说明，计算、证据绑定和确定性检查由程序实际执行。研究线示例从原材料登记到更正与重算，无需模型调用。随项目分发的 ZIP 以独立目录解包、重建投影并核对数值，生成过程另已在本机运行。

### 把录像中的同一个包带到另一台机器

下载[录像实际导出的研究包](../examples/v1/tyk2-research-bundle.zip)。ZIP 的 SHA-256 为 `3b5e49d7cf53b049585c77b085b33cb6a3a451a210451cba722f3ddd4701ec7d`。在目标机空目录解包，先执行 `sh rerun.sh --verify-only`，再执行 `sh rerun.sh`，保存输出、退出码、主机代号和平台。这个包记录的是本机录像中的两次计算，未附 pixi 锁。

如需生成供独立盲重复的锁定案例，在新的目标目录提前备好 `pixi.toml`、`pixi.lock` 及已配置好的 `.pixi`，再运行 `uv run --no-sync python -m examples.v1.prepare target --locked --project <目标目录>`。生成器只读取已有环境。符合认证条件的 Linux 工作台可以用该项目对具名值 `pasi_gap` 发起独立复核，并导出带环境锁的包。两次操作各自保留实际结果。
