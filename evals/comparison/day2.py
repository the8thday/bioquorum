"""Prepare separate, local second-day packets from both frozen B-29 day-one records."""

import argparse
import asyncio
import csv
import io
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from bioquorum.application import artifacts, materials, program_record, programs, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Classification
from bioquorum.core.hashing import sha256_bytes

CASE = "q-B-29"
INPUT_HASH = "88a704e32eb6bd181db4a2b3c24cd5783ce72461555b19c0f994c56ff9e9114f"
DECISION = (
    "Use the pooled control and stimulated monocyte summaries in the internal descriptive report."
)
FORM = """participant_code,arm,order,stage,elapsed_seconds,response_or_evidence
,,,baseline,,
,,,impact,,
,,,revision,,
,,,decision_and_boundaries,,
,,,model_and_usage,,
,,,assistance_or_prior_exposure,,
"""


def correction(data: bytes) -> bytes:
    rows = csv.DictReader(io.StringIO(data.decode()))
    assert rows.fieldnames
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=rows.fieldnames)
    writer.writeheader()
    for row in rows:
        if row["donor"] == "D3":
            row["condition"] = {"control": "stim", "stim": "control"}[row["condition"]]
        writer.writerow(row)
    return output.getvalue().encode()


def counts(data: bytes) -> dict:
    by_sample: dict[tuple[str, str], list[int]] = {}
    for row in csv.DictReader(io.StringIO(data.decode())):
        cell = by_sample.setdefault((row["donor"], row["condition"]), [0, 0])
        cell[0] += int(row["cell_type"] == "monocyte")
        cell[1] += 1
    totals = {}
    for condition in ("control", "stim"):
        rows = [v for (_, c), v in by_sample.items() if c == condition]
        mono, total = map(sum, zip(*rows, strict=True))
        totals[condition] = {"monocytes": mono, "cells": total, "fraction": mono / total}
    changes = []
    for d, c in by_sample:
        if c == "control" and (d, "stim") in by_sample:
            a, n = by_sample[d, "control"]
            b, m = by_sample[d, "stim"]
            changes.append(b / m - a / n)
    return {
        **totals,
        "pairs": len(changes),
        "up": sum(x > 0 for x in changes),
        "down": sum(x < 0 for x in changes),
        "mean_change_percentage_points": 100 * sum(changes) / len(changes),
    }


async def prepare(v08: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("Choose a new output directory to preserve earlier participants' work")
    result_dir = v08 / "heldout/current" / CASE
    result = json.loads((result_dir / "result.json").read_text())
    general = v08 / "day1/general-agent"
    generic_run = json.loads((general / (CASE + ".run.json")).read_text())
    if (
        result["status"] != "ok"
        or result["worker"] != "openai:gpt-6.1-sol"
        or generic_run["status"] != "completed"
        or generic_run["requested_model"] != "gpt-6.1-sol"
    ):
        raise ValueError("Missing or mismatched original day-one runs")
    original = result_dir / result["attempt"] / "project"
    data = (original / "cells.csv").read_bytes()
    if sha256_bytes(data) != INPUT_HASH or result["inputs"]["cells.csv"] != INPUT_HASH:
        raise ValueError("Input does not match the frozen B-29 record")
    updated = correction(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        packet = Path(tmp) / "packet"
        bq = packet / "bioquorum" / "project"
        generic = packet / "general-agent"
        sealed = packet / "coordinator" / "correction"
        for folder in (bq / ".bioquorum", generic, sealed):
            folder.mkdir(parents=True)
        state = original / ".bioquorum"
        shutil.copy2(state / "project.json", bq / ".bioquorum/project.json")
        shutil.copytree(state / "cas", bq / ".bioquorum/cas")
        with (
            closing(
                sqlite3.connect((state / "bioquorum.db").resolve().as_uri() + "?mode=ro", uri=True)
            ) as source,
            closing(sqlite3.connect(bq / ".bioquorum/bioquorum.db")) as dest,
        ):
            source.backup(dest)
        (bq / "cells.csv").write_bytes(data)
        (bq / "bioquorum.toml").write_text(
            '[kernel]\nmacos_sandbox = true\n[providers.openai]\n[roles]\nworker = "openai:gpt-6.1-sol"\nreviewer = "openai:gpt-6.1-sol"\n'
        )
        controls = ResearchControls(workspace.load(bq, global_config=packet / "unused.toml"))
        sid = result["session"]
        try:
            pid = (await programs.create(controls, "Circulating monocyte interpretation"))["id"]
            await programs.attach(controls, pid, sid)

            async def stream():
                yield data

            file = await artifacts.upload(
                controls, sid, "cells.csv", Classification.PUBLIC, stream()
            )
            await materials.register(
                controls, pid, file["id"], "experimental_data", "Frozen synthetic B-29 input"
            )
            claims = programs.show(controls, pid)["claims"]
            ids = [
                claim["id"]
                for claim in claims
                if any(ref.get("name") in ("control_mono", "stim_mono") for ref in claim["refs"])
            ]
            if len(ids) != 2:
                raise ValueError("Original group summaries were not found")
            await program_record.save_decision(controls, pid, DECISION, ids)
        finally:
            await controls.close()
            controls.store.db.engine.dispose()
        (generic / "cells.csv").write_bytes(data)
        shutil.copy2(general / (CASE + ".md"), generic / "day-one.md")
        (generic / "tool-history.json").write_text(
            json.dumps(generic_run["completed_mcp_calls"], indent=2) + "\n"
        )
        (generic / "report-decision.txt").write_text(DECISION + "\n")
        for arm in (bq.parent, generic):
            shutil.copy2(Path(__file__).with_name("day2-task.md"), arm / "TASK.md")
            (arm / "record.csv").write_text(FORM)
        (sealed / "cells.csv").write_bytes(updated)
        (sealed / "notice.txt").write_text(
            "供应商更正：D3 的 control 和 stim 样本标签对调，计数与细胞类型原样保留。请用更正表复核昨日记录。\n"  # noqa: RUF001
        )
        key = {
            "case": CASE,
            "original_sha256": INPUT_HASH,
            "corrected_sha256": sha256_bytes(updated),
            "original": counts(data),
            "corrected": counts(updated),
            "bioquorum_program": pid,
        }
        (sealed.parent / "scorer-key.json").write_text(json.dumps(key, indent=2) + "\n")
        packet.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v08-results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(prepare(args.v08_results, args.output))
