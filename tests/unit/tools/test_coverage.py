import pytest

from bioquorum.core import events as ev
from bioquorum.core.coverage import coverage_view
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.tools import invoke
from bioquorum.tools.coverage import COVERAGE


@pytest.mark.anyio
async def test_ledger_replays_scoped_queries_and_rejects_invented_results(ctx):
    def append(payload):
        return ctx.store.append(
            payload,
            session_id=ctx.session_id,
            actor=Actor.USER if isinstance(payload, ev.UserMessage) else Actor.AGENT,
        )

    def view():
        return coverage_view(ctx.store.history(ctx.session_id))

    append(ev.UserMessage(message=Message.user("Assess evidence.")))
    update = {"action": "plan", "topics": {"genetics": "Is there human genetic support?"}}
    assert not (await invoke(COVERAGE, update, ctx)).is_error
    append(
        ev.ToolCallRequested(
            call_id="call1",
            tool="search_papers",
            args={"query": "trial"},
            permission=PermissionClass.CONNECTOR,
        )
    )
    append(
        ev.ToolCallFinished(
            call_id="call1",
            tool="search_papers",
            result=ToolResultPart(
                call_id="call1",
                content=[TextPart(text="Source URL: public\nResponse SHA-256: abc\n[]")],
            ),
        )
    )
    assert (
        await invoke(COVERAGE, {"action": "assign", "assignments": {"q9": "genetics"}}, ctx)
    ).is_error
    assert not (
        await invoke(COVERAGE, {"action": "assign", "assignments": {"q1": "genetics"}}, ctx)
    ).is_error
    ledger = view()
    assert ledger and ledger["queries"][0]["count"] == 0
    assert ledger["queries"][0]["status"] == "empty"
    assert ledger["queries"][0]["topic"] == "genetics"
    assert not (
        await invoke(
            COVERAGE,
            {
                "action": "close",
                "gaps": ["No registry search yet."],
                "decisive": "A comparative trial.",
            },
            ctx,
        )
    ).is_error
    assert (view() or {})["closed"]
    assert (await invoke(COVERAGE, update, ctx)).is_error
    append(ev.UserMessage(message=Message.user("Another study.")))
    assert view() is None
