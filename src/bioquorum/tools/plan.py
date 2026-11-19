"""Record progress on an approved research plan via the audited tool path."""

from pydantic import BaseModel, Field

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

PROGRESS = "record_plan_step"
DEVIATION = "request_plan_deviation"
PROGRESS_TARGET = "plan:progress"


class Step(BaseModel):
    step: int = Field(ge=1)


class Deviation(BaseModel):
    change: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


def plan_tools(plan: ev.PlanProposed) -> tuple[ToolSpec, ToolSpec]:
    async def step(args: Step, ctx: ToolContext) -> ToolResult:
        done = {
            p.step_index
            for e in ctx.store.read(ctx.session_id)
            if isinstance(p := e.parsed(), ev.PlanStepDone) and p.plan_id == plan.plan_id
        }
        if args.step != len(done) + 1 or args.step > len(plan.steps):
            return ToolResult.text("Record the next completed step in order.", is_error=True)
        ctx.store.append(
            ev.PlanStepDone(plan_id=plan.plan_id, step_index=args.step - 1),
            session_id=ctx.session_id,
            actor=Actor.AGENT,
            causation_id=ctx.requested_event_id,
        )
        return ToolResult.text("Step completion recorded.")

    async def deviation(args: Deviation, ctx: ToolContext) -> ToolResult:
        ctx.store.append(
            ev.PlanDeviation(plan_id=plan.plan_id, reason=args.reason, change=args.change),
            session_id=ctx.session_id,
            actor=Actor.AGENT,
            causation_id=ctx.requested_event_id,
        )
        return ToolResult.text("Deviation approved and recorded.")

    return (
        ToolSpec(
            PROGRESS,
            "Record the next completed step of the approved plan.",
            step,
            PermissionClass.EXECUTE,
            args_model=Step,
            target=lambda *_: PROGRESS_TARGET,
        ),
        ToolSpec(
            DEVIATION,
            "Ask the researcher before deviating from the approved plan.",
            deviation,
            PermissionClass.EXECUTE,
            args_model=Deviation,
            target=lambda args, _: f"Plan deviation: {args.change}\nReason: {args.reason}",
        ),
    )
