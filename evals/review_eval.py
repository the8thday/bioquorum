"""Score the deterministic review checks on the injected-error corpus (ARCHITECTURE.md §6.8).

No models, no network. Usage::

    uv run python evals/review_eval.py            # summary
    uv run python evals/review_eval.py --details  # every finding
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from bioquorum.core.enums import ClaimKind
from bioquorum.review.checks import Finding, deterministic_checks
from bioquorum.review.checks.absence import unsupported_absence
from bioquorum.review.evidence import ClaimInput, Evidence

CORPUS = Path(__file__).resolve().parent / "injected-errors"
CHECK_FOR = {
    "value_mismatch": "value_match",
    "fabricated_citation": "citation_resolves",
    "execution_unsupported": "execution_evidence",
    "unsupported_absence": "negative_search_scope",
    "plan_incomplete": "plan_complete",
}


def load_cases() -> list[dict[str, Any]]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(CORPUS.glob("ie-*.yaml"))]


def claims_of(case: dict[str, Any]) -> list[ClaimInput]:
    return [ClaimInput(c["index"], c["text"], ClaimKind(c["kind"])) for c in case["claims"]]


async def run_case(case: dict[str, Any]) -> list[Finding]:
    evidence, claims = Evidence.from_case(case), claims_of(case)
    out: list[Finding] = []
    for check in deterministic_checks():
        out.extend(await check.run(claims, evidence))
    return out


async def score() -> dict[str, Any]:
    caught: dict[str, list[str]] = defaultdict(list)
    missed: dict[str, list[str]] = defaultdict(list)
    false_pos: list[str] = []
    clean_flagged: list[str] = []
    details: list[str] = []
    for case in load_cases():
        findings = await run_case(case)
        injected = case.get("injected_errors") or []
        bad = {e["claim_index"] for e in injected} | {
            i for e in injected for i in e.get("also_affects", [])
        }
        for e in injected:
            check = CHECK_FOR.get(e["type"])
            if check is None:
                continue
            tag = f"{case['id']}#{e['claim_index']}"
            hit = any(f.claim_index == e["claim_index"] and f.check == check for f in findings)
            (caught if hit else missed)[e["type"]].append(tag)
        for f in findings:
            tag = f"{case['id']}#{f.claim_index} {f.check}: {f.evidence}"
            details.append(tag)
            if f.claim_index not in bad:
                false_pos.append(tag)
        if not injected and findings:
            clean_flagged.append(case["id"])
    n_claims = sum(len(c["claims"]) for c in load_cases())
    return {
        "caught": dict(caught),
        "missed": dict(missed),
        "false_positives": false_pos,
        "clean_flagged": clean_flagged,
        "n_claims": n_claims,
        "details": details,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", action="store_true")
    ap.add_argument(
        "--heldout",
        nargs="*",
        metavar="WORKER",
        help="also score cached model-written answers, e.g. deepseek:deepseek-v4-pro",
    )
    args = ap.parse_args(argv)
    if args.heldout is not None:
        sample = yaml.safe_load((CORPUS.parent / "questions/v08/absence-heldout.yaml").read_text())
        positives = sum(r["unsupported"] for r in sample)
        flagged = [r for r in sample if unsupported_absence(r["text"], {"queries": []})]
        caught = sum(r["unsupported"] for r in flagged)
        print(f"Model-written absence sample: recall {caught}/{positives}; false positives "
              f"{len(flagged)-caught}/{len(sample)-positives} negative controls")
        for worker, h in asyncio.run(score_heldout(args.heldout or ["ollama:qwen3:4b-instruct"])).items():
            got, tot = sum(h["caught"].values()), sum(h["total"].values())
            per = ", ".join(f"{t} {h['caught'].get(t, 0)}/{n}" for t, n in h["total"].items())
            print(
                f"held-out {worker}: caught {got}/{tot} ({per}); "
                f"false positives {len(h['false_positives'])} of {h['n_claims']} claims"
            )
            if args.details:
                for fp in h["false_positives"]:
                    print("  FP", fp)
        return
    r = asyncio.run(score())
    total_c = sum(len(v) for v in r["caught"].values())
    total_m = sum(len(v) for v in r["missed"].values())
    print(
        f"deterministic checks: caught {total_c}/{total_c + total_m} injected errors of their types"
    )
    for t in CHECK_FOR:
        c, m = r["caught"].get(t, []), r["missed"].get(t, [])
        print(f"  {t:24s} {len(c)}/{len(c) + len(m)}  missed: {', '.join(m) or '-'}")
    print(
        f"false positives: {len(r['false_positives'])} of {r['n_claims']} claims; clean cases flagged: {r['clean_flagged'] or 'none'}"
    )
    for fp in r["false_positives"]:
        print("  FP", fp)
    if args.details:
        for d in r["details"]:
            print("  ", d)


# ----------------------------------------------------------------- held-out check


async def score_heldout(workers: list[str], seed: str = "bq-v0") -> dict[str, Any]:
    """Model-written answers from the reviewer experiment (cached, offline), with the same
    programmatic injections. The rules were not tuned on these, so this measures how
    well they generalise to other writing styles."""
    import os
    import random

    os.environ.setdefault("BQ_EVAL_OFFLINE", "1")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import reviewer_experiment as rx  # type: ignore[import-not-found]

    out: dict[str, Any] = {}
    clean_ids = {"ie-021", "ie-022", "ie-023", "ie-024"}
    for worker in workers:
        llm = rx.LLM(rx.ModelSpec.parse(worker))
        caught: dict[str, int] = defaultdict(int)
        total: dict[str, int] = defaultdict(int)
        fps: list[str] = []
        n_claims = 0
        for case in rx.load_corpus(limit=24):  # frozen historical worker experiment
            claims = rx.worker_answer(llm, case)
            rng = random.Random(f"{seed}:{case['id']}")
            n_errors = 0 if case["id"] in clean_ids else rng.choice([1, 1, 2])
            mclaims, mevidence, injected = rx.inject(claims, case["evidence"], rng, n_errors)
            mcase = {**case, "evidence": mevidence, "answer": " ".join(c["text"] for c in mclaims)}
            evidence = Evidence.from_case(mcase)
            cin = [
                ClaimInput(
                    c["index"],
                    c["text"],
                    ClaimKind(c.get("kind") or "numeric"),
                    run_refs=tuple(c.get("run_ids") or ()),
                )
                for c in mclaims
            ]
            findings: list[Finding] = []
            for check in deterministic_checks():
                findings.extend(await check.run(cin, evidence))
            bad = {e["claim_index"] for e in injected} | {
                i for e in injected for i in e.get("collateral", [])
            }
            for e in injected:
                total[e["type"]] += 1
                if any(
                    f.claim_index == e["claim_index"] and f.check == CHECK_FOR[e["type"]]
                    for f in findings
                ):
                    caught[e["type"]] += 1
            fps += [
                f"{case['id']}#{f.claim_index} {f.check}: {f.evidence}"
                for f in findings
                if f.claim_index not in bad
            ]
            n_claims += len(mclaims)
        out[worker] = {
            "caught": dict(caught),
            "total": dict(total),
            "false_positives": fps,
            "n_claims": n_claims,
        }
    return out


if __name__ == "__main__":
    sys.exit(main())
