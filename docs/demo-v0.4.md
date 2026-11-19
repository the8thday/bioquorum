# v0.4 复现、独立重复和研究包

## 用真实 Python 走一遍重跑

在 BioQuorum 项目目录执行：

```sh
uv sync --locked
uv run python scripts/v04_verification_demo.py /tmp/bq-v04-example
uv run bq workbench -C /tmp/bq-v04-example
```

这段演示用合成表格演示计算流程，没有实际患者数据，不调用模型。一个 Jupyter
单元读取数据，另一个单元计算并声明总数。工作台打开 **Verify a cell-count
calculation** 后，点 **68** 查看代码、早期单元、输出和输入哈希。点
**Rerun calculation**，再打开结论下面的 **Rerun · matched** 查看左右比对。

重跑使用同一 kernel 的已成功记录单元重建状态。若中间有失败的运行、输入历史版本
冲突、环境不明，或找不到保存的输入，就报告未能判定。只有锁文件摘要一致、实际
kernel 环境指纹一致、无网络的 Linux 沙箱记录一致时，匹配才可升级已审查结论。
本机无锁或无沙箱时仍会显示实测的比对结果。

## 在 Linux 预配锁定环境

使用 [examples/v04/pixi.toml](../examples/v04/pixi.toml) 在一个新的公开项目中
生成并保留锁文件。应由环境管理员安装 pixi、bubblewrap 及锁定依赖。当前候选版
启动运行时只读取已有环境，不自动安装。

```sh
mkdir -p ~/bq-v04-linux
cp examples/v04/pixi.toml ~/bq-v04-linux/pixi.toml
pixi lock --manifest-path ~/bq-v04-linux/pixi.toml
pixi install --locked --manifest-path ~/bq-v04-linux/pixi.toml
printf '[kernel]\npixi = true\n' > ~/bq-v04-linux/bioquorum.toml
uv run python scripts/v04_verification_demo.py ~/bq-v04-linux --locked
uv run bq workbench -C ~/bq-v04-linux
```

此处的 `pixi install --locked` 执行的是锁文件所指定的环境。仅复制锁并不会安装环境。
参见 [Pixi 锁文件说明](https://pixi.prefix.dev/v0.46.0/workspace/lockfile/)。

独立重复只允许从「已复现」或「已独立重复」出发。工作台先显示根据配置费率计算的
估算，再请研究者填写中性的问题、结果定义和预登记容差。不要把原答案、预期值或原代码
写进这些字段。模型只收到新问题、单位、定义和只读输入。结果按名称、单位和容差比对，
缺失或未能完成的量显示为未能判定。预算按已观测的使用量在调用间检查，实际结算可有差异。

如需观察 pandas、AnnData 和 Matplotlib 文件读写，可设 `[kernel] lineage = true`。
这些是内核报告的事后观察，复现的证据依然来自 `python_execute.input_files` 声明的
输入快照。

## 导出与交接

点 **Export research bundle** 后下载 ZIP。解压后先检查内容，再运行：

```sh
sh rerun.sh --verify-only
sh rerun.sh
```

锁定的公开研究包通过 pixi 重建环境。未锁定的公开包可指定含 `ipykernel` 和
`jupyter_client` 的 Python（`PYTHON=/path/to/python sh rerun.sh`）。独立脚本
会比对命名值、文本和图的哈希，同时单独报告环境是否相同。不需要 BioQuorum 或
API key。此脚本只执行公开研究包；敏感项目应在已隔离的 BioQuorum 中运行。

`[review] method_sanity = true` 开启可选的方法审查。该步骤由配置的 reviewer
运行，提示会把代码和证据标为不可信内容。它能指出潜在方法问题，科学判断仍需研究者验收。

另见：[场景 C 离线文献工作台](demo-v0.3.md) · [公开基准与审查对照](research/v04-verification.md)。
