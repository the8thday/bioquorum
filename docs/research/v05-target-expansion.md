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
