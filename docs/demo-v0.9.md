# v0.9 双语言分析演示

本机的 `examples/v09` 已完成公开项目初始化并备有 GEO 数据。新检出时，从仓库根目录初始化一次并获取原始输入。随示例提供的 `data/geo-inputs.json` 记录来源、字节数和 SHA-256，场景运行前会核对哈希。

```sh
uv run bq project init -C examples/v09 --classification public
for name in GSE96583_RAW.tar GSE96583_batch2.genes.tsv.gz GSE96583_batch2.total.tsne.df.tsv.gz; do
  curl -fL --retry 2 -o "examples/v09/data/$name" "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE96nnn/GSE96583/suppl/$name"
done
```

## 准备运行环境

由作者安装 pixi 后，使用示例附带的项目锁准备环境。

```sh
pixi install --locked --manifest-path examples/v09/pixi.toml
```

示例配置启用了项目 pixi 环境和 macOS 沙箱。Linux 使用 bubblewrap。项目的 R 与 Python 均通过 Jupyter 的私有 IPC 通道启动。

## 运行并检查证据

```sh
uv run --no-sync pytest -m integration tests/integration/test_r_kernel.py -rs
uv run --no-sync python evals/v09_scenario_b.py --project examples/v09
uv run --no-sync bq workbench -C examples/v09
```

场景脚本用固定的工具调用执行真实代码，不消耗模型额度。Python 进行条码匹配、细胞质控和供者汇总，交出整数计数和样本表；R 拟合配对 DESeq2 模型。脚本逐项检查绑定并确认两种语言得到相同的供者配对数和基因数。终端列出会话、结果和研究包路径。

在工作台打开“GSE96583 paired monocyte counts”，点开两个语言各自给出的数值。R 的证据面板显示本次 R 代码、生成输入的 Python 单元及文件哈希。“Project settings → Runtime and project”显示项目锁和包版本。会话中下载 notebook 会得到含 Python 和 R 两个文件的 ZIP。

把研究包复制到单独的、已配置 pixi 的 Linux 容器，解压并进入目录。使用与原记录相同的操作系统和架构做逐字节复现。下例先用系统 Python 3 检查完整性，再按锁准备环境并重跑。只有 pixi 的极简镜像可先用来自受信源的锁在独立目录准备 Python，将其绝对路径通过 `PYTHON` 指给校验脚本。

```sh
sh rerun.sh --verify-only
pixi install --locked --manifest-path env/pixi.toml
sh rerun.sh
```

核对每个 run 的 `outputs_matched` 和 `environment_matched`。跨平台运行可能产生不同的环境指纹。中间文件哈希、命名值、标准输出和图像都参与重跑核对。科学验收使用[场景记录](research/v09-r-kernel.md)和[开发评测题](../evals/questions/v09/B.yaml)。

## 开发题实跑

从仓库根目录运行下面的命令，会把第一道 v0.9 开发题交给示例项目配置的 OpenAI worker 和 reviewer。先完成上述完整场景以生成输入文件。第二道题使用相同入口，将索引改成 1。问题与科学评分标准保存在独立的 v0.9 题库。

```sh
question=$(uv run --no-sync python -c 'import yaml; from pathlib import Path; print(yaml.safe_load(Path("evals/questions/v09/B.yaml").read_text())[0]["question"])')
uv run --no-sync bq run -C examples/v09 --max-steps 10 "$question"
```
