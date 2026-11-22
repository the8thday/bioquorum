"""Session-scoped files, recorded products and portable kernel cells."""

import csv
import json
from collections.abc import AsyncIterable
from pathlib import Path

from bioquorum.application.controls import ResearchControls
from bioquorum.application.scientific_viewers import (
    bed_features,
    fasta_records,
    mol_records,
    point_map,
    sequence_tracks,
)
from bioquorum.application.table_view import read_page
from bioquorum.application.verify import bundle_file
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.selections import RecordSelection
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.policy.secrets import redact
from bioquorum.provenance.catalog import catalog_records
from bioquorum.tools.builtin.files import upload_tool
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke


def catalog(controls: ResearchControls, sid: str) -> list[dict]:
    controls.require_session(sid)
    return catalog_records((event, event.parsed()) for event in controls.store.history(sid))


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


def table(
    controls: ResearchControls,
    sid: str,
    ident: str,
    page: int,
    size: int = 30,
    *,
    query: str = "",
    sort: int | None = None,
    descending: bool = False,
    stats: int | None = None,
) -> dict:
    row = _record(controls, sid, ident)
    if row["preview"] != "table":
        raise ValueError("No table preview for this artifact")
    path, name = download(controls, sid, ident)
    try:
        return read_page(
            path,
            "\t" if name.lower().endswith(".tsv") else ",",
            page,
            size,
            query=query,
            sort=sort,
            descending=descending,
            stats=stats,
        )
    except (csv.Error, UnicodeError, OverflowError):
        raise ValueError("This file cannot be previewed as a UTF-8 delimited table") from None


def scientific_data(controls: ResearchControls, sid: str, ident: str) -> dict:
    row = _record(controls, sid, ident)
    kind = row["preview"]
    if kind in {"genome", "molecule"}:
        path, _ = download(controls, sid, ident)
        return {"records": bed_features(path) if kind == "genome" else mol_records(path)}
    if kind not in {"figure", "sequence"}:
        raise ValueError("No annotations for this artifact")
    data: dict = {}
    if kind == "sequence":
        path, _ = download(controls, sid, ident)
        data["records"] = fasta_records(path)
    suffix = ".points.tsv" if kind == "figure" else ".annotations.tsv"
    matches = [
        item
        for item in catalog(controls, sid)
        if row.get("run") and item.get("run") == row["run"] and item["name"] == row["name"] + suffix
    ]
    if len({item["hash"] for item in matches}) > 1:
        raise ValueError("Ambiguous annotation versions in the generating run")
    data["points" if kind == "figure" else "tracks"] = []
    if matches:
        sidecar = matches[0]
        path, _ = download(controls, sid, sidecar["id"])
        if kind == "figure":
            data["points"] = point_map(path)
        else:
            data["tracks"] = sequence_tracks(path, [len(r["sequence"]) for r in data["records"]])
        data["source"] = {"id": sidecar["id"], "hash": sidecar["hash"], "run": row["run"]}
    return data


def chromosome_extents(controls: ResearchControls, sid: str, ident: str) -> str:
    if _record(controls, sid, ident)["preview"] != "genome":
        raise ValueError("Expected a BED track")
    lengths: dict[str, int] = {}
    for record in scientific_data(controls, sid, ident)["records"]:
        chrom = record["chr"]
        lengths[chrom] = max(lengths.get(chrom, 0), record["end"])
    return "".join(f"{chrom}\t{end}\n" for chrom, end in lengths.items())


def read_report(path: Path) -> str:
    if path.stat().st_size > 512 * 1024:
        raise ValueError("Report exceeds 512 KiB. Download the full file.")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        raise ValueError(
            "Text preview requires a UTF-8 report. Download the original file."
        ) from None
    return text


def resolve_selection(
    controls: ResearchControls, sid: str, ref: ev.TurnReference
) -> ev.TurnReference:
    """Read selected content from the recorded bytes before recording a turn."""
    sel = ref.selection
    if sel is None:
        return ref
    source = _record(controls, sid, ref.id)
    if source["preview"] != sel.type:
        raise ValueError("Unexpected selected artifact type")
    if sel.type == "report":
        path, _ = download(controls, sid, ref.id)
        lines = read_report(path).split("\n")
        if not 1 <= sel.start <= sel.end <= len(lines):
            raise ValueError("Selection outside the report")
        value = "\n".join(lines[sel.start - 1 : sel.end])
    elif sel.type == "table":
        page = table(controls, sid, ref.id, sel.row, 1)
        if not page["rows"]:
            raise ValueError("Row is outside the table")
        row = page["rows"][0]
        if sel.column is None:
            value = row
        elif sel.column < min(len(row), len(page["header"])):
            value = [page["header"][sel.column], row[sel.column]]
        else:
            raise ValueError("Cell is outside the table")
    elif sel.type == "sequence":
        data = scientific_data(controls, sid, ref.id)
        records = data.get("records", [])
        if sel.record > len(records):
            raise ValueError("Unknown sequence record")
        record = records[sel.record - 1]
        if not 1 <= sel.start <= sel.end <= len(record["sequence"]):
            raise ValueError("Interval outside sequence")
        value = dict(name=record["name"], sequence=record["sequence"][sel.start - 1 : sel.end])
        if data.get("source"):
            tracks = [
                track
                for track in data["tracks"]
                if track["record"] == sel.record
                and track["start"] <= sel.end
                and track["end"] >= sel.start
            ]
            value.update(
                annotation_source=data["source"],
                overlapping_tracks=tracks[:8],
                total_overlapping_tracks=len(tracks),
            )
    elif sel.type == "figure":
        data = scientific_data(controls, sid, ref.id)
        points = data.get("points", [])
        if any(i < 0 or i >= len(points) for i in sel.points):
            raise ValueError("Unknown figure point")
        value = dict(samples=[points[i]["sample_id"] for i in sel.points], mapping=data["source"])
        if len(json.dumps(value, ensure_ascii=False)) > 2000:
            raise ValueError("Select fewer points to include every sample identifier")
    elif isinstance(sel, RecordSelection):
        records = scientific_data(controls, sid, ref.id)["records"]
        if sel.record > len(records):
            raise ValueError("Unknown record")
        value = records[sel.record - 1]
        if sel.type == "molecule":
            value = {"record": sel.record, "name": value.splitlines()[0]}
    else:
        # The viewer supplies a user-picked coordinate, not a measured result.
        download(controls, sid, ref.id)
        value = "User selection in the deposited coordinate model"
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if redact(text) != text or redact(sel.model_dump_json()) != sel.model_dump_json():
        raise ValueError("Selection contains a credential")
    return ref.model_copy(
        update={"excerpt": text if len(text) <= 2000 else text[:1990] + "…[clipped]"}
    )


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
