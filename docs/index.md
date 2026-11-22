# BioQuorum 使用手册

BioQuorum 把一次研究保存为可继续使用的项目。第一次体验可以直接打开随包的离线工作台，核对一个数字，再沿引用回到原文。

## 从空目录开始

在仓库目录执行 `uv run bioquorum`。打开 **Try the metformin example**，提交计划并批准，点回答中的 **3** 查看代码和输入哈希，再点文献编号查看高亮引句。示例使用录制回答，离线可运行。要研究自己的问题，用 `uv run bq project init -C my-study --classification public` 建立项目，在项目的 bioquorum.toml 配置模型，再用 `uv run bq workbench -C my-study` 打开。[发行与安装](release/install.md) · [证据综述操作](demo-v0.3.md)

## 怎样读一份记录

| 研究者的问题 | 该看哪里 |
|---|---|
| 这个数字怎么算出来？ | 点命名值查看生成它的 run、代码、输入和输出。引文绑定展示保存的原文。[证据绑定](demo-v0.2.md) |
| 这条结论核对到了哪一步？ | 从断言、有据、已审查，到相同代码重跑和独立重复。对照计算需满足记录中的环境、隔离与比对要求。[可信度与重跑](demo-v0.4.md) |
| 是否漏查了一类证据？ | 从覆盖台账查看实际查询、空结果及范围。覆盖度与单条结论的可信度各自记录。[覆盖台账](demo-v0.8.md) |
| 更正一个样本标签会改变什么？ | 在研究线上登记更正、察看受影响的结果、重算并比较新旧结论。[研究线与交接](demo-v0.10.md) |

## 配置和扩展

- [模型、角色、配置字段和格式兼容性](compatibility.md)
- [Python 与 R 的项目环境、pixi 锁和跨语言文件](demo-v0.9.md)
- [私有部署、内核隔离、出站代理和 Ollama](demo-v0.6.md) · [安全边界](../SECURITY.md)
- [作为 MCP Server 提供工具](demo-v0.1.md)
- [编写 Skill、Connector、Review check 和查看器](compatibility.md#编写扩展) · [查看器操作](demo-v0.11.md)

## 评测与发布

[评测方法与已测结果](research/v1-evaluation.md) · [四个离线项目与演示](demo-v1.0.md) · [v1.0 验收状态](release/v1.0-acceptance.md) · [设计理念](DESIGN.md) · [架构](ARCHITECTURE.md)
