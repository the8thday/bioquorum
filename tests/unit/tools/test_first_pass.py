import pytest

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.messages import Message
from bioquorum.tools import invoke
from bioquorum.tools.first_pass import CLARIFY


@pytest.mark.anyio
async def test_clarification_is_optional_and_once_per_question(ctx):
    ctx.store.append(
        ev.UserMessage(message=Message.user("Assess this.")), session_id="S", actor=Actor.USER
    )
    args = {"question": "Which tissue?", "assumption": "Start with blood."}
    assert not (await invoke(CLARIFY, args, ctx)).is_error
    assert (await invoke(CLARIFY, args, ctx)).is_error
    assert len(ctx.store.read("S", types=["clarification_requested"])) == 1
