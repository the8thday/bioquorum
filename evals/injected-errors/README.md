# Injected-error corpus

25 synthetic cases for measuring how well the review layer (ARCHITECTURE.md section 6.5) catches errors.
Each case pairs an agent answer with the evidence it should be judged against. 21 cases contain 1 to 3
deliberately injected errors. 4 cases (ie-021 to ie-024) are clean controls for measuring false positives.

Everything is fictional: DOIs use the prefix `10.0000/bq.`, GEO accessions use `GSE9xxxxxx`, trial IDs use
`NCT0999xxxx`, and author names are invented. Gene, drug and disease names are real, so the biology reads as plausible.
Do not treat any number in this corpus as a real finding.

## Schema

| Field | Meaning |
|---|---|
| `id`, `scenario`, `title`, `question` | Case identity. `scenario` is A (target dossier), B (omics in a kernel) or C (evidence review). |
| `evidence.runs[]` | Kernel runs: `run_id`, `status` (succeeded or failed), `code`, `stdout`. |
| `evidence.citations[]` | Sources: `key`, `doi`, `title`, `authors`, `year`, `abstract`. |
| `evidence.plan[]` | Approved plan steps. |
| `answer` | The assistant's final answer. It cites sources with `[c1]`-style keys. |
| `claims[]` | The answer split into checkable claims: `index`, `text`, `kind` (numeric, citation, execution, causal, plan). |
| `injected_errors[]` | Ground truth: `claim_index`, `type`, `description`, optional `also_affects` (other claims the same error makes false, e.g. "all planned steps are complete" when a step was skipped; flagging them is correct, missing them is not penalised). Empty for clean controls. |

The answer must be judged only against `evidence`. Apart from the injected errors, every case is internally consistent.

## Error types

| Type | Matching check | Definition |
|---|---|---|
| `value_mismatch` | `value_match` | A number in the answer differs from the one in a run's stdout or an abstract. This includes the wrong unit and the wrong group or endpoint. |
| `fabricated_citation` | `citation_resolves` / `citation_supports` | The answer cites a key missing from the evidence, or attributes a finding to an unrelated source. |
| `citation_unsupported` | `citation_supports` | The source is on topic but does not support the specific claim (mouse vs patients, different endpoint, a subgroup generalised to everyone). |
| `execution_unsupported` | `execution_evidence` | The answer claims a computation for which no run exists, or whose run failed. |
| `plan_incomplete` | `plan_complete` | An approved plan step was skipped, but the answer implies it was done. |
| `stat_method` | `method_sanity` | A statistical error visible in the code or output: no multiple-testing correction, n=2 per group, cells treated as replicates. |
| `overclaim_causal` | `citation_supports` / `method_sanity` | The evidence is associative, but the answer uses causal language. |

## Cases

| id | scenario | topic | injected error types |
|---|---|---|---|
| ie-001 | A | IL23R in IBD | value_mismatch, citation_unsupported |
| ie-002 | A | TYK2 in psoriasis | fabricated_citation |
| ie-003 | A | JAK1 in atopic dermatitis | execution_unsupported, plan_incomplete |
| ie-004 | A | TL1A (TNFSF15) in IBD | value_mismatch, overclaim_causal |
| ie-005 | A | KRAS G12C in NSCLC | fabricated_citation, value_mismatch |
| ie-006 | A | PTPN22 in RA | stat_method |
| ie-007 | A | PCSK9 and LDL cholesterol | value_mismatch, execution_unsupported |
| ie-008 | B | PBMC QC and doublets | value_mismatch, execution_unsupported |
| ie-009 | B | Pseudobulk DE, UC epithelium | stat_method |
| ie-010 | B | Multi-donor PBMC composition | plan_incomplete, value_mismatch |
| ie-011 | B | Bulk RNA-seq, TGFBR1 inhibitor | stat_method, overclaim_causal |
| ie-012 | B | TIL CD8 T cells, anti-PD-1 response | stat_method, value_mismatch, execution_unsupported |
| ie-013 | C | Metformin and colorectal cancer | overclaim_causal |
| ie-014 | C | Semaglutide in MASH | value_mismatch, fabricated_citation |
| ie-015 | C | JAK inhibitors in alopecia areata | citation_unsupported |
| ie-016 | C | Rapamycin and ageing | value_mismatch, citation_unsupported |
| ie-017 | C | SGLT2 inhibitors in HFrEF | fabricated_citation, plan_incomplete |
| ie-018 | C | Colchicine in coronary disease | value_mismatch, execution_unsupported |
| ie-019 | C | Statins and Alzheimer's disease | overclaim_causal, fabricated_citation, value_mismatch |
| ie-020 | C | Dupilumab in COPD | citation_unsupported |
| ie-021 | A | HSD17B13 in liver disease | none (clean) |
| ie-022 | B | pbmc3k standard workflow | none (clean) |
| ie-023 | C | Finerenone in diabetic kidney disease | none (clean) |
| ie-024 | C | BTK inhibitors in MS | none (clean) |
| ie-025 | C | Scoped registry search | unsupported_absence |

Totals: 37 injected errors, as follows: value_mismatch 11, fabricated_citation 5, execution_unsupported 5,
citation_unsupported 4, stat_method 4, overclaim_causal 4, plan_incomplete 3, unsupported_absence 1.

## Scoring notes

- Match a finding to an injected error by `claim_index`. A reviewer that flags the right claim under a closely related type
  (for example `execution_unsupported` vs `plan_incomplete` in ie-012) should be reported separately from an exact type match.
- Report the deterministic checks and the model-based checks separately, as section 6.5 requires.
