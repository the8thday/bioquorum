# Reviewer experiment — mode `self`

Ran: 2026-11-17T00:16:10+00:00 · cases: 24 · worker: `openai:gpt-6.1-sol` · seed: bq-v0

| reviewer | same family as worker | injected | recall (strict) | recall (lenient) | precision | false positives | collateral flagged | clean cases flagged |
|---|---|---|---|---|---|---|---|---|
| `openai:gpt-6.1-sol` | True | 31 | 83.9 | 96.8 | 100.0 | 0 | 17 | 0/4 |
| `deepseek:deepseek-v4-pro` | False | 31 | 87.1 | 96.8 | 97.9 | 1 | 17 | 0/4 |

## Recall by error type (strict, %)

| reviewer | value_mismatch | fabricated_citation | citation_unsupported | execution_unsupported | plan_incomplete | stat_method | overclaim_causal |
|---|---|---|---|---|---|---|---|
| `openai:gpt-6.1-sol` | 50.0 | 100.0 | - | 100.0 | - | - | - |
| `deepseek:deepseek-v4-pro` | 60.0 | 100.0 | - | 100.0 | - | - | - |

Strict = right claim and right error type; lenient = right claim, any type. Precision = findings on genuinely bad claims / all findings.
Collateral = other claims that relied on a run removed by an `execution_unsupported` injection; flagging them is correct and is not counted as a false positive.
In `self` mode only `value_mismatch`, `fabricated_citation` and `execution_unsupported` are injected (programmatically), so other columns are `-`.
