# 安装和候选发布

在源码目录用 `uv sync --locked` 配置环境，随后 `uv run bioquorum` 启动工作台。已有项目可传 `-C PATH`。使用其他目录时默认打开随包的离线示例。界面所需资源在 wheel 内，使用者无须安装 Node。

候选包可在发行目录用 `uvx --from ./bioquorum-1.0.0rc1-py3-none-any.whl bioquorum` 运行；依赖须已缓存或能从包索引安装。正式版经作者发布到 PyPI 后，普通用户的入口为 `uvx bioquorum`。运行 `bq` 子命令可用 `uvx --from bioquorum bq --help`。

文档站的 Markdown 渲染器已列入开发依赖。本轮沿用已有环境构建。普通 `uv run` 会依据 pyproject.toml 与锁文件自动同步环境，已有可用依赖时可用 `uv run --no-sync`。

## 开发者发布流程

- 使用 PEP 440 的主、次、修订版本。候选包用 rcN，正式版本去掉预发布后缀。同步 pyproject.toml、包的 __version__、uv.lock，CHANGELOG 中按版本列出用户可见变更、迁移要求和已测范围。
- 手动运行 release 工作流，默认只构建。工作流执行测试、包成员检查、wheel 入口烟测并构建非 root Docker CLI 镜像。镜像构建命令为 `docker build -t bioquorum:candidate .`；运行 `docker run --rm bioquorum:candidate --help`。部署限制见[私有部署](../demo-v0.6.md)。
- 作者确认名称、商标和 PyPI Trusted Publisher，并在对应版本 tag 上明确选择 publish，才向索引提交。tag 必须与 wheel 元数据版本一致。候选构建与正式发布分别记录。
