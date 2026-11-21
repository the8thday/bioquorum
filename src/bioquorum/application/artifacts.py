"""Session-scoped files, recorded products and portable kernel cells."""

import csv
from collections import Counter
from collections.abc import AsyncIterable, Iterable
from itertools import islice
from pathlib import Path

from bioquorum.application.controls import ResearchControls
from bioquorum.application.verify import bundle_file
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.messages import DataRefPart
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.tools.builtin.files import upload_tool
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke


def catalog(controls: ResearchControls, sid: str) -> list[dict]:
    controls.require_session(sid)
    return catalog_records((event, event.parsed()) for event in controls.store.history(sid))


def catalog_records(parsed: Iterable[tuple[ev.Event, ev.EventPayload]]) -> list[dict]:
    rows: list[dict] = []
    versions: Counter = Counter()
    statuses = {}
    turns = {}
    turn_numbers: dict[str, int] = {}
    turn = None
    turn_count = 0
    for event, p in parsed:
        row: dict | None = None
        if isinstance(p, ev.TurnStarted):
            turn_count += 1
            turn = p.turn_id
            turn_numbers[turn] = turn_count
        elif isinstance(p, ev.TurnFinished):
            turn = None
        elif isinstance(p, ev.RunStarted):
            turns[p.run_id] = turn
        elif isinstance(p, ev.FileRegistered):
            versions[p.filename] += 1
            row = dict(
                id=p.file_id,
                name=p.filename,
                path=p.path,
                hash=p.ref.content_hash,
                kind="table" if Path(p.filename).suffix.lower() in {".csv", ".tsv"} else "file",
                version=versions[p.filename],
                status="registered",
                source=p.source,
                classification=p.classification.value,
            )
        elif (
            isinstance(p, ev.LineageObserved)
            and p.direction == "output"
            and p.post_run_hash
            and p.path != "bioquorum.toml"
        ):
            versions[p.path] += 1
            row = dict(
                id=event.id,
                name=p.path,
                hash=p.post_run_hash,
                kind="table" if p.path.lower().endswith((".csv", ".tsv")) else "file",
                version=versions[p.path],
                status="observed",
                run=p.run_id,
            )
        elif (
            isinstance(p, ev.ToolCallFinished)
            and p.tool == "download_file"
            and not p.result.is_error
        ):
            part = next((part for part in p.result.content if isinstance(part, DataRefPart)), None)
            if part:
                name = part.summary.partition(", downloaded from ")[0]
                versions[name] += 1
                row = dict(
                    id=event.id,
                    name=name,
                    hash=part.ref.content_hash,
                    version=versions[name],
                    status="downloaded",
                    kind="table" if name.lower().endswith((".csv", ".tsv")) else "file",
                )
        elif isinstance(p, ev.RunOutput) and p.ref:
            row = dict(
                id=event.id,
                name="Figure " + p.run_id,
                hash=p.ref.content_hash,
                kind="figure",
                version=1,
                status="recorded",
                run=p.run_id,
            )
        elif isinstance(p, ev.ArtifactVersionCreated):
            row = dict(
                id=p.version_id,
                name=p.name,
                hash=p.content_hash,
                kind=p.kind.value,
                version=p.version,
                status="unverified",
                run=p.run_id,
            )
        elif isinstance(p, ev.ArtifactStatusChanged):
            statuses[p.version_id] = p.to_status.value
        elif isinstance(p, ev.BundleExported):
            row = dict(
                id=p.bundle_id,
                name=p.bundle_id + ".zip",
                kind="bundle",
                version=1,
                status="exported",
                session=event.session_id,
            )
        if row is not None:
            suffix = Path(row["name"]).suffix.lower()
            if row["kind"] in {"figure", "table", "report"}:
                preview = row["kind"]
            elif suffix in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}:
                preview = "figure"
            elif suffix in {".md", ".markdown", ".txt"}:
                preview = "report"
            elif suffix in {".csv", ".tsv"}:
                preview = "table"
            else:
                preview = None
            row.update(seq=event.seq, preview=preview)
            row_turn = turns.get(row.get("run"), turn)
            row["turn"] = row_turn
            row["turn_number"] = turn_numbers.get(row_turn) if row_turn else None
            rows.append(row)
    for row in rows:
        row["status"] = statuses.get(row["id"], row["status"])
    return rows


async def upload(
    controls: ResearchControls,
    sid: str,
    name: str,
    classification: Classification,
    stream: AsyncIterable[bytes],
) -> dict:
    controls.ensure_idle()
    controls.require_active_session(sid)
    if controls.demo:
        raise ValueError("Open a research project to upload files")
    policy = PermissionPolicy()
    policy.grant(
        PermissionClass.WRITE_FS, "project-uploads", PermissionScope.SESSION, session_id=sid
    )
    ctx = ToolContext(
        store=controls.store,
        session_id=sid,
        project_id=controls.ws.project_id,
        classification=controls.ws.classification,
        permissions=policy,
        workdir=controls.ws.root,
        cas=controls.ws.cas(),
        actor=Actor.USER,
    )
    result = await invoke(
        upload_tool(stream), {"filename": name, "classification": classification.value}, ctx
    )
    if result.is_error:
        raise ValueError(result.text())
    return next(row for row in catalog(controls, sid) if row["id"] == result.text())


def _record(controls: ResearchControls, sid: str, ident: str) -> dict:
    for row in catalog(controls, sid):
        if row["id"] == ident:
            return row
    raise LookupError("Artifact not found in this session")


def download(controls: ResearchControls, sid: str, ident: str) -> tuple[Path, str]:
    row = _record(controls, sid, ident)
    if row["kind"] == "bundle":
        return bundle_file(controls, row["session"], ident), row["name"]
    return controls.ws.cas().verified_path(row["hash"]), Path(row["name"]).name


def table(controls: ResearchControls, sid: str, ident: str, page: int, size: int = 30) -> dict:
    if page < 1 or not 1 <= size <= 100:
        raise ValueError("Invalid table page")
    row = _record(controls, sid, ident)
    if row["preview"] != "table":
        raise ValueError("No table preview for this artifact")
    path, name = download(controls, sid, ident)
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t" if name.lower().endswith(".tsv") else ",")
            header = next(reader, [])
            start = (page - 1) * size
            data = list(islice(reader, start, start + size + 1))
    except (csv.Error, UnicodeError, OverflowError):
        raise ValueError("This file cannot be previewed as a UTF-8 delimited table") from None
    return {"header": header, "rows": data[:size], "page": page, "more": len(data) > size}


def notebook(controls: ResearchControls, sid: str, language: str = "python") -> dict:
    controls.require_session(sid)
    cells = [
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {"bioquorum_run": p.run_id},
            "outputs": [],
            "source": p.code.splitlines(keepends=True),
        }
        for e in controls.store.history(sid)
        if isinstance(p := e.parsed(), ev.RunStarted)
        and not p.verification_run
        and p.language == language
    ]
    return {
        "nbformat": 4,
        "nbformat_minor": 4,
        "metadata": {
            "kernelspec": (
                {"name": "ir", "language": "R", "display_name": "R"}
                if language == "r"
                else {"name": "python3", "language": "python", "display_name": "Python 3"}
            )
        },
        "cells": cells,
    }
