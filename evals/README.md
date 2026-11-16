# Evaluations

| Path | What |
|---|---|
| `questions/scenario-{a,b,c}.yaml` | 15 north-star questions with rubrics (PRODUCT.md §4). Run against real models and databases from v0.1. |
| `injected-errors/` | 24 hand-written cases (20 with injected errors, 4 clean controls). Fixtures for `review/` deterministic checks and for the reviewer experiment. |
| `reviewer_experiment.py` | ADR-011 control experiment: same-family vs cross-family reviewer. See module docstring. |
| `results/` | JSON results and response cache (git-ignored). |

Nothing here runs in CI. Everything calls real models and costs money; the response cache makes re-scoring free.
