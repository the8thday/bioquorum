"""Read saved results without starting a model or modifying the evaluation ledger."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from run import ABLATIONS, CONFIRMATION_CASES, ROOT, Case, ablation_schedule, aggregate, load_cases


def read_row(root: Path, case: Case, profile: str) -> dict | None:
    path = root / case.split / profile / case.id / "result.json"
    if not path.is_file():
        return None
    row = json.loads(path.read_text())
    # Early protocol-1 rows only stored known model usage, even for ambiguous attempts.
    if row["status"] != "ok" and "tool_requests" not in row:
        row["cost_usd"] = None
    return row


def baseline(root: Path, cases: list[Case]) -> dict:
    result = {}
    for profile in ("minimal", "current"):
        rows = [r for case in cases if (r := read_row(root, case, profile)) is not None]
        scored = [r for r in rows if r["status"] == "ok"]
        summary = aggregate(rows)
        summary.update(
            planned=len(cases),
            missing=len(cases) - len(rows),
            scored=len(scored),
            covered=sum(r["covered"] for r in scored),
            scored_criteria=sum(r["expected"] for r in scored),
        )
        if len(rows) < len(cases):
            summary["coverage"] = None
        result[profile] = summary
    return result


def paired(root: Path, cases: list[Case]) -> dict:
    pairs = ablation_schedule(cases)
    return {
        profile: paired_profile(root, [c for c, arm in pairs if arm == profile], profile)
        for profile in ABLATIONS
    }


def confirmation(root: Path, cases: list[Case]) -> dict:
    selected = [c for c in cases if c.split == "heldout" and c.id in CONFIRMATION_CASES]
    return paired_profile(root, selected, "confirmation")


def paired_profile(root: Path, cases: list[Case], profile: str) -> dict:
    treated, control = [], []
    completed = incomplete = missing = 0
    for case in cases:
        on, off = read_row(root, case, profile), read_row(root, case, "current")
        if on is None:
            missing += 1
            continue
        if on["status"] != "ok":
            incomplete += 1
            continue
        completed += 1
        if off and off["status"] == "ok":
            treated.append(on)
            control.append(off)
    return {
        "planned_pairs": completed + incomplete + missing,
        "progress": {
            "completed": completed,
            "incomplete": incomplete,
            "missing": missing,
            "paired": len(treated),
        },
        "ids": [r["id"] for r in treated],
        "on": aggregate(treated),
        "current": aggregate(control),
        "sessions_requesting_clarification": sum(bool(r.get("clarifications")) for r in treated),
    }


if __name__ == "__main__":
    cases = [c for c in load_cases() if c.split == "heldout"]
    ledger = ROOT / "spending.sqlite"
    reserved = None
    if ledger.is_file():
        with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as db:
            reserved = (
                db.execute("SELECT COALESCE(SUM(amount),0) FROM charges").fetchone()[0] / 1_000_000
            )
    print(
        json.dumps(
            {
                "budget_used_or_reserved_usd": reserved,
                "baseline": {
                    s: baseline(ROOT, [c for c in cases if c.scenario == s]) for s in "ABC"
                },
                "overall": baseline(ROOT, cases),
                "ablations": paired(ROOT, cases),
                "confirmation": confirmation(ROOT, cases),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
