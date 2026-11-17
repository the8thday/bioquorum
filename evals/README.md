# Evaluations

| Path | What | Costs money? |
|---|---|---|
| `questions/scenario-{a,b,c}.yaml` | 15 north-star questions with rubrics (PRODUCT.md §4). Run with `bq run` against real models and databases. | yes |
| `injected-errors/` | 24 hand-written cases (20 with injected errors, 4 clean controls); schema and error types in its README. Development set for `review/` deterministic checks and input to the reviewer experiment. | no |
| `review_eval.py` | Scores the deterministic review checks on the corpus (offline). `--heldout <worker...>` also scores cached model-written answers from the reviewer experiment. Results: `docs/research/review-checks.md`. A unit test pins the corpus result. | no |
| `reviewer_experiment.py` | ADR-011 control experiment, same-family vs cross-family reviewer (outcome: ADR-013). See module docstring; report in `docs/research/reviewer-experiment.md`. `BQ_EVAL_OFFLINE=1` re-scores from cache only. | yes, once |
| `results/` | JSON results and the response cache (git-ignored, local only). The `--heldout` scoring needs this cache. | |

Only `review_eval.py` (through `tests/unit/review/test_corpus.py`) runs in CI. Everything else calls real models; the response cache makes re-scoring free.
