# v0.5 场景 A 扩展验收

2026-11-19，本地 `qwen3:4b-instruct` 从空白会话回答题库的四道剩余题。模型自行选择搜索词和工具。可用入口是 Europe PMC、GWAS Catalog、HPA、美国 FDA 标签和 ClinicalTrials.gov。评分数据包括原始回答、成功和失败的调用、来源、逐字绑定及审查事件。IL23R 的预设步骤验收另有记录。 [首个场景 A 样例](v05-scenarios.md)

| 题目 | 成功来源 | 有效逐字绑定 | 审查与覆盖 |
|---|---:|---:|---|
| TYK2 | 4 | 3/4 | 把单次空查询推成缺少遗传证据。漏掉关键变体、感染边界和标签机制。审查产出 3 条 `disputed`、1 条 `grounded`、6 条 `unverified`。 |
| PCSK9 | 3 | 2/7 | 找到部分药物记录和体外功能研究。漏掉关键人群遗传与临床结局来源，回答混入拟运行代码。提取器没有交回可审查条目。 |
| HSD17B13 | 4 | 4/5 | 摘要请求超时。引用以试验标题和搜索结果为主，漏掉原始保护性变体和相关项目，用空返回推断证据缺乏。提取器没有交回可审查条目。 |
| NOD2 | 6 | 3/6 | 检索命中综述与无关条目，漏答三种主要变体和功能方向。提取器没有交回可审查条目。 |

逐字核对通过只说明引句可回到来源。标题匹配、无关研究和空查询仍可能支撑不了结论。本次四题都生成了可校验的研究记录，自主检索的科学覆盖未达到发布要求。三个空抽取结果不计为通过审查。保留原始答案供诊断。

随后用预先选定的原始记录复跑，诊断检索失败和后续合成的影响。TYK2 取回 2 份来源，7 处逐字绑定通过，没有结论升级为 `reviewed`。PCSK9 的初次对照超时，限时重跑取回 4 份来源，6/7 处逐字绑定通过，抽取器仍没有交回可审查条目。HSD17B13 只取回 2 份来源，7 处绑定均失败。NOD2 的首轮摘要读取超时，随后 NCBI BioC 的独立读取检查取得全部 3 篇摘要。带模型重跑时收到 1 篇，另外 2 次调用失败，收到的来源支持 4 处绑定和 4 条机器审查结论。这些结果保留在不同会话，首次自主检索成绩保持原样。 [NCBI BioC 服务](https://www.ncbi.nlm.nih.gov/research/bionlp/APIs/BioC-PubMed/)

复核漏项可从这些原始记录开始。

- TYK2 的原始遗传研究、感染人群研究、FDA 标签和对照试验提供可独立审查的线索。 [遗传研究](https://pubmed.ncbi.nlm.nih.gov/27807284/) · [感染人群研究](https://pubmed.ncbi.nlm.nih.gov/30578352/) · [FDA 标签](https://www.accessdata.fda.gov/drugsatfda_docs/label/2026/214958s002lbl.pdf) · [试验注册](https://clinicaltrials.gov/study/NCT04999839)
- PCSK9 的遗传发现、人群结局、双变体个案和干预结局各用对应研究核对。 [遗传发现](https://pubmed.ncbi.nlm.nih.gov/12730697/) · [人群研究](https://pubmed.ncbi.nlm.nih.gov/16554528/) · [双变体报告](https://pubmed.ncbi.nlm.nih.gov/16909389/) · [FOURIER](https://pubmed.ncbi.nlm.nih.gov/28304224/)
- HSD17B13 的主要遗传锚点是原始变体研究。 [遗传研究](https://pubmed.ncbi.nlm.nih.gov/29562163/)
- NOD2 的变体和功能方向用原始功能研究复核，表达用组织证据。 [变体功能研究](https://pubmed.ncbi.nlm.nih.gov/16804397/) · [细胞表达研究](https://pubmed.ncbi.nlm.nih.gov/14570728/)

## 图像

同一张 GSE96583 QC 图在真实 Jupyter 中作为 PNG 发出，经 BioQuorum 的 Chat 兼容请求交给本机的 Apple Vision 文本识读模型。关闭附图时返回零个文字区域，开启时识读 15 个区域，预设的三个标签均命中。Chat 和 Responses 的适配器请求体也在本地 mock 服务核对过。通用视觉语言模型的图表推理、真实 Responses 服务与外部模型端点仍需单独验收。

```bash
uv run python evals/v05_target_validation.py --url http://127.0.0.1:11434/v1 \
  --output evals/results/v05/target-expansion
uv run python evals/v05_vision_eval.py --image /path/to/qc.png \
  --output evals/results/v05/native-vision.json
```

## 强模型复测（2026-11-20，Linux 服务器）

worker 和 reviewer 都用 `openai:gpt-6.1-sol`，题目、工具入口和提示与上面的本地模型首测完全相同。
四题并行，各自从空白会话开始，取证由模型自己完成。

| 题目 | 工具调用（成功/失败） | 有效逐字绑定 | 审查结论 | 输入/输出 token |
|---|---:|---:|---|---:|
| TYK2 | 31 / 0 | 22/23 | 13 `reviewed`、4 `disputed`、31 `unverified` | 96,701 / 6,638 |
| PCSK9 | 12 / 0 | 22/22 | 9 `reviewed`、5 `disputed`、12 `unverified` | 28,572 / 4,462 |
| HSD17B13 | 19 / 0 | 17/19 | 13 `reviewed`、2 `disputed`、30 `unverified` | 55,054 / 6,304 |
| NOD2 | 11 / 0 | 9/9 | 7 `reviewed`、1 `disputed`、14 `unverified` | 20,724 / 3,126 |

四题的审查都完成了，都导出了研究包。和本地模型首测相比，这次取到了关键的原始来源：TYK2 的遗传研究、
感染相关研究和 FDA 标签，PCSK9 的发现论文和人群结局研究，HSD17B13 的原始变体研究，NOD2 的原始功能研究；
回答里也没再出现"一次空查询就推断没有证据"的情况。

对照各题的评分标准，AI 自己初查（**只作参考，科学判断由作者做**），四题大约都满足 3/5：

- TYK2：漏掉 JH2 结合带来的 JAK1–3 选择性，结核易感的原始研究没有取到（回答里明确标为缺口）。其它在研药物用 ClinicalTrials.gov 记录作证。
- PCSK9：没提双等位功能缺失者的安全性证据，也没引 FOURIER 或 ODYSSEY 结局试验（只引了标签上的心血管适应症）。
- HSD17B13：没提生理底物和保护机制尚不清楚；小分子项目因检索超时没能核实（回答里明确标为缺口）。
- NOD2：方向判断正确（反对抑制），但只点名了 3020insC 移码，没提 R702W、G908R 和回肠型表型。

结论：强模型明显改善了检索和证据绑定，科学覆盖仍不完整，公开样例的科学验收继续由作者判断。
另外有一处实测观察：用 DeepSeek V4 Pro（思考模式）作 reviewer 时，单次结论抽取用了 175 秒、17,680 个输出 token，
超过了评测脚本当时设的时间上限；所以本轮按作者的要求统一用 OpenAI。
原始记录在本地 `evals/results/v05/target-expansion-remote/`。

```bash
uv run python evals/v05_target_validation.py --worker openai:gpt-6.1-sol \
  --reviewer openai:gpt-6.1-sol --output evals/results/v05/target-expansion-remote
```

## 远程视觉模型（2026-11-20）

`evals/v05_remote_vision_eval.py` 用 R 画的一张确定性 QC 柱状图（`evals/assets/vision_chart.R`），图上不印数值。
图由沙箱 kernel 显示，作为 `python_execute` 的结果交给 `openai:gpt-6.1-sol`，经真实的 Responses API。
问题是：哪个文库最低、大约多少、哪些低于 80% 虚线。开图 3/3 全对（D2-ctrl、62、D2-ctrl 和 D3-ctrl；D3-ctrl 是 79%，
只比阈值低一点）；关图 3/3 都回答看不到图。这只测了一张合成图，不代表一般的图表理解能力。
