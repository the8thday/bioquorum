import pytest
from pydantic import ValidationError

from bioquorum.core.enums import DataKind, MessageRole, Trust
from bioquorum.core.messages import (
    DataRefPart,
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)
from bioquorum.core.refs import DataRef

H = "a" * 64


def test_round_trip_json_with_all_part_types():
    ref = DataRef(uri="file:///x.parquet", content_hash=H, size=10, kind=DataKind.DATAFRAME)
    msg = Message(
        role=MessageRole.ASSISTANT,
        parts=[
            TextPart(text="hello"),
            ThinkingPart(provider="anthropic", opaque=b"\x00\x01binary", text=None),
            ToolCallPart(call_id="c1", name="kernel.execute", args={"code": "1+1"}),
            ToolResultPart(call_id="c1", content=[DataRefPart(ref=ref, summary="shape (2,2)")]),
        ],
    )
    dumped = msg.model_dump_json()
    back = Message.model_validate_json(dumped)
    assert back == msg
    assert isinstance(back.parts[1], ThinkingPart) and back.parts[1].opaque == b"\x00\x01binary"


def test_tool_results_are_untrusted_by_default():
    part = ToolResultPart(call_id="c", content=[TextPart(text="from the web")])
    assert part.trust is Trust.UNTRUSTED
    assert Message.tool(part).has_untrusted_content()


def test_helpers():
    m = Message.assistant("a")
    assert m.text() == "a"
    assert m.tool_calls() == []
    assert Message.system("s").role is MessageRole.SYSTEM


def test_unknown_part_type_rejected():
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "user", "parts": [{"type": "video", "x": 1}]})


def test_bad_hash_rejected():
    with pytest.raises(ValidationError):
        DataRef(uri="u", content_hash="xyz", size=1)
