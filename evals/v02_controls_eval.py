"""Compare each v0.2 runtime control with the same local model on published data."""

import argparse
import asyncio
from pathlib import Path

import yaml

from bioquorum.agent.budget import Budget
from bioquorum.models import ModelsConfig, Router

from .binding_eval import dump
from .harness_eval import CASES, Case, run_arm, validate_inputs

ARMS = ("baseline", "budget", "compaction", "stream")


async def run(inputs: Path, out: Path, url: str, model: str) -> int:
    cases = [Case.model_validate(c) for c in yaml.safe_load(CASES.read_text(encoding="utf-8"))]
    manifest = validate_inputs(cases, inputs)
    selected = [c for c in cases if c.id in ("plant-growth", "tooth-growth", "paired-sleep")]
    manifest.update(
        model=model,
        arms=ARMS,
        cases=[c.id for c in selected],
        budget_tokens=4500,
        budget_seconds=120,
        compact_at_bytes=3000,
        baseline_max_steps=4,
    )
    router = Router(
        ModelsConfig.from_dict(
            {
                "providers": {"ollama": {"base_url": url, "family": "qwen"}},
                "roles": {"worker": f"ollama:{model}"},
            }
        )
    )
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "manifest.json", manifest)
    rows = []
    for index, case in enumerate(selected):
        order = ARMS[index:] + ARMS[:index]
        for arm in order:
            row = await run_arm(
                out / case.id / arm,
                inputs,
                case,
                router,
                "full",
                {f"ollama:{model}": (0.0, 0.0, 0.0)},
                4,
                budget=Budget(tokens=4500, seconds=120) if arm == "budget" else None,
                compact_at_bytes=3000 if arm == "compaction" else None,
                stream=arm == "stream",
                seed_history=True,
                skip_review=True,
            )
            row.update(case=case.id, split=case.split, arm=arm)
            dump(out / case.id / arm / "result.json", row)
            rows.append(row)
            print(case.id, arm, row["error"] or row["rubric"], flush=True)
    dump(out / "results.json", {**manifest, "runs": rows})
    return int(any(row["error"] for row in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", required=True, help="OpenAI-compatible URL of a local Ollama")
    parser.add_argument("--model", default="qwen3:4b-instruct")
    args = parser.parse_args()
    return asyncio.run(run(args.inputs, args.output, args.url, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
