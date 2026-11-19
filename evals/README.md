# Evaluations

| Path | What | Costs money? |
|---|---|---|
| `questions/scenario-{a,b,c}.yaml` | 15 north-star questions with rubrics (PRODUCT.md §4). Run with `bq run` against real models and databases. | yes |
| `injected-errors/` | 24 hand-written cases (20 with injected errors, 4 clean controls); schema and error types in its README. Development set for `review/` deterministic checks and input to the reviewer experiment. | no |
| `review_eval.py` | Scores the deterministic review checks on the corpus (offline). `--heldout <worker...>` also scores cached model-written answers from the reviewer experiment. Results: `docs/research/review-checks.md`. A unit test pins the corpus result. | no |
| `binding_eval.py`, `binding-cases.yaml` | ADR-018 paired binding ablation on frozen source and kernel cases. Offline controls and replay of numeric mutations are available without a model. Results: `docs/research/binding-eval.md`. | only with `--live` |
| `binding_repair_eval.py`, `binding-repair-cases.yaml` | Paired retry-cap comparison starting from the same frozen tool evidence and faulty answer. Results: `docs/research/binding-repair-eval.md`. | yes |
| `harness_eval.py`, `harness_reference.R`, `harness-research-cases.yaml` | Kernel-only and full harness comparison on published experimental data, with independent numeric reference answers. | only with `--live` |
| `v02_controls_eval.py` | Runtime-control ablation using a local Ollama model and the published R datasets. Results in `docs/research/v02-runtime-controls.md`. | local model |
| `v02_demo.py` | PBMC 3k kernel and metformin literature acceptance. Fixed model replies, real inputs and tools. | public registries only |
| `v05_literature_eval.py` | Paired direct and delegated retrieval with synthetic records using local Ollama. Results: `docs/research/v05-literature-subtasks.md`. | local model |
| `v05_scenario_a.py` | IL23R target dossier, public source retrieval, semantic review and evidence-only export. | local model |
| `v05_target_validation.py` | Four scenario A cases with model-selected retrieval and a preselected-reference diagnostic. | local model |
| `v05_vision_eval.py` | Route a real kernel PNG to the native macOS Vision OCR model. | no |
| `v05_scenario_b.py` | GEO GSE96583 on a real local kernel, including QC sensitivity and portable replay. | no |
| `v05_source_handoff.py` | Paired off/on comparison for staging a tool result into a real kernel. | no |
| `reviewer_experiment.py` | ADR-011 control experiment, same-family vs cross-family reviewer (outcome: ADR-013). See module docstring; report in `docs/research/reviewer-experiment.md`. `BQ_EVAL_OFFLINE=1` re-scores from cache only. | yes, once |
| `results/` | JSON results and the response cache (git-ignored, local only). The `--heldout` scoring needs this cache. | |

Default CI uses offline tests only. Live outputs, including the original event stores, go under the ignored `evals/results/` directory when using the commands below.

## Binding ablation

```bash
uv run python evals/binding_eval.py
uv run python evals/binding_eval.py --live --worker deepseek:deepseek-v4-pro \
  --mode source --output evals/results/binding-source
uv run python evals/binding_eval.py --live --worker deepseek:deepseek-v4-pro \
  --mode kernel --output evals/results/binding-kernel
uv run python evals/binding_eval.py --probe-results evals/results/binding-kernel
```

The kernel cases require a Linux host with working bubblewrap and fail before model calls if it is missing. Use a new output directory for each run. Omit `--mode` to run both sets. Add `--worker` for each model and pin one claim extractor with `--reviewer` when comparing multiple workers. Credentials use the provider's environment reference.

An optional `--config` accepts BioQuorum's `[providers.<name>]` sections and a `[prices]` table mapping quoted model specs to three USD rates per million tokens in uncached-input, cached-input and output order. A missing rate is reported as unknown. All three arms use the same tools. The `default` arm reproduces the earlier retry setting of two rounds. See the report for the tested scope and the rubric limits.

For local models, use `--worker ollama:qwen3:4b-instruct` when Ollama runs on the same host. If Ollama runs on macOS while BioQuorum runs in OrbStack Linux, point `[providers.ollama].base_url` at the host endpoint, for example `"http://host.internal:11434/v1"`. Set `--reviewer deepseek:deepseek-v4-pro` to use the same claim extractor as the DeepSeek runs. That option sends the synthetic answers to DeepSeek and incurs reviewer API fees.

## Binding repair

```bash
uv run python -m evals.binding_repair_eval --live \
  --worker deepseek:deepseek-v4-pro --worker deepseek:deepseek-flash \
  --output evals/results/binding-repair
```

This uses the production feedback loop with a frozen replay kernel and source packet, so synthetic Python results need neither Jupyter nor bubblewrap. Use `--case` for a subset or the same `--config` format as the ablation to record prices. Results include provider failures, added calls and the final disposition of each target.

## v0.2 runtime controls

Run on Linux with bubblewrap and a reachable local Ollama instance containing the Qwen3 model.

```bash
Rscript --vanilla evals/harness_reference.R evals/results/harness-inputs
uv run python -m evals.v02_controls_eval --inputs evals/results/harness-inputs \
  --output evals/results/v02-controls --url http://localhost:11434/v1
```

## Research harness comparison

```bash
Rscript --vanilla evals/harness_reference.R evals/results/harness-inputs
uv run python -m evals.harness_eval --inputs evals/results/harness-inputs
uv run python -m evals.harness_eval --live --inputs evals/results/harness-inputs \
  --worker deepseek:deepseek-v4-pro --reviewer deepseek:deepseek-flash \
  --output evals/results/harness-research
```

R only exports the official datasets and computes a reference before the run. The model runs use Python in Linux bubblewrap. They see the same read-only observations and study note. The evaluator retains source hashes, separate worker and review cost, answers, and any answers that preceded binding feedback.
