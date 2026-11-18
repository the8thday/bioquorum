# Changelog

Extension interfaces (checks, connectors, providers, skills) may change before v1.0; every such change is recorded here (ARCHITECTURE.md §7).

## Unreleased (0.2.0)

- **direction**: `docs/DESIGN.md`; ADR-014 (evidence binding), ADR-015 (trust ladder, replication), ADR-016 (record-centric web workbench, supersedes ADR-012), ADR-017 (kernel isolation), ADR-018 (harness mechanisms measured by lift). ARCHITECTURE and PRODUCT v0.4; roadmap reordered.
- **runtime**: kernels start inside bubblewrap (`runtime/sandbox.py`): no network, project dir read-write, data dirs and the Python environment read-only, cleared environment, `.bioquorum/` hidden, `bioquorum.toml` and `.git` read-only. `internal` / `confidential` projects refuse to start a kernel when isolation is unavailable; `public` projects run with a warning. `[kernel] network = true` (public projects only).
- **interface change**: `Kernel` gains a read-only `isolation` attribute; `run_started` events gain an optional `isolation` field.
- **cli**: `bq run` and `bq mcp serve` report or refuse the isolation mode before running; an empty `bioquorum.toml` is created if missing so it can be mounted read-only.
- Secret-looking environment variables are no longer passed to kernels in any mode.
- **tools**: `download_file` fetches public files into `downloads/` and the CAS (public projects, `network` permission on the host; private/local addresses refused on every redirect hop; streaming size limit). `httpx2` is now a declared dependency (it was already installed through `mcp` and `pydantic-ai`).
- **evidence binding (ADR-014)**: kernels get a `bq` object at start and restart; `bq.value(name, value, unit, desc)` records a named scalar on `run_finished.values` (successful runs only). Answers write `{{name}}` / `{{name:spec}}` and `[@s3 "quote"]` / `[@PMID:123 "quote"]`; `review/binding.py` resolves them deterministically; tool results the model sees start with `[source sN]`. `bq run` prints the answer with recorded values filled in, failed bindings marked, and an evidence list.
- First real-model runs (GPT-6.1, 2026-11-18) led to four fixes: label numbers (`type 2`, `outcome 0`, `IL-23`) no longer block `grounded`; checks receive bound values at full precision (a rounded `{{p:.3e}}` was disputed by `value_match`); quotes may contain straight quotes and malformed `[@` markers fail instead of passing as text; source labels in the CLI evidence list were swallowed by rich markup.
- **status (ADR-015)**: new level `grounded` and triggers `bindings_resolved` / `bindings_failed`. Levels cannot be skipped: **an unbound claim that passes review now stays `unverified`** (it was `reviewed` in 0.1), and leaving `disputed` restarts from the bottom. Failed bindings are `binding` findings at error level.
- **agent**: unresolved bindings are sent back to the model (`user_message` with `actor = system`), at most 2 rounds. Switches `[agent] binding` and `[agent] binding_retries` (ADR-018); both on by default, lift not yet measured.
- **interface change**: `ClaimRef` gains `ValueRef` and `QuoteRef`; `Evidence` gains `values` and `sources`; `ReviewResult` gains `evidence`; checks receive rendered claim text; `value_match` also looks at non-literature tool results. Manifest runs gain `values` (bundle-spec regenerated). `run_turn` takes `binding` / `binding_retries` and returns `binding_retries`.

## 0.1.0 — 2026-11-17

First end-to-end version (ROADMAP v0.1).

- **runtime**: `Kernel` protocol with a guaranteed `ExecutionEnd`; `FakeKernel`; `JupyterKernel` over private IPC sockets, started in the project root; per-session `KernelManager`; output summaries.
- **provenance**: content-addressed store with hash verification; canonical DataFrame/AnnData hashing; manifest v0 from events.
- **policy**: permission grants by scope (deny by default without an asker); classification checks; secret redaction and `env:` references.
- **tools**: `ToolSpec`, single registry, `invoke()` chokepoint (every call ends in a result event); `python_execute`, `read_file`, `list_files`; external MCP tools wrapped as untrusted specs; the MCP exit.
- **mcp**: client, server, cassette record/replay; Open Targets official remote server and BioMCP connected.
- **models**: `pydantic-ai-slim[openai]`; provider/role config, classification-first routing with fallbacks, single conversion point, model cassettes; OpenAI (Responses API) and DeepSeek.
- **review**: deterministic checks `execution_evidence`, `value_match`, `citation_resolves`, `plan_complete`; model check `citation_supports`; claim extraction; review events and verification status. Measured in `docs/research/review-checks.md`.
- **agent**: stateless loop, context rebuilt from events each step.
- **cli**: `bq project`, `bq models`, `bq tools`, `bq run`, `bq review`, `bq sessions`, `bq mcp serve|record`, `bq db`.
- **decisions**: ADR-013 supersedes ADR-011 after the reviewer control experiment (`docs/research/reviewer-experiment.md`).
- **known gaps**: no sandbox (kernel code can reach the network); review sees only what tools retrieved in the session; see NEXT.md.

## 0.0.1 — 2026-11-13

Foundations: `core/` domain model, event store and projections, ADRs 001–012, eval corpus, reviewer experiment script.
