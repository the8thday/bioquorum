"""Paired local reviewer study. Launches and cleans up only its own Ollama service."""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import time
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

from v04_judgment_cases import cases

MODELS = ("qwen3:4b-instruct", "llama3.2:3b")


def server_request(port, path, body=None, timeout=45):
    req = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def chat(port, model, system, rows):
    result = server_request(
        port,
        "/api/chat",
        {
            "model": model,
            "stream": False,
            "format": "json",
            "think": False,
            "keep_alive": "5m",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(rows)},
            ],
            "options": {"temperature": 0, "num_predict": 900, "num_ctx": 4096},
        },
    )
    return json.loads(result["message"]["content"])


REWRITE = (
    "Paraphrase both statements of every row in one brief sentence each. Preserve the study design, "
    "all numbers, strength of the inference, and which statement is true. Preserve false statements "
    'as false, without correcting them. Output {"items":[{"id":"...","good":"...","bad":"..."}]}.'
)
JUDGE = (
    "For each item decide whether the claim has a method error, is unsupported by its source or "
    "makes a causal inference that the design cannot establish. Flag only a material error. "
    "Judge the claim using the supplied evidence. "
    'Return {"items":[{"id":"...","error":true}]} with exactly one item per claim.'
)


def batches(rows, size=5):
    return (rows[i : i + size] for i in range(0, len(rows), size))


def fresh_cases(port, model, frozen):
    generated = {}
    for batch in batches(frozen):
        try:
            payload = chat(port, model, REWRITE, batch)
            for row in payload.get("items", []):
                identifier = row.get("id")
                if (
                    identifier in {x["id"] for x in batch}
                    and all(isinstance(row.get(k), str) and row[k].strip() for k in ("good", "bad"))
                    and row["good"] != row["bad"]
                ):
                    generated[identifier] = row
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return generated


def compare(port, output):
    frozen = list(cases())
    rng = random.Random(424)
    authors, predictions, misses = {}, [], []
    for author in MODELS:
        authors[author] = fresh_cases(port, author, frozen)
        trials = []
        for row in frozen:
            generated = authors[author].get(row["id"])
            if not generated:
                misses.append({"author": author, "id": row["id"]})
                continue
            for polarity in ("good", "bad"):
                trials.append(
                    {
                        "id": row["id"] + "-" + polarity,
                        "evidence": row["evidence"],
                        "claim": generated[polarity],
                    }
                )
        rng.shuffle(trials)
        for reviewer in MODELS:
            for batch in batches(trials, size=10):
                try:
                    raw = chat(port, reviewer, JUDGE, batch)
                    choices = {
                        r["id"]: r["error"]
                        for r in raw.get("items", [])
                        if isinstance(r, dict) and isinstance(r.get("error"), bool) and "id" in r
                    }
                except (ValueError, OSError, KeyError, TypeError):
                    choices = {}
                for row in batch:
                    predictions.append(
                        {
                            "author": author,
                            "reviewer": reviewer,
                            "id": row["id"],
                            "error": choices.get(row["id"]),
                        }
                    )
            print(f"Finished {reviewer} reviewing {author}", flush=True)
            output.write_text(
                json.dumps(
                    {"generated": authors, "predictions": predictions, "missed_generation": misses},
                    indent=2,
                )
                + "\n"
            )
    counts: Counter = Counter()
    for row in predictions:
        group = "same" if row["author"] == row["reviewer"] else "cross"
        category, _, polarity = row["id"].split("-")
        guess = row["error"]
        counts[group, category, polarity, str(guess)] += 1
    return {"|".join(k): v for k, v in sorted(counts.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {
        **os.environ,
        "OLLAMA_HOST": f"127.0.0.1:{port}",
        "OLLAMA_MODELS": str(args.models_dir),
        "OLLAMA_MAX_LOADED_MODELS": "2",
    }
    with args.output.with_suffix(".server.log").open("wb") as log:
        proc = subprocess.Popen(["ollama", "serve"], env=environment, stdout=log, stderr=log)
        try:
            until = time.monotonic() + 15
            while True:
                try:
                    installed = {
                        m["name"] for m in server_request(port, "/api/tags", timeout=1)["models"]
                    }
                    if not set(MODELS).issubset(installed):
                        raise ValueError("The declared local models are not installed")
                    break
                except OSError:
                    if proc.poll() is not None or time.monotonic() > until:
                        raise
                    time.sleep(0.2)
            print(json.dumps(compare(port, args.output), indent=2))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    main()
