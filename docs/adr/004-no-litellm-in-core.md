# ADR-004 不内置 LiteLLM

状态：已接受（2026-11-13）

## 背景
LiteLLM 覆盖的 provider 最广，但依赖体积大，且 2026 年两次 PyPI 投毒事件。Pydantic AI 已原生支持 Anthropic、OpenAI、Google 及任意 OpenAI 兼容接口。

## 决定
核心包不依赖 LiteLLM。用户需要网关时，把 LiteLLM Proxy、OpenRouter 等作为 OpenAI 兼容端点配置进来。

## 后果
- 少一个重依赖和供应链风险。
- 少数特殊协议的 provider 需要用户自行架网关。
