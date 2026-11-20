"""Verification exercises the audited tools and event-backed claim state."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from bioquorum.application import verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.bundle_v1 import ManifestV1
from bioquorum.core.enums import Actor, ClaimKind, Classification
from bioquorum.core.hashing import code_hash
from bioquorum.core.messages import Message, TextPart, ThinkingPart, ToolResultPart
from bioquorum.core.refs import NamedValue, ValueRef
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.runtime import FakeKernel, Script
from bioquorum.runtime.values import value_output

pytestmark = pytest.mark.anyio


class IsolatedFixture(FakeKernel):
    # Exercise the certification branches without claiming real isolation in tests.
    environment = "test-environment"
    synthetic = False

    @property
    def isolation(self):
        return "bwrap"


def seed(tmp_path: Path, *, lock: str | None = None):
    ws = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    count = NamedValue(name="n", value=3, unit="records")
    code = 'bq.value("n", len(open("input.txt").readlines()), unit="records")'
    cas = ws.cas()
    source = cas.put_bytes(b"a\nb\nc\n")
    output = cas.put_bytes(b"bq.value n = 3 records")
    actual_kernel = IsolatedFixture()
    actual_kernel.on(code, Script(outputs=[value_output("n", 3, unit="records")]))

    async def factory(_):
        return actual_kernel

    controls = ResearchControls(ws, kernel_factory=factory)
    sid = controls.new_session("Study")
    store = controls.store
    from bioquorum.core.hashing import run_key

    events = [
        ev.UserMessage(message=Message.user("Count recorded samples")),
        ev.RunStarted(
            run_id="run",
            kernel_id="kernel",
            code=code,
            code_hash=code_hash(code),
            env_lock_hash=lock,
            inputs=[source],
            input_paths=["input.txt"],
            environment=actual_kernel.environment,
            isolation=actual_kernel.isolation,
        ),
        ev.RunFinished(
            run_id="run",
            values=[count],
            stdout_ref=output,
            run_key=run_key(code_hash(code), lock, [source.content_hash]),
        ),
    ]
    for payload in events:
        store.append(payload, session_id=sid, actor=Actor.SYSTEM)
    answer = store.append(
        ev.AssistantMessage(
            message=Message.assistant("Counted {{n}} records.").model_copy(
                update={
                    "parts": [
                        *Message.assistant("Counted {{n}} records.").parts,
                        ThinkingPart(provider="test", text="Private provider reasoning"),
                    ]
                }
            )
        ),
        session_id=sid,
        actor=Actor.AGENT,
    )
    store.append(
        ev.ClaimExtracted(
            claim_id="claim",
            source_event_id=answer.id,
            text="Counted {{n}} records.",
            kind=ClaimKind.NUMERIC,
            refs=[ValueRef(name="n", run_id="run")],
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    for before, after, trigger in [
        (
            VerificationStatus.UNVERIFIED,
            VerificationStatus.GROUNDED,
            StatusTrigger.BINDINGS_RESOLVED,
        ),
        (VerificationStatus.GROUNDED, VerificationStatus.REVIEWED, StatusTrigger.REVIEW_PASSED),
    ]:
        store.append(
            ev.ClaimStatusChanged(
                claim_id="claim", from_status=before, to_status=after, trigger=trigger
            ),
            session_id=sid,
            actor=Actor.REVIEWER,
        )
    return controls, sid, actual_kernel


@pytest.mark.parametrize("locked", [True, False])
async def test_replay_uses_cas_and_never_promotes_an_unlocked_match(tmp_path, monkeypatch, locked):
    lock = "locked" if locked else None
    controls, sid, kernel = seed(tmp_path, lock=lock)
    (tmp_path / "input.txt").write_text("different host input\n")
    monkeypatch.setattr(verify, "project_environment", lambda *args, **kw: (None, lock))
    result = await verify.rerun(controls, sid, "claim")
    assert result["outcome"] == "matched", result
    assert result["status_eligible"] is locked
    assert kernel.closed
    history = controls.store.read(sid)
    assert any(
        e.type == "tool_call_requested" and e.payload["tool"] == "replay_recorded_run"
        for e in history
    )
    assert verify._study(controls, sid, "claim").status.value == (
        "reproduced" if locked else "reviewed"
    )


async def test_tampered_cas_never_executes_into_fresh_kernel(tmp_path):
    controls, sid, kernel = seed(tmp_path)
    start = verify._study(controls, sid, "claim").runs[0][0]
    controls.ws.cas().path(start.inputs[0].content_hash).write_text("tampered")
    result = await verify.rerun(controls, sid, "claim")
    assert result["outcome"] == "inconclusive"
    assert not kernel.executed


async def test_bundle_is_scoped_and_has_binding_and_input_hashes(tmp_path):
    controls, sid, _ = seed(tmp_path)
    other = controls.new_session("Other")
    controls.store.append(
        ev.UserMessage(message=Message.user("Private unrelated session")),
        session_id=other,
        actor=Actor.USER,
    )
    result = await verify.export_bundle(controls, sid)
    path = verify.bundle_file(controls, sid, result["id"])
    with pytest.raises(LookupError):
        verify.bundle_file(controls, other, result["id"])
    from bioquorum.core.hashing import sha256_bytes

    with ZipFile(path) as archive:
        manifest = ManifestV1.model_validate_json(archive.read("manifest.json"))
        assert manifest.model_json_schema() == json.loads(
            Path("bundle-spec/manifest-v1.schema.json").read_text()
        )
        assert (
            archive.read("objects/" + manifest.run_records[0].inputs["input.txt"]) == b"a\nb\nc\n"
        )
        assert "run_id" in archive.read(manifest.claim_bindings_path).decode()
        assert b"Private provider reasoning" not in archive.read("conversation.jsonl")
        assert b"Private unrelated session" not in archive.read("conversation.jsonl")
        assert all(sha256_bytes(archive.read(file)) == sha for file, sha in manifest.files.items())
        methods = archive.read("Methods.md").decode()
        assert "input.txt" in methods and "test-environment" in methods
        assert "runs/run/code.py" in methods


async def test_bundle_can_preserve_literature_without_creating_a_computational_run(tmp_path):
    controls = ResearchControls(workspace.init_project(tmp_path))
    sid = controls.new_session("Evidence only")
    quote = "The archived record describes the original study group."
    controls.store.append(
        ev.ToolCallFinished(
            call_id="reference",
            tool="literature__article",
            result=ToolResultPart(call_id="reference", content=[TextPart(text=quote)]),
        ),
        session_id=sid,
        actor=Actor.TOOL,
    )
    result = await verify.export_bundle(controls, sid)
    path = verify.bundle_file(controls, sid, result["id"])
    with ZipFile(path) as archive:
        manifest = ManifestV1.model_validate_json(archive.read("manifest.json"))
        assert manifest.runs == [] and manifest.run_records == []
        assert json.loads(archive.read("review/sources.json")) == {"reference": quote}
        methods = archive.read("Methods.md").decode()
        assert "literature__article" in methods and "No computational runs" in methods
        assert "No computational runs" in archive.read("README.md").decode()


async def test_replication_model_receives_only_the_blind_packet(tmp_path, monkeypatch):
    from pydantic_ai import messages as pa
    from pydantic_ai.models.function import FunctionModel

    from bioquorum.models import ModelClient, ModelsConfig, Router

    controls, sid, _ = seed(tmp_path)
    store = controls.store
    store.append(
        ev.ClaimStatusChanged(
            claim_id="claim",
            from_status=VerificationStatus.REVIEWED,
            to_status=VerificationStatus.REPRODUCED,
            trigger=StatusTrigger.RERUN_MATCHED,
            rerun_id="prior-verification",
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    monkeypatch.setattr(verify, "bwrap_available", lambda: True)
    code = 'records = open("inputs/input.txt").read().count("\\n"); bq.value("n", records, unit="records")'

    def factory(*args, **kwargs):
        async def make(_language):
            return IsolatedFixture().on(
                code, Script(outputs=[value_output("n", 3, unit="records")])
            )

        assert kwargs["require_isolation"] is True
        assert kwargs["sandbox"].network is False
        return make

    monkeypatch.setattr(verify, "jupyter_factory", factory)
    monkeypatch.setattr(verify, "project_environment", lambda root, use_pixi: (None, "f" * 64))
    cfg = ModelsConfig.from_dict(
        {"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:test"}}
    )
    prompts = []

    def model(messages, info):
        parts = [part for req in messages for part in req.parts]
        if any(isinstance(part, pa.ToolReturnPart) for part in parts):
            return pa.ModelResponse(parts=[pa.TextPart(content="The count is recorded.")])
        prompts.extend(str(part.content) for part in parts if isinstance(part, pa.UserPromptPart))
        return pa.ModelResponse(
            parts=[
                pa.ToolCallPart(
                    "python_execute",
                    {
                        "code": code,
                        "input_files": ["inputs/input.txt"],
                    },
                    tool_call_id="replicate",
                )
            ]
        )

    controls.make_client = lambda ws, store: ModelClient(
        Router(cfg),
        store,
        prices={"deepseek:test": (0, 0)},
        factory=lambda _: FunctionModel(model),
    )
    estimate = verify.replication_quote(controls, sid, "claim")
    assert estimate["estimated_usd"] == 0
    result = await verify.replicate(
        controls,
        sid,
        "claim",
        verify.BlindRequest(
            question="Count the table rows.",
            definitions={"n": "Number of data rows"},
            max_estimated_usd=0.05,
        ),
    )
    assert result["outcome"] == "matched", result
    assert len(prompts) == 1 and "Number of data rows" in prompts[0]
    assert "predefined" in prompts[0]  # the model is told not to import or define bq
    blind = next(
        e.payload["blind_session_id"] for e in store.read(sid) if e.type == "replication_started"
    )
    starts = [e.payload for e in store.read(blind) if e.type == "run_started"]
    assert starts and all(s["env_lock_hash"] == "f" * 64 for s in starts)
    for secret in (
        "Count recorded samples",
        "3 records",
        "readlines",
        "Private provider reasoning",
    ):
        assert secret not in prompts[0]
    assert verify._study(controls, sid, "claim").status is VerificationStatus.REPLICATED


async def test_isolation_mismatch_never_executes_recorded_code(tmp_path, monkeypatch):
    controls, sid, kernel = seed(tmp_path)
    monkeypatch.setattr(IsolatedFixture, "isolation", property(lambda self: "none"))
    result = await verify.rerun(controls, sid, "claim")
    assert result["outcome"] == "inconclusive"
    assert not kernel.executed


async def test_missing_explicit_run_prevents_verification(tmp_path):
    from bioquorum.core.refs import RunRef

    controls, sid, kernel = seed(tmp_path)
    old = verify._study(controls, sid, "claim").claim
    controls.store.append(
        old.model_copy(update={"claim_id": "mixed", "refs": [*old.refs, RunRef(run_id="absent")]}),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    with pytest.raises(ValueError, match="unavailable calculation"):
        verify._study(controls, sid, "mixed")
    assert not kernel.executed


async def test_empty_retry_supersedes_old_claim_in_bundle(tmp_path):
    controls, sid, _ = seed(tmp_path)
    claim = verify._study(controls, sid, "claim").claim
    start = controls.store.append(
        ev.ReviewStarted(
            review_id="retry", reviewer_model="test", target_event_id=claim.source_event_id
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    controls.store.append(
        ev.ReviewFinished(review_id="retry", finding_count=0),
        session_id=sid,
        actor=Actor.REVIEWER,
        causation_id=start.id,
    )
    result = await verify.export_bundle(controls, sid)
    with ZipFile(verify.bundle_file(controls, sid, result["id"])) as archive:
        assert json.loads(archive.read("review/claims.json")) == []
        assert "no checkable claims" in archive.read("review/limitations.json").decode()


async def test_legacy_download_and_local_file_metadata_are_not_exported(tmp_path):
    from bioquorum.core.enums import PermissionClass

    controls, sid, _ = seed(tmp_path)
    private = "fixture-private-value"
    url = "https://example.org/counts?session=" + private
    for call, tool, body in [
        ("file", "read_file", f"path: {tmp_path}/notes\nprivate unrelated file"),
        ("download", "download_file", "source: " + url),
    ]:
        if tool == "download_file":
            controls.store.append(
                ev.ToolCallRequested(
                    call_id=call, tool=tool, args={"url": url}, permission=PermissionClass.NETWORK
                ),
                session_id=sid,
                actor=Actor.AGENT,
            )
        controls.store.append(
            ev.ToolCallFinished(
                call_id=call,
                tool=tool,
                result=ToolResultPart(call_id=call, content=[TextPart(text=body)]),
            ),
            session_id=sid,
            actor=Actor.TOOL,
        )
    result = await verify.export_bundle(controls, sid)
    with ZipFile(verify.bundle_file(controls, sid, result["id"])) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            assert private.encode() not in data and str(tmp_path).encode() not in data
        assert b"unrelated file" not in archive.read("review/sources.json")
        assert b"https://example.org/counts" in archive.read("Methods.md")


async def test_empty_retrieval_and_error_result_reach_audit_and_export(tmp_path):
    from bioquorum.application.record import audit_log

    controls, sid, _ = seed(tmp_path)
    for call, text, failed in (
        ("empty", '{"results":[]}', False),
        ("timeout", "service detail", True),
    ):
        controls.store.append(
            ev.ToolCallFinished(
                call_id=call,
                tool="lit__search",
                result=ToolResultPart(call_id=call, content=[TextPart(text=text)], is_error=failed),
            ),
            session_id=sid,
            actor=Actor.TOOL,
        )
    audit = audit_log(controls, sid, before=None, limit=20)["entries"]
    assert audit[0]["details"]["is_error"] and audit[1]["details"]["empty_result"]
    assert "service detail" not in str(audit)
    result = await verify.export_bundle(controls, sid)
    with ZipFile(verify.bundle_file(controls, sid, result["id"])) as archive:
        gaps = json.loads(archive.read("review/limitations.json"))
        assert {g["call_id"] for g in gaps} == {"empty", "timeout"}


async def test_bundle_keeps_file_bound_in_an_unreviewed_answer(tmp_path):
    controls = ResearchControls(workspace.init_project(tmp_path))
    sid = controls.new_session("Research")
    quote = "Samples were processed on arrival."
    controls.store.append(
        ev.ToolCallFinished(
            call_id="source",
            tool="read_file",
            result=ToolResultPart(
                call_id="source",
                content=[TextPart(text=f"path: {tmp_path}/protocol.txt\n---\n{quote}")],
            ),
        ),
        session_id=sid,
        actor=Actor.TOOL,
    )
    controls.store.append(
        ev.AssistantMessage(message=Message.assistant(f'Handling: [@s1 "{quote}"].')),
        session_id=sid,
        actor=Actor.AGENT,
    )
    result = await verify.export_bundle(controls, sid)
    with ZipFile(verify.bundle_file(controls, sid, result["id"])) as archive:
        source = json.loads(archive.read("review/sources.json"))["source"]
        assert "protocol.txt" in source and quote in source and str(tmp_path) not in source
