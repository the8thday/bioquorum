"""Describe immutable products in an event history."""

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from bioquorum.core import events as ev
from bioquorum.core.messages import DataRefPart


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
        elif isinstance(p, ev.TurnResumed):
            turn = p.turn_id
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
            elif suffix in {".mol", ".sdf"}:
                preview = "molecule"
            elif suffix == ".bed":
                preview = "genome"
            elif suffix in {".pdb", ".mmcif", ".cif", ".ent"}:
                preview = "structure"
            elif suffix in {".fasta", ".fa", ".faa", ".fna", ".fas"}:
                preview = "sequence"
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
