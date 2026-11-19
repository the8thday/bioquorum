"""Render an editable Methods draft from the records included in a research bundle."""

from collections.abc import Iterable, Mapping
from html import escape

from bioquorum.core import events as ev
from bioquorum.core.bundle import ManifestRun
from bioquorum.core.bundle_v1 import PortableRun
from bioquorum.policy.secrets import public_url


def _code(value: str) -> str:
    return f"<code>{escape(' '.join(value.splitlines()))}</code>"


def methods_draft(
    events: Iterable[ev.Event],
    runs: list[ManifestRun],
    records: list[PortableRun],
    sources: Mapping[str, str],
) -> str:
    lines = [
        "# Methods draft",
        "",
        "BioQuorum generated this draft from the archived research record. "
        "Review the source selection, scientific protocol and interpretation before publication.",
        "",
        "## Evidence collection",
        "",
    ]
    starts: dict[str, ev.RunStarted] = {}
    tools: dict[str, str] = {}
    urls: dict[str, str] = {}
    for event in events:
        payload = event.parsed()
        if isinstance(payload, ev.RunStarted):
            starts[payload.run_id] = payload
        elif isinstance(payload, ev.ToolCallFinished):
            tools[payload.call_id] = payload.tool
        elif isinstance(payload, ev.LiteratureFinished):
            for source in payload.sources:
                tools[source.call_id] = source.tool
        elif (
            isinstance(payload, ev.ToolCallRequested)
            and payload.tool == "download_file"
            and isinstance(url := payload.args.get("url"), str)
        ):
            urls[payload.call_id] = public_url(url)
    if sources:
        lines.append("Archived tool results and literature excerpts are in `review/sources.json`.")
        lines.append("")
        for call_id in sources:
            url = f" from {_code(urls[call_id])}" if call_id in urls else ""
            lines.append(f"- {_code(tools[call_id])}{url}, call {_code(call_id)}.")
    else:
        lines.append("No retrieved sources were included in this record.")

    lines.extend(("", "## Computational methods", ""))
    if not runs:
        lines.append("No computational runs are included in this bundle.")
    for run, record in zip(runs, records, strict=True):
        start = starts[run.run_id]
        lines.extend(
            (
                f"### Run {_code(run.run_id)}",
                "",
                f"The archived {_code(run.language)} code is in {_code(run.code_path)}. "
                f"Code SHA-256 is {_code(run.code_hash)}.",
                "",
                f"Runtime fingerprint is {_code(record.environment or 'unrecorded')} and "
                f"kernel isolation is {_code(start.isolation or 'unrecorded')}.",
            )
        )
        if run.env_lock_hash:
            lines.append(f"The environment lock SHA-256 is {_code(run.env_lock_hash)}.")
        if record.synthetic:
            lines.append("This run used a synthetic kernel.")
        if record.inputs:
            lines.extend(("", "Recorded inputs and SHA-256 hashes", ""))
            lines.extend(f"- {_code(path)} · {_code(sha)}" for path, sha in record.inputs.items())
        if run.values:
            lines.extend(("", "Named run outputs", ""))
            for value in run.values:
                unit = f" {_code(value.unit)}" if value.unit else ""
                description = f" · {_code(value.desc)}" if value.desc else ""
                lines.append(
                    f"- {_code(value.name)} = {_code(str(value.value))}{unit}{description}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
