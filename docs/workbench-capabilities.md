# v0.1–v0.12 工作台功能对照

左栏 **Research tools** 读取当前项目设置，可从首页或已打开的会话进入。首页显示项目级操作；打开会话后，可从同一处定位到证据、产物、用量和上下文。历史回答沿用当时记录的证据。

| 版本 | 研究者要做的事 | 在界面里怎么做 | 使用条件和边界 |
|---|---|---|---|
| [v0.1](demo-v0.1.md) | 提问、调用工具、逐条审查 | 输入问题，查看 Activity，在 Findings 下运行审查 | 连接模型和数据源；向外部 Agent 提供 MCP 服务使用终端 |
| [v0.2](demo-v0.2.md) | 核对数字和原文、控制费用与上下文 | 点击回答中的证据标记；用 Usage、Context 和 Project settings → Budget per turn；Public 项目可在 Run review 旁勾选在线核验 PMID/DOI | 在线核验检查已记录的文献编号是否存在；全文是否支持结论另看引文与审查。用量换算需配置费率 |
| [v0.3](demo-v0.3.md) | 批准计划、处理权限、中断研究 | 在输入区勾选 Review a plan first；执行中使用 Allow once、Deny 或 Stop research | 关闭页面后可回到 Inbox 处理待办 |
| [v0.4](demo-v0.4.md) | 重跑、独立重复、导出研究包 | 符合条件的计算结论下有 Rerun calculation 或 Independent replication；Session actions 提供导出 | 独立重复需 Linux 隔离；验证等级取决于结论状态、输入快照和运行环境；解包后跨机器执行使用随包脚本 |
| [v0.5](demo-v0.5.md) | 复用方法、跨会话笔记、独立文献检索 | 左栏 Skills；启用 Project notes 后在 Session actions 编辑；Chat settings 可配置 Literature subtasks，输入框可选择 Dedicated literature lookup | 子任务使用项目允许的工具和已有权限；直接提问仍可普通检索 |
| [v0.6](demo-v0.6.md) | 私有运行、隔离与审计 | Models & accounts 配本地模型；Inspect runtime 看隔离、网络和锁；Session actions → Audit log 查记录 | 本地模型安装、SSH 转发、容器和沙箱配置在运行主机完成 |
| [v0.7](demo-v0.7.md) | 管理项目、上传、选中内容追问 | 左栏管理项目和会话，输入框附加文件，选中内容加入追问；Inspector 查看产物和费用 | 引用能力取决于项目设置；使用已有文件或先上传 |
| [v0.8](demo-v0.8.md) | 判断研究覆盖及分歧 | Research tools → Research methods 启用 Evidence coverage 或 First-pass judgment；在新回答展开覆盖记录或追问分歧 | 只有当轮实际记录的检索才进入覆盖台账 |
| [v0.9](demo-v0.9.md) | 用 Python 和 R 计算并保留证据 | Inspect runtime 查看两种语言的包和 R 内核状态；在问题中指定语言，点击结果查看对应运行，Artifacts 可导出 notebook | R 需要项目配置和 IRkernel；依赖由使用者安装 |
| [v0.10](demo-v0.10.md) | 跨会话研究、更正材料、交接 | Research lines 管理研究线和决定，Materials 管理材料版本、更正和影响范围，Inbox 定位待复核项 | 会话与研究线、结论与材料的关联必须已有记录 |
| [v0.11](demo-v0.11.md) | 看专业产物并引用具体位置 | Artifacts 或回答中的产物入口打开结构、序列、分子、轨道、表格或图；选择残基、区间、单元格等继续追问 | 查看器随文件类型出现；需要支持的项目文件，追问需启用引用 |
| [v0.12](demo-v0.12.md) | 并行研究、断点恢复、角色模型路由 | Research tasks、Inbox 查看任务并恢复；Research methods 列出项目专家；Models & accounts → Project models and fallbacks 可看各角色、备用链和评测结果 | 专家定义来自项目的 Markdown 文件。模型评测由研究者在终端启动；运行后台任务期间服务需持续运行或随后恢复 |

阅读这张表可以确认界面入口。科学结论质量、真实外部服务可用性、跨物理机器重复及版本验收分别见每个版本的演示和[验收矩阵](release/v1.0-acceptance.md)。
