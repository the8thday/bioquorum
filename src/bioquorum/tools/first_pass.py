"""Record a decisive ambiguity without suspending the investigation (ADR-028)."""

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.policy.secrets import redact
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

TARGET = "research:clarification"


async def ask(args: ev.ClarificationRequested, ctx: ToolContext) -> ToolResult:
    for event in reversed(ctx.store.read(ctx.session_id)):
        if event.type == "user_message" and event.actor is Actor.USER:
            break
        if event.type == "clarification_requested" or (
            event.type == "tool_call_requested"
            and event.payload["tool"] not in {"ask_clarification", "research_coverage"}
        ):
            return ToolResult.text(
                "Continue this investigation under the stated assumptions.", is_error=True
            )
    else:
        return ToolResult.text("No active researcher question.", is_error=True)
    if any(redact(v) != v or not v.strip() for v in (args.question, args.assumption)):
        return ToolResult.text(
            "Use a nonempty question and assumption without secrets.", is_error=True
        )
    ctx.store.append(
        args, session_id=ctx.session_id, actor=ctx.actor, causation_id=ctx.requested_event_id
    )
    return ToolResult.text("Question recorded. Continue now under the stated assumption.")


CLARIFY = ToolSpec(
    "ask_clarification",
    "Before retrieval, record at most one question only if its answer would "
    "change the investigation. Supply a working assumption and continue immediately.",
    ask,
    PermissionClass.EXECUTE,
    args_model=ev.ClarificationRequested,
    target=lambda *_: TARGET,
)
