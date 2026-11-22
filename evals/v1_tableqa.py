"""Prepare and independently score the frozen v1.0 single-table reading cohort."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.v04_public_subset import ALLOW, choices, equivalent

from bioquorum.core.hashing import sha256_file, sha256_text

BENCHMARK = "lab-bench-tableqa-v1"
REVISION = "998a8e0a40cf116c80e1b0e7a805ebb5fb9fa838"
SOURCE = f"https://github.com/Future-House/LAB-Bench/tree/{REVISION}/TableQA"
QUESTIONS_SHA256 = "3d6ed45cedf0ec4aaba56b925a816be66efd620ba4f403d7d853a6a823198915"
IMAGE_SET_SHA256 = "787ae611326d55fd2f297d0199b657ed9473eeb9bd2367e52b4923ff1312f8bc"
COHORT_SIZE = 32


def select_tasks(rows: list[dict], count: int) -> tuple[list[dict], dict[str, str]]:
    pilot = [r for r in rows if r["id"] in ALLOW[BENCHMARK]]
    papers = {r["source"] for r in pilot}
    tables = {path for r in pilot for path in r["table-path"]}
    tasks = []
    key = {}
    for row in sorted(rows, key=lambda r: sha256_text("bq-v1-tableqa/" + r["id"])):
        paths = row["table-path"]
        if len(paths) != 1 or paths[0] in tables or row["source"] in papers:
            continue
        uid = row["id"]
        tasks.append(
            {
                "id": uid,
                "benchmark": BENCHMARK,
                "source": SOURCE,
                "question": row["question"],
                "inputs": paths,
                "choices": choices(uid, row["ideal"], row["distractors"]),
            }
        )
        key[uid] = row["ideal"]
        papers.add(row["source"])
        tables.add(paths[0])
        if len(tasks) == count:
            return tasks, key
    raise ValueError("Too few distinct papers and tables")


def tally(key: dict[str, str], predictions: dict) -> dict:
    if predictions.keys() - key.keys():
        raise ValueError("Predictions contain unknown item IDs")
    items = {}
    for uid, reference in key.items():
        if uid not in predictions:
            status = "missing"
        elif predictions[uid] is None:
            status = "unanswered"
        elif equivalent(predictions[uid], reference, numeric=False):
            status = "correct"
        else:
            status = "wrong"
        items[uid] = status
    return {"total": len(key), "correct": list(items.values()).count("correct"), "items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", type=Path, required=True, help="Pinned upstream TableQA directory"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--questions", type=Path, help="Write model-facing tasks for the runner")
    action.add_argument("--predictions", type=Path, help="Score a saved predictions.json")
    args = parser.parse_args()
    source = args.data / "tableqa-v1-public.jsonl"
    if sha256_file(source) != QUESTIONS_SHA256:
        parser.error("Question file differs from the frozen revision")
    tasks, key = select_tasks(
        [json.loads(line) for line in source.read_text().splitlines()], COHORT_SIZE
    )
    if args.predictions:
        print(json.dumps(tally(key, json.loads(args.predictions.read_text())), indent=2))
        return
    images = {t["inputs"][0]: sha256_file(args.data / t["inputs"][0]) for t in tasks}
    fingerprint = sha256_text(json.dumps(images, sort_keys=True))
    if fingerprint != IMAGE_SET_SHA256:
        parser.error("Table images differ from the frozen revision")
    for task in tasks:
        task["image_sha256"] = images[task["inputs"][0]]
    payload = {
        "sources": {BENCHMARK: {"source": SOURCE, "sha256": QUESTIONS_SHA256}},
        "tasks": tasks,
    }
    args.questions.parent.mkdir(parents=True, exist_ok=True)
    args.questions.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(tasks)} questions with verified images.")


if __name__ == "__main__":
    main()
