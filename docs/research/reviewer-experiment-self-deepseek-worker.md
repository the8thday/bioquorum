# Reviewer experiment — mode `self`

Ran: 2026-11-17T00:16:10+00:00 · cases: 24 · worker: `deepseek:deepseek-v4-pro` · seed: bq-v0

| reviewer | same family as worker | injected | recall (strict) | recall (lenient) | precision | false positives | collateral flagged | clean cases flagged |
|---|---|---|---|---|---|---|---|---|
| `openai:gpt-5.5` | False | 31 | 93.5 | 96.8 | 90.7 | 4 | 9 | 0/4 |
| `deepseek:deepseek-v4-pro` | True | 31 | 93.5 | 96.8 | 90.7 | 4 | 9 | 0/4 |

## Recall by error type (strict, %)

| reviewer | value_mismatch | fabricated_citation | citation_unsupported | execution_unsupported | plan_incomplete | stat_method | overclaim_causal |
|---|---|---|---|---|---|---|---|
| `openai:gpt-5.5` | 84.6 | 100.0 | - | 100.0 | - | - | - |
| `deepseek:deepseek-v4-pro` | 84.6 | 100.0 | - | 100.0 | - | - | - |

Strict = right claim and right error type; lenient = right claim, any type. Precision = findings on genuinely bad claims / all findings.
Collateral = other claims that relied on a run removed by an `execution_unsupported` injection; flagging them is correct and is not counted as a false positive.
In `self` mode only `value_mismatch`, `fabricated_citation` and `execution_unsupported` are injected (programmatically), so other columns are `-`.
