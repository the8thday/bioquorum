# Contributing to BioQuorum

Start with [AGENTS.md](AGENTS.md), [NEXT.md](NEXT.md) and the
[architecture](docs/ARCHITECTURE.md). Use Python 3.12 or newer.

```sh
uv sync --locked --all-groups
uv run pytest
uv run lint-imports
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Tests run offline by default using the model cassette and FakeKernel. Linux
bubblewrap and real-kernel checks are opt-in via `uv run pytest -m integration`.
The browser test uses a local server, the packaged replay and Chrome.

```sh
npm ci --prefix tests/e2e --ignore-scripts
npm test --prefix tests/e2e
```

Run `uv run python scripts/record_workbench_example.py` if a deliberate change to
the fixture, model messages or tool definitions changes the replay requests.
The generator uses scripted responses and cannot call a provider. Use a temporary
public workspace for browser development. Raise architecture changes in an ADR
and keep user-facing claims aligned with the evidence in the repository.

Please use the disclosure instructions in [SECURITY.md](SECURITY.md) for
security reports. Never include keys or private research data in an issue,
test fixture, screenshot or commit.
