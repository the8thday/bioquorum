# ADR-013 reviewer 按审查能力选择，家族多样性改为可选（取代 ADR-011）

状态：已接受（2026-11-17）。取代 [ADR-011](011-reviewer-different-family.md)。

## 背景
ADR-011 假设同家族模型自审有盲区，因此默认要求 reviewer 与 worker 不同家族。v0.0 对照实验（[docs/research/reviewer-experiment.md](../research/reviewer-experiment.md)）用 GPT-5.5、GPT-6.1-sol、DeepSeek-v4-pro 做了两轮交叉：在可对照证据核实的错误上（改数字、假引用、无执行证据），同家族严格召回 89.5%、跨家族 88.7%，没有发现盲区。决定审查质量的是 reviewer 本身的能力：static 语料上，三个模型的召回率是 83%–94%，干净题误判 1/4–3/4。需要判断的错误（`citation_supports`、`method_sanity`、因果表述过度）样本太少，没有结论。

## 决定
- 角色集合不变。`reviewer` 默认取配置中审查评测成绩最好的模型；没有评测数据时回退到 worker，并提示用户配置 reviewer。
- 同家族**不再警告**，也不需要 `allow_same_family`。`prefer_different_family = true` 作为可选配置保留：开启后，候选 reviewer 成绩相近时优先选不同家族的。
- 评测报告继续把确定性检查和模型判断类检查分开报告，并补测判断类错误上的同家族 / 跨家族差异（ROADMAP v0.2）。如果补测显示跨家族有显著优势，再写新 ADR 把默认改回来。
- 对外宣传不说"跨家族审查能抓更多错"。交叉验证的卖点落在：结论级 Claim、确定性检查、可重跑、共识模式。

## 后果
- 用户只配一家 provider 也能完整使用审查功能，上手门槛更低。
- `models/` 需要能读取审查评测成绩（能力矩阵里加一列），v0.1 先用配置文件里的手工值。
- PRODUCT.md 支柱 2 和竞品对比表相应修改。
