"""Declare questions and classify actual retrieval events; results remain tool-owned."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core import events as ev
from bioquorum.core.coverage import coverage_view
from bioquorum.core.enums import PermissionClass
from bioquorum.core.hashing import canonical_json
from bioquorum.policy.secrets import redact
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

TARGET = "research:coverage"
Label = Annotated[str, Field(min_length=1, max_length=80)]
Detail = Annotated[str, Field(min_length=1, max_length=600)]


class LedgerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["plan", "inspect", "assign", "close"]
    topics: dict[Label, Detail] = Field(default_factory=dict, max_length=20)
    assignments: dict[Label, Label] = Field(default_factory=dict, max_length=100)
    gaps: list[Detail] = Field(default_factory=list, max_length=20)
    decisive: str = Field(default="", max_length=600)


async def maintain(args: LedgerAction, ctx: ToolContext) -> ToolResult:
    history = ctx.store.history(ctx.session_id)
    view = coverage_view(history)
    if args.action == "inspect":
        return ToolResult.text(canonical_json(view).decode())
    if view and view["closed"]:
        return ToolResult.text("This question's ledger is closed.", is_error=True)
    recorded = canonical_json(args.model_dump()).decode()
    if redact(recorded) != recorded:
        return ToolResult.text("Remove sensitive material from the ledger.", is_error=True)
    event: ev.EventPayload
    if args.action == "plan" and args.topics:
        event = ev.CoveragePlanned(topics=args.topics)
    elif args.action == "assign" and view and args.assignments:
        calls = {q["id"]: q["call_id"] for q in view["queries"]}
        if not all(q in calls and t in view["topics"] for q, t in args.assignments.items()):
            return ToolResult.text(
                "Use only recorded query IDs and existing topics.", is_error=True
            )
        event = ev.CoverageUpdated(assignments={calls[q]: t for q, t in args.assignments.items()})
    elif args.action == "close" and view and args.decisive.strip():
        event = ev.CoverageClosed(gaps=args.gaps, decisive=args.decisive)
    else:
        return ToolResult.text("Supply the fields required for this action.", is_error=True)
    ctx.store.append(
        event, session_id=ctx.session_id, actor=ctx.actor, causation_id=ctx.requested_event_id
    )
    return ToolResult.text("Ledger updated.")


COVERAGE = ToolSpec(
    "research_coverage",
    "Maintain the evidence coverage ledger. plan replaces the map of evidence categories to subquestions; "
    "inspect lists query IDs, scopes and observed outcomes; assign maps query IDs to categories; "
    "close records remaining gaps and the evidence most likely to change the judgment.",
    maintain,
    PermissionClass.EXECUTE,
    args_model=LedgerAction,
    target=lambda *_: TARGET,
)
