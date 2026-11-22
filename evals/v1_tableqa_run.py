"""Run the authorized 32 public image questions using one persistent spending ledger."""

import argparse
import asyncio
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import mkdtemp

import httpx2
from evals.v04_public_run import run_task
from evals.v08_budget import DEVELOPMENT_MODEL, MAX_OUTPUT, MeteredOpenAI, SpendingLog

from bioquorum.application import research
from bioquorum.core.hashing import sha256_file
from bioquorum.models.registry import build_model

ROOT = Path("evals/results/v1")
CAMPAIGN = ROOT / "tableqa-official"
MANIFEST_SHA256 = "60ff6bbc6f1274bb0e7b6e8591d7ccb0e59b51e6dbc37642ec56adfad8275434"


def load_tasks(path: Path) -> list[dict]:
    if sha256_file(path) != MANIFEST_SHA256:
        raise ValueError("Task manifest differs from the frozen cohort")
    return json.loads(path.read_text())["tasks"]


def save(path: Path, data) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def exclusive_campaign(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "campaign-lock.sqlite", timeout=0)) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
            raise RuntimeError("The evaluation is already running") from exc
        yield


async def main() -> None:
    tasks = load_tasks(ROOT / "tableqa-questions.json")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is missing")
    ledger = SpendingLog(CAMPAIGN / "spending.sqlite", 50_000_000)
    meter = MeteredOpenAI(ledger, model=DEVELOPMENT_MODEL)
    semaphore = asyncio.Semaphore(3)
    async with httpx2.AsyncClient(transport=meter, timeout=150) as http:

        def client_for(ws, store):
            client = research.model_client(ws, store)
            if client.cassette is not None or client.outbound_proxy is not None:
                raise ValueError("A formal evaluation requires the metered live endpoint")
            client.factory = lambda route: build_model(route, http_client=http)
            client.max_output_tokens = MAX_OUTPUT
            return client

        async def one(task):
            directory = CAMPAIGN / task["id"]
            directory.mkdir(exist_ok=True)
            result = directory / "result.json"
            async with semaphore:
                if result.exists() or meter.exhausted or meter.preflight_error:
                    return
                attempt = Path(mkdtemp(prefix="attempt-", dir=directory))
                args = argparse.Namespace(
                    data=ROOT / "tableqa-inputs",
                    pixi_env=None,
                    output=attempt,
                    worker=f"openai:{DEVELOPMENT_MODEL}",
                    reviewer=f"openai:{DEVELOPMENT_MODEL}",
                    max_steps=12,
                    budget_tokens=32_000,
                )
                row = await run_task(task, args, make_client=client_for, isolated=True)
                row["attempt"] = attempt.name
                save(result, row)
                print(task["id"], "answered" if row["answer"] else "unanswered", flush=True)

        await asyncio.gather(*(one(task) for task in tasks))
    results = [json.loads(p.read_text()) for p in sorted(CAMPAIGN.glob("*/result.json"))]
    save(CAMPAIGN / "runs.json", {"model": DEVELOPMENT_MODEL, "runs": results})
    save(CAMPAIGN / "predictions.json", {r["id"]: r["answer"] for r in results})
    print(
        f"{len(results)} of {len(tasks)} attempted; metering preflight: {meter.preflight_error or 'ok'}"
    )


if __name__ == "__main__":
    with exclusive_campaign(CAMPAIGN):
        asyncio.run(main())
