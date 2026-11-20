# BioQuorum

> 我并实质参与过抗体药物的研发。那我想试一下，只通过这套Harness加最好的模型，可不可以得到一个候选抗体序列,并把一个抗体序列推到PCC阶段

**The open, model-neutral AI workbench for biomedical research — cross-verified, reproducible, and private by design.**

> Status: **v0.4.0 development candidate.** Rerun recorded calculations, compare evidence and export a portable research bundle from the workbench or CLI. Model-written code runs in a sandboxed kernel on Linux (see [Security](#security)).
> Where it is going: [docs/DESIGN.md](docs/DESIGN.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/PRODUCT.md](docs/PRODUCT.md) · [ROADMAP.md](ROADMAP.md) · decisions in [docs/adr/](docs/adr/) · changes in [CHANGELOG.md](CHANGELOG.md)

[Try the v0.4 verification walkthrough](docs/demo-v0.4.md) · [Documentation](docs/index.md)

## Why

| Question researchers ask | BioQuorum's answer |
|---|---|
| "Can I trust this conclusion?" | The workbench and CLI resolve named values and exact quotes against session evidence. Review checks the claims and assigns `unverified`, `grounded`, `reviewed`, or `disputed` as evidence permits. |
| "Can I reconstruct what happened?" | The event log records run code, declared input hashes, outputs, and evidence links. |
| "Can I use company data?" | Runs locally, zero telemetry, data classification enforced on model routing and tool calls, every action in an append-only audit log. |

We tested whether a reviewer from a *different* model family catches more errors than one from the same family. On evidence-checkable errors it did not ([report](docs/research/reviewer-experiment.md)), so BioQuorum picks the reviewer by measured review quality ([ADR-013](docs/adr/013-reviewer-by-capability.md)).

## Quick start

Open the included offline example from a checkout with `uv sync --locked` and
`uv run bioquorum`. The example needs neither API keys nor Node. Submit and approve
its plan, then click a number or citation in the research record to inspect the
evidence. See the [v0.3 walkthrough](docs/demo-v0.3.md) for the local wheel and
for the research controls.

### Use your own project

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

Commands: `bq rerun` · `bq replicate` · `bq export` · `bq workbench -C PROJECT` · `bq run` (ask; tools; answer; review) · `bq review [--answer-file]` · `bq sessions` · `bq models` · `bq tools` · `bq mcp serve` (use BioQuorum's tools from Claude Code or other MCP hosts) · `bq mcp record` · `bq project init|show` · `bq db events|rebuild`.

See the [v0.2 walkthrough](docs/demo-v0.2.md) for budget configuration, usage reports, public citation checks, and the PBMC and literature demos.

## Security

Tool calls go through permission checks, classification checks and the audit log. Code the model runs in the Jupyter kernel is isolated with [bubblewrap](https://github.com/containers/bubblewrap) on Linux: no network, write access only to the project directory, no API keys in its environment. Where that isolation is not available (macOS, Windows, containers without user namespaces), `public` projects run with a warning and `internal` / `confidential` projects refuse to run code. External MCP server processes are not isolated yet. Details and limits: [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --all-groups
uv run pytest            # unit tests: no network, no model calls, no real kernel
uv run pytest -m integration   # real Jupyter kernel
uv run pytest -m live          # real providers and databases (needs keys, costs a little)
uv run lint-imports      # architecture boundaries (ARCHITECTURE.md §2)
uv run ruff check . && uv run pyright
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and [THIRD_PARTY.md](THIRD_PARTY.md).

License: Apache-2.0.
