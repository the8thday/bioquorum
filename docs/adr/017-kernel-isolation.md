# ADR-017 kernel 在 OS 级隔离里运行，网络默认关闭

状态：已接受（2026-11-17）。实现于 v0.2。

## 背景
v0.1 验收实测：worker 在 `python_execute` 里 `import requests` 自己联网，绕过了 `invoke()` 的权限、分级和审计；kernel 也能读写项目外的文件。提示词里的要求不是安全控制。NEXT.md 列了三个方案：OS 命名空间隔离、进程内 monkeypatch、把完整沙箱提前。monkeypatch 可被绕过，只能算提示；完整沙箱（白名单代理、MCP 进程隔离）工作量大。开发机上 `bwrap` 可用，非特权的网络命名空间也可用。

## 决定
- 【承重】kernel 进程在 OS 级隔离里启动。Linux 用 bubblewrap：
  - 网络：独立的空网络命名空间，**所有分级默认不能联网**。联网只能经工具。
  - 文件：项目目录可读写，`data_dirs` 和 Python 环境只读，私有 tmp；其余不可见。
  - 与 kernel 的通信走私有目录里的 unix socket（v0.1 已是绝对路径 IPC），不依赖网络。
- 【承重】隔离不可用时（没有 bwrap、平台不支持）：`public` 项目可以运行，但每次会话明确提示"未隔离"并写审计；`internal` / `confidential` 项目**拒绝启动 kernel**，不静默降级。
- `public` 项目可以由用户明确选择让 kernel 联网；`internal` / `confidential` 没有这个选项，配置了也拒绝启动。【可调】选择的方式：v0.2 是项目配置 `[kernel] network = true`（配置文件在沙箱里只读，模型改不了），界面就位后改为权限卡片。
- 环境变量：沙箱里清空后只设白名单，API key 进不了 kernel。未隔离运行时也去掉名字像密钥的变量。
- kernel 不能碰 BioQuorum 自己的状态和决定"下次在沙箱外运行什么"的文件：`.bioquorum/` 在沙箱里是空目录，`bioquorum.toml` 和 `.git` 只读。
- kernel 里不能自己装包：改环境走工具，需要权限。
- macOS（`sandbox-exec`）在有用户需要时补；在那之前按"隔离不可用"处理。
- 这是 `runtime/` 内部的实现。`Kernel` 接口只增加一个只读属性 `isolation`（`bwrap` / `bwrap+net` / `none`），由 `tools/runs.py` 记入每个 `run_started` 事件，所以每次执行是否隔离都可审计。上层其余部分不变（§8）。
- 实现要点：打断不能用信号（信号会打到 bwrap 进程，连 kernel 一起结束），改为经控制通道让 kernel 自己中断；解释器常是一串符号链接（uv、pyenv、conda），沙箱里要把链路上的目录挂进去并重建中间的链接。

## 后果
- 写 `SECURITY.md`，如实说明边界：外部 MCP 服务器进程尚未隔离，没有出站白名单代理（留在 v0.6）。
- 新增 `integration` 测试：隔离的 kernel 里联网失败、读项目外文件失败、读写项目内文件成功。
- 需要下载数据的分析（如 GEO）必须有对应的下载工具，下载结果进 CAS 并带来源记录。这顺带补上了溯源。
- 模型会先尝试联网再失败：系统提示里说明原因和替代工具，失败信息要能让模型一次改对。
