# v0.6 私有部署开发预览

## 工作台审计

在研究会话点 **Audit log**。页内显示项目数据分级、模型调用、工具执行、权限决定和内核隔离模式。当前页只读，以会话事件流为准。用 **Older events** 读取早期记录。提示词和工具参数留在本地研究事件库，审计页只显示运行元数据。

## 服务器与本机模型

在自己的 Linux 主机安装本项目和 bubblewrap，确认非特权用户命名空间可用。以专用普通用户运行，每项研究选定一个独立项目目录。以下命令在服务器上运行。

```bash
bq workbench -C /srv/research/example --port 8765 --no-browser
```

在自己的电脑建立 SSH 转发，再打开 `http://127.0.0.1:8765/`。

```bash
ssh -N -L 8765:127.0.0.1:8765 analyst@research-host
```

在服务器本机启动已安装的 Ollama，给已配置的模型指定 loopback 地址。

```toml
[providers.local]
kind = "ollama"
base_url = "http://127.0.0.1:11434/v1"

[roles]
worker = "local:your-installed-model"
reviewer = "local:your-installed-model"
```

## macOS 内核和 stdio MCP 隔离

```toml
[kernel]
macos_sandbox = true

[mcp.servers.example]
command = "/path/to/installed/python"
args = ["/path/to/connector.py"]
read_paths = ["/path/to/connector.py"]
isolated = true
clearance = "local"
```

隔离的内核可以读写项目允许的文件，通过私有 Unix socket 与 BioQuorum 通信。隔离的 MCP 进程在自己的临时目录运行，只挂载明示资料、可执行文件的运行时和系统目录。该模式支持离线 stdio 连接器。启用隔离后，启动失败会显式报错。macOS 配置使用系统 `sandbox-exec`，支持情况以该主机实测为准。

## HTTPS 出站白名单

在同一主机启动独立的 loopback 代理。按需要列出精确的 HTTPS 域名。

```bash
bq egress --port 8788 --allow eutils.ncbi.nlm.nih.gov --allow doi.org
```

```toml
[security]
outbound_proxy = "http://127.0.0.1:8788"
```

支持项目下载、在线引文查询、远端模型和 HTTP MCP 的受控代理路径。本机模型直接走已声明的 loopback 端点。项目配置代理时，stdio MCP 必须使用离线隔离，内核网络开关关闭。主机级的出站规则在部署网络中配置，代理只管理经过它的请求。

## Docker CLI 镜像

镜像构建需要 Python 基础镜像、系统软件包和 PyPI 依赖。以非 root 用户运行，给 UID `10001` 一个可写的专用项目目录。

```bash
docker build -t bioquorum:v0.6-dev .
docker run --rm bioquorum:v0.6-dev --help
docker run --rm --mount type=bind,src=/path/to/project,dst=/workspace \
  bioquorum:v0.6-dev sessions -C /workspace
```

内核隔离能力依赖容器宿主的用户命名空间配置及容器策略。私有项目在隔离不成立时拒绝启动内核。工作台的远程单用户使用方式见上面的 SSH 转发。 [安全边界](../SECURITY.md)
