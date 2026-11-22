# Changelog

## v0.12 · 开发中

- 0.12.1 项目任务列表、服务重启检测、原轮次恢复和未确认工具结果的人工核对。
- 0.12.2 项目待办入口和可选浏览器通知。
- 0.12.3–0.12.4 通用并行子任务与 Markdown 专家，各自按项目启用。每项有独立事件流、权限上限、父会话共享预算和可核对的来源。[真实模型对照](docs/research/v12-scale.md)
- 0.12.5 计划草稿、逐角色模型评测、可调整的备用模型链与回退事件。
- 0.12.6 长会话首屏分页、增量证据绑定、游标续传与审计页。[工程验收](docs/release/v0.12-acceptance.md)


Extension interfaces (checks, connectors, providers, skills) may change before v1.0; every such change is recorded here (ARCHITECTURE.md §7).

## Unreleased · Artifact viewers and domain Skills (v0.11 development branch)

- Inspect and cite a versioned table cell, figure sample, report passage, FASTA interval, structure chain or residue, molecule record, or BED interval from the local workbench.
- Load Mol*, RDKit.js and igv.js on demand. Replay a saved selection and compare a recomputed figure with its original. Python and R can record the sample mapping alongside the figure.
- Enable four domain Skills per project and inspect declared external Skill licenses. All 12 paired held-out questions received the correct choice with the feature on and off. See the [evaluation](docs/research/v11-domain-skills.md).
- The [engineering acceptance record](docs/release/v0.11-acceptance.md) covers steps 0.11.1–0.11.7. A [public trastuzumab–HER2 research line](docs/research/v11-scenarios.md) now links sequence annotations, Mol* selections, primary literature and reproducible interface counts. The author accepted the scientific conclusions for 0.11.8 on 2026-11-22.

## Unreleased · Research lines (v0.10)

- Group sessions under a research line and inspect the latest recorded status of its claims with `bq program list` and `bq program show`.
- Register uploaded files on the workbench Materials page, record the reason for a correction or withdrawal, and download each version by its recorded content hash.
- Trace a material version to calculations, values, figures, claims, decisions and previous exports. Follow a claim back to the input it used.
- Preview a correction, recompute the original cells in isolated kernels and compare recorded values side by side. Preserve pending interpretations and the history of researcher decisions.
- Download a research-line handoff summary and a bundle containing member sessions and versioned materials.
- Enable audited research-line background retrieval per project. See the [paired evaluation](docs/research/v10-program-context.md) and the [scored human handoff comparison](docs/research/v10-day2-comparison.md).

## Unreleased · Bilingual (v0.9)

- Add project-local IRkernel support alongside Python, per-session workbench kernels and R named-value declarations.
- Record declared file handoffs between languages and verify their hashes during replay. Export language-specific notebooks and portable bilingual research bundles.
- Show Python and R package inventories, environment lock hashes and provisioning instructions in project settings.
- Add a donor-paired DESeq2 scenario and two R development questions. See the [acceptance record](docs/release/v0.9-acceptance.md) for the live model, sandbox, cross-platform and independent replay results. The author accepted the scenario B scientific conclusions on 2026-11-21.

## v0.8 · Coverage

- New projects start with the evaluated Recommended profile. Evidence binding is enabled with zero retries. Project settings can switch profiles or customize individual mechanisms.

- Inspect actual query outcomes and evidence gaps under an answer and in its research bundle.
- Enable scoped absence checks, first-pass discussion and built-in evidence-category Skills independently.
- Compare the frozen 90-question bank using paired research profiles and a shared evaluation budget.
- Report held-out coverage, cost, elapsed time, and observed feature use from 128 completed ablation pairs.

## v0.7.0.dev0 · Workbench

- Create and switch local projects in the sidebar. Session actions share a menu, and the usage inspector shows intact values and expandable role totals.

- Ship the React and TypeScript workbench as prebuilt assets in the Python package.
  The offline example, evidence inspection, notes and verification use the existing record API.

- Search sessions by title or research question, rename and archive them. Keep the
  composer available across turns and show generation progress in the record.

- Select a classification-permitted worker for each turn and show the actual model on each answer.

- Inspect projected model usage by scope and role in the workbench and with `bq usage SESSION`.

- Inspect the estimated context occupancy, loaded material and saved summaries, compact the conversation or fork it.

- Configure model roles, prices, context windows, research options, permissions and budgets in project settings. The workbench and CLI apply the same recorded changes. Inspect kernel packages, connector availability and environment key presence.

- Attach a selected finding, calculation or excerpt to a follow-up question and recover its recorded provenance.

- Upload project files, attach recorded versions to questions, preview CSV and TSV pages, inspect and download session artifacts, and export kernel cells as a Jupyter notebook.

- Show first-run model configuration and kernel or connector availability. Open the independent offline example directly from an unconfigured project.

- Resize either sidebar and open figures, tables and reports beside the conversation. Switch result tabs while keeping the follow-up draft.
- Install and manage project Skills and MCP connectors from the sidebar. Use the composer for attachments, quoted follow-ups and common project settings.

## v0.6.0.dev0

- Show a read-only, paginated session audit log and data classification in the workbench.
- Add opt-in macOS kernel isolation and offline stdio MCP process isolation.
- Add a local HTTPS egress proxy and route configured tool and provider HTTP traffic through it.
- Add a non-root Docker CLI image definition and private deployment guidance.
- Fix: `import bq` in a kernel returns the injected value helper instead of failing; the
  blind replication prompt says `bq` is predefined.
- Fix: blind replication runs record the project's environment lock hash.
- Add pandas to the v0.4 pixi example; add a Linux bubblewrap MCP isolation integration
  test, a remote vision chart check and a public benchmark runner.

## v0.5 development

- Add opt-in per-project Agent Skills with metadata discovery and permission-checked access
  to instructions and text resources.
- Add a researcher-controlled project note backed by events. Model drafts are scoped to
  the originating session and stale edits cannot replace newer revisions.
- Add opt-in literature subtasks with a fresh context, a retrieval-tool allowlist,
  shared turn budgets and verbatim evidence linked to the original tool result.
- Export evidence-only research bundles and generate an editable Methods draft from the archive.
- Review semantic support for bound source aliases. Keep claims at their current level when a verdict is missing.
- Add opt-in source staging for replayable kernel inputs and project- and model-scoped image attachments.


## 0.4.0 (unreleased)

- Replay recorded Python kernel cells with captured inputs in a fresh kernel, compare
  outputs and advance eligible claims through reproduction.
- Add an opt-in blind replication run with a separate session, an estimated budget,
  isolated inputs, preregistered tolerances and a comparison record.
- Export a portable manifest v1 bundle with an independent, hash-verifying replay runner.
- Observe optional pandas, AnnData and Matplotlib file I/O; support provisioned pixi
  kernels and an opt-in method review check.


## 0.3.0 (unreleased)

- Add the local FastAPI workbench with an event-backed research record, a reconnecting
  activity stream, and evidence drawers for recorded figures, named values, and citations.
- Support opt-in research plans, approval before execution, auditable plan deviations,
  one-time browser permissions, interruption, and session forks.
- Ship a static browser client and a metformin walkthrough that runs offline with
  a model cassette and FakeKernel. Add the `bioquorum` launcher and `bq workbench`.
- Leave grounded claims at their current level when a requested review check cannot finish.


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
- **agent**: `[agent] binding` defaults to on. `[agent] binding_retries` defaults to 0 after the first ablation (ADR-018). Set it to 1 or 2 to send unresolved bindings back through a `user_message` with `actor = system`. See [the evaluation](docs/research/binding-eval.md).
- **models**: DeepSeek Chat Completions uses the DeepSeek profile to preserve existing reasoning and include the required empty field on tool-call turns without recorded reasoning.
- **agent**: optional per-turn token, estimated USD and elapsed-time budgets, compacted event-backed context and live CLI generation progress. The answer renderer labels unbound numbers and citations even when review is off.
- **usage**: configure per-model input and output prices in TOML. CLI reports answer and review usage and distinguishes missing prices from configured zero cost.
- **review**: `--online-citations` checks session-retrieved PMIDs and DOIs against their public registries. Consecutive kernel display values retain their boundaries in numeric review.
- **tools**: external MCP connections recover on subsequent calls after transport failure. A failed call stays in the audit trail without resending it. Python runs can record the content-addressed snapshots of declared input files.
- **storage**: packaged Alembic migrations add `model_calls.cost_known` when opening legacy SQLite databases.
- **acceptance**: reproducible PBMC 3k QC and metformin evidence-review demos, and a local-model runtime-control ablation. See [the v0.2 walkthrough](docs/demo-v0.2.md).
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
