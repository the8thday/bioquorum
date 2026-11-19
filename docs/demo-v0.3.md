# v0.3 工作台验收

## 打开

当前工作区开发启动：

```sh
uv sync --locked
uv run bioquorum
```

直接用候选 wheel：

```sh
uvx --from ./dist/bioquorum-0.3.0-py3-none-any.whl bioquorum
```

也可以用 `pip install ./dist/bioquorum-0.3.0-py3-none-any.whl`，然后运行
`bioquorum`。PyPI 发布后才可以省掉本地路径，直接用 `uvx bioquorum` 或
`pip install bioquorum`。安装完成后不需要 Node。命令在已有 BioQuorum 项目里
打开该项目，在普通目录打开离线示例。可从终端复制启动时显示的本机地址。

## 浏览器里的场景 C

1. 点 **Try the metformin example**，再点 **Submit plan**。先看到计划才开始执行。
2. 刷新后点 **Approve and run**。研究记录包含问题、已完成步骤、图和发现。
3. 点数字 **3**，核对计算代码、三条输出和输入文件的 SHA-256。这个数字是选定摘录数。
4. 点 **[PMID:11118008]**，核对原话高亮，也可以打开完整 PubMed 记录。
5. 查看灰色的 **Assertion only**。对应句子用来演示证据边界，三条摘录无法判断
   哪个机制对人体降糖贡献最大。
6. 点 **Fork session**，输入另一名称。分叉在刷新后仍能点开原来的证据。

来源为 [11118008](https://pubmed.ncbi.nlm.nih.gov/11118008/)、
[21947382](https://pubmed.ncbi.nlm.nih.gov/21947382/)、
[27216492](https://pubmed.ncbi.nlm.nih.gov/27216492/) 的题录和短摘录。
示例回放预设模型回答和 FakeKernel 输出，不调模型、不联网、不需要密钥。
图与数量由同一份输入生成。回放中的审查标签展示了流程，其科学内容仍由作者验收。
在线查询的 v0.2 资料及其边界见 [v0.2 演示](demo-v0.2.md)。

## 用已有项目继续

```sh
bq workbench -C /path/to/project
```

使用现有项目和全局配置中的模型及 MCP。密钥从启动终端的环境变量读取。
真实项目可直接提问，也可逐行填写计划后提交。批准后模型开始执行。
每个计划步骤由模型显式报告完成，改动需经权限卡片批准，记录显示改动的理由。
系统不能独立判定某段科学代码有没有在语义上偏离计划。

需要改方向时点 **Stop research**，等记录显示已打断后输入新问题。已写入的活动
和证据保留。分叉固定历史位置，后续对话独立，项目文件仍共享。项目保持事件与
证据数据的原有分类和沙箱限制。

要走 DESIGN §4 的 TYK2 用例，请打开配置了文献和靶点 MCP 的公开项目，提问
“评估 TYK2 作为银屑病靶点”，走一次计划、查证据、看记录的相同路径。
在线模型或数据服务的可用性由该项目的配置决定。
