# ADR-009 Jupyter 协议作 kernel 实现，以 Kernel 接口隔离

状态：已接受（2026-11-13）

## 背景
Claude Science 用自定义 JSON 协议的 worker。Jupyter 协议是标准，R/Julia kernel 现成，可导出 ipynb；但 jupyter_client 的 API 不适合直接暴露给上层。

## 决定
`runtime/kernel.py` 定义 `Kernel` Protocol（execute / interrupt / restart / snapshot_variables）。`JupyterKernel` 是生产实现，`FakeKernel` 是测试实现。只有 `runtime/` 允许 import jupyter_client。

## 后果
- 所有上层测试用 FakeKernel，CI 不需要启动真实 kernel。
- 将来换成自定义 worker 或远程 kernel 只改 runtime。
