# BioQuorum

**The open, model-neutral AI workbench for biomedical research — cross-verified, reproducible, and private by design.**

> Status: **v0.1.0, pre-alpha.** The science layer, model layer, review layer, agent loop and CLI work end to end; there is no web UI and **no sandbox** yet (see [Security](#security)).
> Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/PRODUCT.md](docs/PRODUCT.md) · [ROADMAP.md](ROADMAP.md) · decisions in [docs/adr/](docs/adr/) · changes in [CHANGELOG.md](CHANGELOG.md)

## Why

| Question researchers ask | BioQuorum's answer |
|---|---|
| "Can I trust this conclusion?" | Every answer is split into claims and checked claim by claim: deterministic checks (was it computed, do the numbers match the runs, do the citations exist, is the plan complete) plus a reviewer model reading the cited abstracts. Claims are marked `unverified → reviewed → reproduced` or `disputed`. |
| "Can I rerun this in six months?" | Every run is recorded with its code hash, environment and outputs in a content-addressed store; research bundles that rerun with only `pixi` arrive in v0.3. |
| "Can I use company data?" | Runs locally, zero telemetry, data classification enforced on model routing and tool calls, every action in an append-only audit log. |

We tested whether a reviewer from a *different* model family catches more errors than one from the same family. On evidence-checkable errors it did not ([report](docs/research/reviewer-experiment.md)), so BioQuorum picks the reviewer by measured review quality ([ADR-013](docs/adr/013-reviewer-by-capability.md)).

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). API keys are read from environment variables only.

```bash
git clone https://github.com/the8thday/bioquorum ~/bioquorum && cd ~/bioquorum
uv sync --all-groups
export OPENAI_API_KEY=... DEEPSEEK_API_KEY=...        # any two providers you have
uv tool install biomcp-cli                            # optional: literature, trials, variants

mkdir -p ~/bq-demo && cd ~/bq-demo
uv run --project ~/bioquorum bq project init --classification public
# write bioquorum.toml (providers, roles, MCP servers): see docs/demo-v0.1.md
uv run --project ~/bioquorum bq models                # which model plays which role
uv run --project ~/bioquorum bq run "What are the proposed mechanisms by which metformin lowers blood glucose?"
```

Commands: `bq run` (ask; tools; answer; review) · `bq review [--answer-file]` · `bq sessions` · `bq models` · `bq tools` · `bq mcp serve` (use BioQuorum's tools from Claude Code or other MCP hosts) · `bq mcp record` · `bq project init|show` · `bq db events|rebuild`.

Full walkthrough: [docs/demo-v0.1.md](docs/demo-v0.1.md).

## Security

v0.1 has **no OS sandbox**. Tool calls go through permission checks, classification checks and the audit log, but code the model runs in the Jupyter kernel can read files and reach the network directly. Do not use confidential data with v0.1. Sandboxing is on the [roadmap](ROADMAP.md).

## Development

```bash
uv sync --all-groups
uv run pytest            # unit tests: no network, no model calls, no real kernel
uv run pytest -m integration   # real Jupyter kernel
uv run pytest -m live          # real providers and databases (needs keys, costs a little)
uv run lint-imports      # architecture boundaries (ARCHITECTURE.md §2)
uv run ruff check . && uv run pyright
```

Contributor and AI-assistant conventions: [AGENTS.md](AGENTS.md).

License: Apache-2.0.
