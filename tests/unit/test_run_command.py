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
from bioquorum.runtime.values import value_output
from bioquorum.storage.events import EventStore

pytestmark = pytest.mark.anyio
CFG = ModelsConfig.from_dict(
    {"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:w", "reviewer": "deepseek:r"}}
)


V01_ANSWER = "1. The cohort has 712 participants.\n2. APOE4 triples risk [PMID:99999999]."
V01_CLAIMS = [
    {"text": "The cohort has 712 participants.", "kind": "execution"},
    {"text": "APOE4 triples risk [PMID:99999999].", "kind": "citation"},
]


def fake_model(answer: str = V01_ANSWER, claims: list | None = None):
    """Worker: run code, then answer. Default: one true and one fabricated citation claim.
    Reviewer: split into claims; judge no citations (none resolve)."""
    claims = V01_CLAIMS if claims is None else claims

    def fn(messages, info):
        system = next(
            p.content
            for m in messages
            if isinstance(m, pa.ModelRequest)
            for p in m.parts
            if isinstance(p, pa.SystemPromptPart)
        )
        if "split a research assistant's answer" in system:
            return pa.ModelResponse(parts=[pa.TextPart(content=json.dumps({"claims": claims}))])
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
        return pa.ModelResponse(parts=[pa.TextPart(content=answer)])

    return FunctionModel(fn)


@pytest.fixture
def project(tmp_path, monkeypatch, request):
    ws = wsp.init_project(tmp_path, classification=Classification.PUBLIC)
    kernel = FakeKernel().on(
        "len(df)", Script(outputs=[value_output("n_participants", 712, unit="participants")])
    )

    async def factory(language):
        return kernel

    real_ctx = wsp.tool_context
    monkeypatch.setattr(
        runner.wsp, "tool_context", lambda *a, **k: real_ctx(*a, **{**k, "kernel_factory": factory})
    )
    model = getattr(request, "param", None) or fake_model()
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


BOUND = fake_model(
    "1. The cohort has {{n_participants}} participants (run r).\n2. {{n_missing}} were excluded.",
    [
        {"text": "The cohort has {{n_participants}} participants.", "kind": "execution"},
        {"text": "{{n_missing}} were excluded.", "kind": "numeric"},
    ],
)


@pytest.mark.parametrize("project", [BOUND], indirect=True)
async def test_run_renders_bound_values_and_shows_their_evidence(project):
    console = Console(record=True, width=200)
    await runner.run_question(
        project,
        "How many participants?",
        console=console,
        err=console,
        session_id=None,
        yes=True,
        review=True,
        max_steps=6,
    )
    out = console.export_text()
    assert "The cohort has 712 participants" in out  # the recorded value, filled in
    assert "{{n_participants}} = 712 participants" in out and "run " in out  # evidence
    assert "unresolved {{n_missing}}" in out  # failures are shown, not hidden
    assert "reviewed" in out and "binding" in out and "overall: disputed" in out


def test_evidence_list_keeps_source_labels():
    from bioquorum.review.evidence import Evidence, Source

    ev_ = Evidence(sources=[Source("s9", "C9", "biomcp__get", "metformin reduces hepatic output")])
    console = Console(record=True, width=120)
    runner.print_answer(console, 'It acts on the liver [@s9 "reduces hepatic output"].', ev_)
    out = console.export_text()
    assert '"reduces hepatic output" [s9]' in out and "[s9] biomcp__get  call C9" in out
