"""`bq run` / `bq review` wiring with a scripted model and a FakeKernel (no network)."""

import json

import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel
from rich.console import Console

from bioquorum.cli import runner
from bioquorum.cli import workspace as wsp
from bioquorum.core.enums import Classification
from bioquorum.core.status import VerificationStatus
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.runtime import FakeKernel, Script
from bioquorum.storage.events import EventStore

pytestmark = pytest.mark.anyio
CFG = ModelsConfig.from_dict(
    {"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:w", "reviewer": "deepseek:r"}}
)


def fake_model():
    """Worker: run code, then answer with one true and one fabricated citation claim.
    Reviewer: split into claims; judge no citations (none resolve)."""

    def fn(messages, info):
        system = next(
            p.content
            for m in messages
            if isinstance(m, pa.ModelRequest)
            for p in m.parts
            if isinstance(p, pa.SystemPromptPart)
        )
        if "split a research assistant's answer" in system:
            return pa.ModelResponse(
                parts=[
                    pa.TextPart(
                        content=json.dumps(
                            {
                                "claims": [
                                    {
                                        "text": "The cohort has 712 participants.",
                                        "kind": "execution",
                                    },
                                    {
                                        "text": "APOE4 triples risk [PMID:99999999].",
                                        "kind": "citation",
                                    },
                                ]
                            }
                        )
                    )
                ]
            )
        if "check whether cited sources" in system:
            return pa.ModelResponse(parts=[pa.TextPart(content='{"verdicts": []}')])
        returned = any(
            isinstance(p, pa.ToolReturnPart)
            for m in messages
            if isinstance(m, pa.ModelRequest)
            for p in m.parts
        )
        if not returned:
            return pa.ModelResponse(
                parts=[
                    pa.ToolCallPart("python_execute", {"code": "print(len(df))"}, tool_call_id="t")
                ]
            )
        return pa.ModelResponse(
            parts=[
                pa.TextPart(
                    content="1. The cohort has 712 participants.\n2. APOE4 triples risk [PMID:99999999]."
                )
            ]
        )

    return FunctionModel(fn)


@pytest.fixture
def project(tmp_path, monkeypatch):
    ws = wsp.init_project(tmp_path, classification=Classification.PUBLIC)
    kernel = FakeKernel().on("len(df)", Script.stdout("712\n"))

    async def factory(language):
        return kernel

    real_ctx = wsp.tool_context
    monkeypatch.setattr(
        runner.wsp, "tool_context", lambda *a, **k: real_ctx(*a, **{**k, "kernel_factory": factory})
    )
    model = fake_model()
    monkeypatch.setattr(
        runner,
        "model_client",
        lambda ws_, store: ModelClient(Router(CFG), store, factory=lambda r: model),
    )
    return ws


async def test_run_question_end_to_end(project):
    console = Console(record=True, width=200)
    code = await runner.run_question(
        project,
        "How many participants?",
        console=console,
        err=console,
        session_id=None,
        yes=True,
        review=True,
        max_steps=5,
    )
    out = console.export_text()
    assert code == 0
    assert "> python print(len(df))" in out and "712" in out
    assert "cohort has 712 participants" in out
    assert "citation_resolves" in out and "99999999" in out and "overall: disputed" in out


async def test_review_external_answer_file(project, tmp_path):
    store = EventStore(project.database())
    sid = wsp.start_session(project, store, "from claude code")
    f = tmp_path / "answer.md"
    f.write_text("1. APOE4 triples risk [PMID:99999999].")
    result = await runner.review_session(
        project, console=Console(), session_id=sid, answer_file=f, model_checks=False
    )
    assert result.status is VerificationStatus.DISPUTED
    assert any(fd.check == "citation_resolves" for fd in result.findings)
