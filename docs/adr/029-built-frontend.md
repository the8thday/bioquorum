# ADR-029 工作台前端改用 TypeScript 组件并随包发布构建产物

状态：已接受（2026-11-20）。取代 [ADR-021](021-local-workbench.md) 中"前端采用原生 JavaScript、无构建链"的部分；ADR-021 关于 loopback、计划、打断和分叉的决定继续有效。延续 ADR-016。

## 背景
v0.3 的工作台是一个约 700 行的原生 JavaScript 文件，够用来验收证据抽屉。接下来要加连续对话、模型选择、用量、上下文、设置、选中内容引用、文件与产物面板，以及结构和序列查看器。单文件、无类型、无组件的写法在这个规模上难以由 AI 可靠地维护和测试。

## 决定
- 【可调】技术栈：React + TypeScript + Vite，样式用普通 CSS（沿用现有自然史期刊版式的变量），不引入 Tailwind 和大型组件库。运行时依赖逐个写明理由，记入 `THIRD_PARTY.md`。
- 源码放在仓库根目录的 `web/`；构建产物输出到 `src/bioquorum/server/web/` 并**提交进仓库**，随 wheel 发布。
- 【承重】用户和只跑后端的协作者不需要 Node（ADR-016 不变）：`git clone` 后 `uv run bioquorum` 直接可用。Node 只在修改前端时需要。CI 检查提交的构建产物与源码一致。
- 【承重】界面不持有不能从事件推出的状态（ADR-016 不变）。因此：
  - 每一轮的选择（模型、机制开关、引用的选段、附带的文件）写进 `turn_requested`；
  - 项目设置的修改写成 `project_settings_changed` 事件，生效配置 = `bioquorum.toml` 之上叠加最新的设置事件；
  - 纯显示偏好（面板宽度、深浅色）可以留在浏览器本地，它们不影响研究记录。
- 密钥不经过界面：设置页只显示 `env:NAME` 引用和 set / missing，不提供输入框。
- 前端只调用 `server/` 的 HTTP 和 SSE 接口；`server/` 仍只转发 `application/` 的调用。
- 测试：组件测试用 Vitest，不联网；端到端测试继续用 `tests/e2e/`，对着 cassette 模型和 `FakeKernel`。
- 查看器（Mol*、序列、基因组）按需懒加载，不进首屏包。

## 后果
- 开发机需要 Node（修改前端时）。本机现有 Node 18；动手前先确认所选 Vite 版本支持，或请作者升级 Node。
- 旧的 `workbench.js` 在新前端通过同一套端到端验收后删除，不并存。
- ARCHITECTURE §6.9 相应更新；`project_settings_changed` 实现时加入 §5.3。
