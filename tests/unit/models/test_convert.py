from pydantic_ai import messages as pa

from bioquorum.core.enums import MessageRole, Trust
from bioquorum.core.ids import is_id
from bioquorum.core.messages import (
    UNTRUSTED_OPEN,
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)
from bioquorum.models.convert import from_provider, to_provider, tool_definitions


def history(thinking_family="deepseek"):
    call = ToolCallPart(
        call_id="01M7KH0000000000000000000A", name="python_execute", args={"code": "6*7"}
    )
    return [
        Message.system("sys"),
        Message.user("q"),
        Message(
            role=MessageRole.ASSISTANT,
            parts=[ThinkingPart(provider=thinking_family, text="let me compute"), call],
        ),
        Message.tool(
            ToolResultPart(
                call_id=call.call_id, content=[TextPart(text="42")], trust=Trust.UNTRUSTED
            )
        ),
    ]


def test_history_shape_ids_and_tool_names():
    out = to_provider(history(), family="deepseek")
    assert [type(m) for m in out] == [pa.ModelRequest, pa.ModelResponse, pa.ModelRequest]
    first = out[0]
    assert isinstance(first, pa.ModelRequest)
    assert [type(p) for p in first.parts] == [pa.SystemPromptPart, pa.UserPromptPart]
    resp = out[1]
    assert isinstance(resp, pa.ModelResponse)
    call = next(p for p in resp.parts if isinstance(p, pa.ToolCallPart))
    assert call.tool_call_id == "01M7KH0000000000000000000A"  # our ULID is the provider id
    ret = out[2].parts[0]
    assert isinstance(ret, pa.ToolReturnPart)
    assert ret.tool_name == "python_execute" and ret.tool_call_id == call.tool_call_id


def test_untrusted_tool_output_is_fenced():
    ret = to_provider(history(), family="deepseek")[2].parts[0]
    assert isinstance(ret, pa.ToolReturnPart)
    assert isinstance(ret.content, str) and ret.content.startswith(UNTRUSTED_OPEN)


def test_thinking_replayed_only_to_same_family():
    same = to_provider(history("deepseek"), family="deepseek")[1]
    other = to_provider(history("deepseek"), family="openai")[1]
    assert isinstance(same, pa.ModelResponse) and isinstance(other, pa.ModelResponse)
    assert any(isinstance(p, pa.ThinkingPart) for p in same.parts)
    assert not any(isinstance(p, pa.ThinkingPart) for p in other.parts)


def test_response_gets_fresh_ulids_and_thinking_roundtrips():
    resp = pa.ModelResponse(
        parts=[
            pa.ThinkingPart(content="hmm", id="reasoning_content", provider_name="openai"),
            pa.TextPart(content="calling"),
            pa.ToolCallPart("python_execute", '{"code": "1"}', tool_call_id="call_provider_1"),
        ]
    )
    msg = from_provider(resp, family="deepseek")
    thinking, text, call = msg.parts
    assert isinstance(call, ToolCallPart) and is_id(call.call_id) and call.args == {"code": "1"}
    assert isinstance(thinking, ThinkingPart) and thinking.provider == "deepseek"
    back = to_provider([msg], family="deepseek")[0]
    assert isinstance(back, pa.ModelResponse)
    rt = back.parts[0]
    assert isinstance(rt, pa.ThinkingPart) and rt.id == "reasoning_content" and rt.content == "hmm"
    assert isinstance(text, TextPart)


def test_malformed_tool_args_are_kept_for_invoke_to_reject():
    resp = pa.ModelResponse(parts=[pa.ToolCallPart("t", "{bad json", tool_call_id="x")])
    (call,) = from_provider(resp, family="openai").parts
    assert isinstance(call, ToolCallPart) and "code" not in call.args and call.args


def test_tool_definitions():
    (td,) = tool_definitions(
        [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    )
    assert td.name == "t" and td.parameters_json_schema == {"type": "object"}
