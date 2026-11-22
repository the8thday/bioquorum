# v1.0 公开基准扩样 · TableQA

2026-11-22 冻结。本批衡量 BioQuorum 从单张公开论文表格图片读取答案的能力。32 个输入和评分入口均已验证。本批成绩为 28/32，冻结规则和正式结果如下。[上游固定版本](https://github.com/Future-House/LAB-Bench/tree/998a8e0a40cf116c80e1b0e7a805ebb5fb9fa838/TableQA)

## 入选和评分规则

- 输入为固定版本 `tableqa-v1-public.jsonl` 的 244 道公开题。先排除 v0.4 两道 TableQA 试题涉及的论文和表图，再限于单图题。按 `SHA256("bq-v1-tableqa/" + id)` 升序取题，每个上游论文来源和每张表图最多一次。题目入选只使用 ID、来源和输入路径。
- 新组含 32 篇来源、32 张表、32 道题。32 个独立来源可同时保留逐题复核能力，一题对应 3.125 个百分点。该设计仅覆盖单表阅读。公开题可能进入过模型训练，无法确认模型此前是否接触过题面。
- 准备程序固定题库和图像哈希。模型项目仅放本题图片，提示包含题干以及按 v0.4 规则固定排序的选项（含信息不足）。评分键留在项目外。评分使用选项全文，忽略大小写与空白差异。保留未完成和缺失，每次报告的分母均为 32。
- 本批预定 worker 和 reviewer 为 `openai:gpt-6.2-sol`，使用已有公开基准 runner、视觉输入、每题独立公开项目和 Linux bubblewrap。每题至多 12 步，探索预算 32,000 tokens。实际模型调用、输入输出 tokens、失败、隔离和逐题回答与结果一并保留。本轮费用授权和实测所用的内核平台见下文。原四题使用的模型和成绩另见 [v0.4 实测](../release/v0.4-acceptance.md)。

## 复做

在仓库根目录执行。另一台机器可使用上游固定提交中的完整 `TableQA/` 目录作为 `data`。本机已备齐题库和所需的 32 张 PNG，合计图像字节数 13,953,302。已离线逐题实际调用 runner 的输入准备和提示生成函数，核对每个新目录中仅出现该题的一张图。

```sh
data=evals/results/v1/tableqa-inputs
questions=evals/results/v1/tableqa-questions.json
uv run --no-sync python -m evals.v1_tableqa --data "$data" --questions "$questions"
```

本次生成的题单 SHA-256 为 `60ff6bbc6f1274bb0e7b6e8591d7ccb0e59b51e6dbc37642ec56adfad8275434`。离线评分烟测传入空预测对象，评分器将 32 项全部标为缺失，分母为 32。

正式作答与离线评分分别运行。完成后可直接复用保存的预测做核对。

```sh
uv run --no-sync python -m evals.v1_tableqa_run
out=evals/results/v1/tableqa-official
uv run --no-sync python -m evals.v1_tableqa --data "$data" \
  --predictions "$out/predictions.json" > "$out/scores.json"
```

## 执行前修订 · 2026-11-22

作者批准本批合计 50 美元。首次付费请求前将执行条件登记为本机 macOS seatbelt，或具备相同断网要求的 Linux bubblewrap。本机使用前者，原计划中的 Linux 专项实测保留延期。题单摘要、题图、评分器、模型、题目级上限均沿用冻结版本。使用既有 OpenAI 请求计数和跨请求 SQLite 费用预留，将作答、审查及重试计入同一账本。输出上限为每次请求 4,096 tokens。运行器只接受冻结题单，使用实际模型及独立项目；已落盘的答案保留。正式入口为 `uv run --no-sync python -m evals.v1_tableqa_run`，结果位于 `evals/results/v1/tableqa-official/`。

## 正式结果

2026-11-22，32 个独立项目全部完成，正确 **28/32（87.5%）**。实际运行均为 macOS seatbelt，工具完成 48 次。事件中有 82 次成功 worker 请求，均为 gpt-6.2-sol；本次答题路径没有发起 reviewer 请求。总输入 380,073 tokens，输出 5,700 tokens。共享账本的 82 笔记录合计占用 **1.202919 / 50.00 美元**，以供应商账单为最终费用依据。

四道错题的 ID 前缀为 `3b61eec6`、`ba146a67`、`60c43ef9`、`0bf8ed44`，其中三题选了信息不足。`60c43ef9` 的源 PNG 为 3,905,852 字节，超过单张 2 MiB 的模型附图限制；这条观察不能单独确定失分原因。评分保持冻结口径。保留了各题的原文、事件库、模型与内核记录，预测 JSON 的 SHA-256 是 `096c416d32b8a26264cfa87db35f88f3104273fc2234e391514e4d3c551ef020`。复核时直接对保存的预测运行评分命令即可。

[32 道题的逐题判定和用量](v1-tableqa-results.csv)使用题单原始 ID，可与上游题库和本机预测文件对齐。
