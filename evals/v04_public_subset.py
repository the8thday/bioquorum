"""Prepare a fixed, small public BixBench/TableQA subset and score recorded answers."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bioquorum.core.hashing import sha256_bytes, sha256_file

ALLOW = {
    "bixbench-v1.5": {"bix-34-q5", "bix-34-q6"},
    "lab-bench-tableqa-v1": {
        "75147404-3707-4a19-93bb-6745ed00f0d4",
        "30d26305-f6de-4636-87a2-0fbaf6e05e66",
    },
}
SOURCES = {
    "bixbench-v1.5": "https://huggingface.co/datasets/futurehouse/BixBench/tree/v1.5",
    "lab-bench-tableqa-v1": "https://github.com/Future-House/LAB-Bench/tree/main/TableQA",
}
VERSIONS = {"bixbench-v1.5": "1.5", "lab-bench-tableqa-v1": "1.0"}
# LAB-Bench presents TableQA as multiple choice with this extra option.
UNSURE = "Insufficient information to answer the question"


def choices(uid: str, ideal: str, distractors: list[str]) -> list[str]:
    """Upstream multiple-choice options in a fixed order derived from the item ID only."""
    options = [ideal, *distractors]
    return [*sorted(options, key=lambda text: sha256_bytes((uid + text).encode())), UNSURE]


def sample(path: Path, benchmark: str) -> tuple[list[dict], dict[str, str]]:
    found, key = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        uid = str(row.get("question_id") if benchmark.startswith("bix") else row.get("id"))
        if uid not in ALLOW[benchmark]:
            continue
        if uid in key or str(row["version"]) != VERSIONS[benchmark]:
            raise ValueError("Unexpected revision or duplicate item: " + uid)
        found.append(
            {
                "id": uid,
                "benchmark": benchmark,
                "source": SOURCES[benchmark],
                "question": row["question"],
                "inputs": row.get("table-path") or [row["data_folder"]],
                **(
                    {"choices": choices(uid, row["ideal"], row["distractors"])}
                    if benchmark.startswith("lab-bench")
                    else {}
                ),
            }
        )
        key[uid] = row["ideal"]
    if key.keys() != ALLOW[benchmark]:
        raise ValueError("Upstream file does not contain the complete selected subset")
    return found, key


def equivalent(answer: object, reference: str, *, numeric: bool) -> bool:
    if answer is None:
        return False
    if numeric:
        try:
            value, expected = Decimal(str(answer).strip()), Decimal(reference)
            return value.is_finite() and abs(value - expected) <= Decimal("0.005")
        except InvalidOperation:
            return False
    return " ".join(str(answer).casefold().split()) == " ".join(reference.casefold().split())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bixbench", type=Path, required=True)
    parser.add_argument("--tableqa", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument(
        "--predictions", type=Path, help="JSON object mapping item ID to final answer"
    )
    args = parser.parse_args()
    tasks, refs, versions = [], {}, {}
    for benchmark, path in zip(ALLOW, (args.bixbench, args.tableqa), strict=True):
        rows, key = sample(path, benchmark)
        tasks += rows
        refs.update(key)
        versions[benchmark] = {"source": SOURCES[benchmark], "sha256": sha256_file(path)}
    args.questions.parent.mkdir(parents=True, exist_ok=True)
    args.questions.write_text(json.dumps({"sources": versions, "tasks": tasks}, indent=2) + "\n")
    if args.predictions:
        prediction = json.loads(args.predictions.read_text())
        results = {
            row["id"]: equivalent(
                prediction.get(row["id"]),
                refs[row["id"]],
                numeric=row["benchmark"].startswith("bix"),
            )
            for row in tasks
        }
        print(
            json.dumps(
                {"checked": len(results), "correct": sum(results.values()), "results": results}
            )
        )
    else:
        print(f"Prepared {len(tasks)} questions. No model scores computed.")


if __name__ == "__main__":
    main()
