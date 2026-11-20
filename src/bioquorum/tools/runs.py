"""Turn one kernel execution into a recorded Run (ARCHITECTURE.md §5.5, §5.6, §6.2).

Lives in tools/ because it needs runtime (kernel), provenance (CAS) and storage (events)
together, and those sibling layers may not import each other.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, DataKind, OutputStream
from bioquorum.core.hashing import code_hash, run_key, sha256_file
from bioquorum.core.ids import new_id
from bioquorum.core.messages import ImageRefPart, ResultContent, TextPart
from bioquorum.core.refs import DataRef, NamedValue
from bioquorum.runtime import summarize
from bioquorum.runtime.kernel import EndStatus, ExecutionEnd, KernelError, KernelOutput
from bioquorum.runtime.lineage import LINEAGE_MIME
from bioquorum.runtime.values import parse_value
from bioquorum.tools.context import ToolContext

EVENT_TEXT_LIMIT = 20_000  # per run_output event; the full stdout goes to the CAS
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_BINARY_IMAGES = {"image/png", "image/jpeg"}


@dataclass
class RunRecord:
    run_id: str
    status: EndStatus
    outputs: list[DataRef] = field(default_factory=list)
    stdout_ref: DataRef | None = None
    content: list[ResultContent] = field(default_factory=list)
    error: str | None = None
    values: list[NamedValue] = field(default_factory=list)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


async def execute_run(
    ctx: ToolContext,
    code: str,
    *,
    language: str = "python",
    timeout: float | None = None,
    inputs: Sequence[DataRef] = (),
    input_paths: Sequence[str] = (),
) -> RunRecord:
    kernels, cas = ctx.require_kernels(), ctx.require_cas()
    store, sid = ctx.store, ctx.session_id
    run_id = new_id()
    chash = code_hash(code)

    async with kernels.lock(sid):
        kernel = await kernels.get(sid, language)
        started = store.append(
            ev.RunStarted(
                run_id=run_id,
                kernel_id=kernel.kernel_id,
                language=language,
                code=code,
                code_hash=chash,
                env_lock_hash=ctx.env_lock_hash,
                inputs=list(inputs),
                input_paths=list(input_paths),
                environment=getattr(kernel, "environment", None),
                synthetic=getattr(kernel, "synthetic", False),
                verification_run=ctx.verification_run,
                isolation=kernel.isolation,
            ),
            session_id=sid,
            actor=Actor.TOOL,
            causation_id=ctx.requested_event_id,
        )

        outputs: list[KernelOutput] = []
        figures: list[DataRef] = []
        labels: dict[int, str] = {}
        image_parts: list[ImageRefPart] = []
        stdout: list[str] = []
        values: dict[str, NamedValue] = {}  # bq.value declarations; last one per name wins
        value_problems: list[str] = []
        observed_io: set[tuple[str, str, str]] = set()
        end = ExecutionEnd(status=EndStatus.ERROR)

        try:
            async for item in kernel.execute(code, timeout=timeout):
                if isinstance(item, ExecutionEnd):
                    end = item
                    break
                idx = len(outputs)
                outputs.append(item)
                if len(observed_io) < 100 and (trace := item.data.get(LINEAGE_MIME)):
                    try:
                        row = json.loads(trace)
                        path, direction, fn = row["path"], row["direction"], row["function"]
                        if (
                            isinstance(path, str)
                            and len(path) <= 1024
                            and isinstance(fn, str)
                            and len(fn) <= 60
                            and direction in ("input", "output")
                        ):
                            observed_io.add((path, direction, fn))
                    except (ValueError, KeyError, TypeError):
                        pass
                declared = parse_value(item)
                if isinstance(declared, NamedValue):
                    values[declared.name] = declared
                elif isinstance(declared, str):
                    value_problems.append(declared)
                mime = summarize.image_mime(item)
                if mime is not None:
                    payload = item.data[mime]
                    raw = (
                        base64.b64decode(payload)
                        if mime in _BINARY_IMAGES
                        else payload.encode("utf-8")
                    )
                    ref = cas.put_bytes(raw, kind=DataKind.FIGURE)
                    figures.append(ref)
                    image_parts.append(ImageRefPart(ref=ref, mime=mime))
                    labels[idx] = f"[figure {len(figures)}: {mime}, {ref.uri}]"
                    store.append(
                        ev.RunOutput(run_id=run_id, stream=OutputStream.DISPLAY, ref=ref),
                        session_id=sid,
                        actor=Actor.TOOL,
                        causation_id=started.id,
                    )
                    continue
                text = summarize.output_text(item)
                if text is None:
                    continue
                if item.stream is not OutputStream.STDERR:
                    stdout.append(text)
                store.append(
                    ev.RunOutput(
                        run_id=run_id,
                        stream=item.stream,
                        text=summarize.truncate(text, EVENT_TEXT_LIMIT),
                    ),
                    session_id=sid,
                    actor=Actor.TOOL,
                    causation_id=started.id,
                )
        except Exception as exc:  # kernel died or protocol error: still end the Run
            end = ExecutionEnd(
                status=EndStatus.ERROR,
                error=KernelError(ename=type(exc).__name__, evalue=str(exc)),
            )
        except BaseException:  # task cancelled: record it, then propagate
            store.append(
                ev.RunCancelled(run_id=run_id),
                session_id=sid,
                actor=Actor.TOOL,
                causation_id=started.id,
            )
            raise

    # A library-specific observation names the same write more precisely.
    library_outputs = {
        path for path, direction, fn in observed_io if direction == "output" and fn != "file_write"
    }
    for path, direction, fn in sorted(observed_io):
        if fn == "file_write" and path in library_outputs:
            continue
        # The kernel reports observations, not proof of what bytes a call used.
        root = ctx.workdir.resolve()
        try:
            resolved = (root / path).resolve()
            relative = resolved.relative_to(root)
            if any(part.startswith(".") for part in relative.parts) or not relative.parts:
                continue
            digest = None
            if resolved.is_file() and resolved.stat().st_size <= 64 * 2**20:
                if direction == "output" and relative.as_posix() != "bioquorum.toml":
                    digest = cas.put_file(resolved).content_hash
                else:
                    digest = sha256_file(resolved)
            event = ev.LineageObserved(
                run_id=run_id,
                path=relative.as_posix(),
                direction=direction,  # type: ignore[arg-type]
                function=fn,
                post_run_hash=digest,
            )
            store.append(event, session_id=sid, actor=Actor.TOOL, causation_id=started.id)
        except (ValueError, OSError, RuntimeError):
            continue

    stdout_ref = (
        cas.put_bytes("".join(stdout).encode("utf-8"), kind=DataKind.OTHER) if stdout else None
    )
    record = RunRecord(run_id=run_id, status=end.status, outputs=figures, stdout_ref=stdout_ref)
    end_payload: ev.EventPayload
    if end.status is EndStatus.OK:
        key = run_key(chash, ctx.env_lock_hash, (ref.content_hash for ref in inputs))
        record.values = list(values.values())
        end_payload = ev.RunFinished(
            run_id=run_id,
            outputs=figures,
            stdout_ref=stdout_ref,
            run_key=key,
            values=record.values,
        )
    elif end.status is EndStatus.CANCELLED:
        end_payload = ev.RunCancelled(run_id=run_id)
        record.error = "cancelled"
    elif end.status is EndStatus.TIMED_OUT:
        end_payload = ev.RunTimedOut(run_id=run_id, timeout_s=end.timeout_s or 0.0)
        record.error = f"timed out after {end.timeout_s}s"
    else:
        err = end.error
        detail = ""
        if err is not None:
            tb = strip_ansi("\n".join(err.traceback[-3:]))
            detail = err.short() + (f"\n{tb}" if tb else "")
        record.error = detail or "execution failed"
        end_payload = ev.RunFailed(run_id=run_id, error=record.error)
    store.append(end_payload, session_id=sid, actor=Actor.TOOL, causation_id=started.id)

    summary = summarize.summarize_outputs(outputs, image_labels=labels)
    header = f"run_id: {run_id}\nstatus: {end.status.value}"
    body = summary or "(no output)"
    if record.error:
        body += f"\nerror: {record.error}"
        if values:
            body += f"\nbq.value not recorded because the run did not finish: {', '.join(values)}"
    for problem in value_problems:
        body += f"\n{problem}"
    record.content = [TextPart(text=f"{header}\n{body}"), *image_parts]
    return record
