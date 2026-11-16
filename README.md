# BioQuorum

**The open, model-neutral AI workbench for biomedical research — cross-verified, reproducible, and private by design.**

> Status: pre-alpha (v0.0, foundations). Nothing to run yet beyond the test suite.
> Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/PRODUCT.md](docs/PRODUCT.md) · [ROADMAP.md](ROADMAP.md) · decisions in [docs/adr/](docs/adr/)

## Why

| Question researchers ask | BioQuorum's answer |
|---|---|
| "Can I trust this conclusion?" | Every claim is reviewed by a model from a *different* family and marked `unverified → reviewed → reproduced`. |
| "Can I rerun this in six months?" | Every artifact carries code, environment lock and input hashes; export a research bundle that reruns with only `pixi`. |
| "Can I use company data?" | Runs locally, zero telemetry, data classification enforced on model routing, audit log. |

## Development

```bash
uv sync --all-groups
uv run pytest            # unit tests, no network, no model calls
uv run lint-imports      # architecture boundaries (ARCHITECTURE.md §2)
uv run ruff check . && uv run pyright
```

License: Apache-2.0.
