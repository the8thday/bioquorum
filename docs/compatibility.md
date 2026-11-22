# v1.x 兼容规范

## 研究包与项目

`manifest.json` 的 `spec_version` 是字符串。可移植包使用 `"1"`，字段、类型、必填项以 [v1 Schema](../bundle-spec/manifest-v1.schema.json) 为准。读者先校验 `files` 中各归档相对路径的 SHA-256。重算必须核对声明的输入、交接文件、输出和环境，保留失败及无法判定。历史 v0 是记录格式。导出包与可继续工作的 SQLite 项目有不同的内容和入口。[格式与执行规则](../bundle-spec/README.md)

项目打开时从 `.bioquorum/project.json` 取项目身份，原始事件保存在同目录的 SQLite 库。事件以 `type` 和整数 `schema_version` 识别，信封持有 ID、时间、会话、序号、actor、causation_id 和 payload。旧 payload 按连续版本转换后解析。新增版本不得改变过去事件的身份、顺序或证据语义。项目库自动事务迁移。先备份整个项目，再在副本用新版打开；降级时恢复备份。[事件目录](ARCHITECTURE.md#53-事件类型v01-起) · [旧版本夹具](../tests/fixtures/compat/generate.py)

## bioquorum.toml

全局配置位于 `~/.bioquorum/config.toml`，项目同名设置优先；设置页修改和项目初始化还有相应的记录事件。新建项目可用 recommended、minimal 或 custom 配置。切换已有项目时保留已存储的选择。如下是公共项目的最小模型配置。

```toml
[providers.openai]
api = "responses"
api_key = "env:OPENAI_API_KEY"

[roles]
worker = "openai:gpt-6.2-sol"
reviewer = "openai:gpt-6.2-sol"
```

| 配置段 | 用途与可接受值 |
|---|---|
| project | data_dirs 是可读数据目录列表，相对路径以项目根为基准，在隔离内核中只读。 |
| providers.NAME | kind、base_url、api、family、api_key 和 allowed_classifications 字符串列表；密钥使用 env:NAME。连接本机兼容服务见部署说明。 |
| roles | worker、reviewer、planner、summarizer 使用 provider:model 字符串或有序回退列表。reviewer 也可用 auto。回退仍受分级约束。 |
| models."provider:model" | vision 为布尔值（默认 false），context_window 为正整数，review_score 为数值；另可声明模型价格，详见模型解析器。缺失费用在记录中保留未知。 |
| profile、agent | 配置名、证据绑定、覆盖、Skills、子任务、预算等。推荐组合以已冻结的评测为依据。 |
| kernel、security | pixi、macos_sandbox、network、outbound_proxy 等。分级拒绝优先于执行。 |
| mcp.servers.NAME | 本地命令及 args 或 HTTP url，clearance、protocol、timeout_s 等按该运输方式配置。 |
| extensions、permissions | 项目扩展的启用、连接配置与权限撤销覆盖。 |

精确字段、参数默认值与各类约束见[项目配置读取](../src/bioquorum/application/workspace.py)、[模型配置](../src/bioquorum/models/config.py)和[部署示例](demo-v0.6.md)。既有字段的含义在 v1.x 保留，不兼容变化需要新版本和迁移记录。

## 编写扩展

- **Skill**　在项目 `.bioquorum/skills/my-skill/SKILL.md` 写 YAML 头 `name: my-skill` 和 `description: ...`，在正文写方法及限制，在同目录放要读取的文字资源。启用 Skills，再让工作台打开该 Skill。脚本不会因导入自动运行。[加载器](../src/bioquorum/skills/loader.py) · [内置实例](../src/bioquorum/skills/builtin/)
- **Connector**　独立扩展以 MCP 暴露有输入模式的工具，在项目配置中声明命令或 URL、数据分级及权限。在工作台测试连接再启用。内置连接器可按现有实现写成异步数据读取函数，由带有权限、分级和输入模型的 ToolSpec 注册，通过 invoke 调用。[内置连接器示例](../src/bioquorum/tools/public_sources.py) · [MCP 配置](demo-v0.1.md)
- **Review check**　实现带 `name`、`needs_model` 的 Check，其 `async run(claims, evidence)` 返回 Finding 列表。每条 Finding 记录 claim_index、check、severity 及可核对的 evidence。在嵌入式调用中把实例加入 review 的 checks 列表，随发行版提供的检查在 deterministic_checks 中组装。[协议](../src/bioquorum/review/checks/base.py) · [组装](../src/bioquorum/review/checks/__init__.py)
- **Viewer**　在 web/src/viewers 编写默认导出的 React 组件，接收 ViewerProps 的 file、base、products、open，按需接收 initial、comparedFigure 和 quote。用 registry 中的延迟 import 注册对应类型，重建工作台。选区保留版本及原始坐标，未识别类型仍可下载。[注册表](../web/src/viewers/registry.ts) · [引用与权限](adr/042-artifact-viewers.md)

配置中的工具和角色以名字引用。stdio 的 protocol 缺省为 legacy，HTTP 缺省为 auto；MCP clearance 缺省 public。公开项目可显式允许内核网络，敏感项目始终先检查沙箱能否成立。各代理与工具调用的安全边界见[安全模型](../SECURITY.md)。

旧事件升级按 type、schema_version 逐级读取；未知的更高版本返回错误，原始 JSON 仍在数据库。检查 v1 包规范时可运行 `uv run --no-sync python scripts/gen_bundle_schema.py` 并核对生成结果。历史项目的来源提交、固定的样例生成逻辑与逐版回归在[夹具生成器](../tests/fixtures/compat/generate.py)和[升级测试](../tests/unit/storage/test_v1_compatibility.py)。
