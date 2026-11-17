# v0.1 演示步骤

> 前提：`OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 已在当前终端 export；`biomcp` 已安装（`uv tool install biomcp-cli`）。
> 下面的 `bq` 指 `/data/Work/AgenticScience/.venv/bin/bq`（或在仓库里用 `uv run bq`）。

## 1. 建项目

```bash
mkdir -p ~/bq-demo && cd ~/bq-demo
bq project init --name metformin --classification public
cat > bioquorum.toml <<'TOML'
[providers.openai]
[providers.deepseek]

[roles]
worker = ["openai:gpt-6.1-sol", "deepseek:deepseek-v4-pro"]
reviewer = "auto"

[models."deepseek:deepseek-v4-pro"]
review_score = 94.4
[models."openai:gpt-6.1-sol"]
review_score = 91.7

[mcp.servers.biomcp]
command = "biomcp"
args = ["serve"]
clearance = "public"
timeout_s = 120

[mcp.servers.opentargets]
url = "https://mcp.platform.opentargets.org/mcp"
TOML
bq models          # worker = GPT-6.1-sol, reviewer = DeepSeek（按评分）
```

## 2. `bq run`：提问 → 工具 → 回答 → 逐条审查（验收第 1 条）

```bash
bq run "What are the proposed mechanisms by which metformin lowers blood glucose in type 2 diabetes? Find 2-3 key papers with the literature tools and build a short cited evidence table."
```
不加 `--yes` 时，第一次碰到未授权的操作会在终端里问你（一次 / 本会话 / 本项目 / 拒绝）。

## 3. `bq review` 抓出假引用（验收第 2 条）

```bash
cat > fake.md <<'MD'
1. Metformin lowers blood glucose mainly by reducing hepatic gluconeogenesis [PMID:32897388].
2. A 2019 meta-analysis of 48 trials showed metformin cures type 2 diabetes in 62% of patients [PMID:31415926].
MD
bq review --answer-file fake.md      # 第 2 条：citation_resolves 报 PMID:31415926 不在本会话检索到的来源里
```
（第 1 条的 PMID 要在第 2 步的会话里真的被检索过才会通过。）

## 4. 在 Claude Code 里用同一套工具（验收第 3 条）

```bash
claude mcp add bioquorum -- /data/Work/AgenticScience/.venv/bin/bq mcp serve -C ~/bq-demo
```
在 Claude Code 里让它"用 bioquorum 的 python_execute 和 biomcp 工具查二甲双胍机制并给出引用"。答完把回答存成 `cc-answer.md`，然后：
```bash
bq sessions                                  # 找到 "mcp serve" 那个会话
bq review <session-id> --answer-file cc-answer.md
```
Claude Code 的回答就按 MCP 会话里实际跑过的工具结果逐条审查。

## 5. 录终端演示（验收第 4 条）

推荐 asciinema（需要你自己安装：`! uv tool install asciinema`）：
```bash
asciinema rec bq-v0.1.cast    # 然后执行第 2、3 步，Ctrl-D 结束
```

## 已知局限（演示时如实说明）

- **没有沙箱**：kernel 里的代码可以自己联网、读写文件，不经过权限和分级检查（ARCHITECTURE §8）。confidential 数据不要在 v0.1 里用。
- 审查只能核对会话里检索到的内容；worker 在 kernel 里自己下载的全文，reviewer 看不到。
- 数值检查是"出现即通过"：证据很长时，编造的数字可能碰巧在别处出现过（见 docs/research/review-checks.md）。
