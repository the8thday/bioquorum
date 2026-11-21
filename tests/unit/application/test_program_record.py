"""Cross-language lineage, immutable decisions and portable program handoffs."""

import io
import json
from zipfile import ZipFile

import pytest
from tests.unit.application.test_materials import upload

from bioquorum.application import materials, program_lineage, program_record, programs
from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification
from bioquorum.core.hashing import code_hash, sha256_bytes
from bioquorum.core.messages import Message
from bioquorum.core.refs import NamedValue, ValueRef


@pytest.fixture
async def study(tmp_path):
    controls = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    sid = controls.new_session("Original")
    pid = (await programs.create(controls, "Should we repeat the experiment?"))["id"]
    await programs.attach(controls, pid, sid)
    fid = await upload(controls, sid, b"original file")
    material = await materials.register(controls, pid, fid, "experimental_data", "Author")
    cas = controls.ws.cas()

    def add(payload):
        return controls.store.append(payload, session_id=sid, actor=Actor.TOOL)

    def run(rid, language, source, dest):
        ref = cas.put_bytes(source)
        output = cas.put_bytes(dest)
        add(
            ev.RunStarted(
                run_id=rid,
                kernel_id=language,
                language=language,
                code="x",
                code_hash=code_hash("x"),
                inputs=[ref],
                input_paths=[{"one": "input.csv", "two": "one.csv", "three": "two.csv"}[rid]],
            )
        )
        add(
            ev.LineageObserved(
                run_id=rid,
                path=rid + ".csv",
                direction="output",
                function="declared_write",
                post_run_hash=output.content_hash,
            )
        )
        add(
            ev.RunFinished(
                run_id=rid,
                run_key="key",
                values=[NamedValue(name="result" if rid == "three" else rid, value=13)],
            )
        )

    run("one", "python", b"original file", b"converted")
    run("two", "r", b"converted", b"counts")
    run("three", "python", b"counts", b"final")
    q = controls.store.append(
        ev.UserMessage(message=Message.user("Question")), session_id=sid, actor=Actor.USER
    )
    add(ev.CoveragePlanned(topics={"d": "Dosage"}))
    add(ev.CoverageClosed(gaps=["Dose is unavailable"], decisive="Check"))
    answer = add(ev.AssistantMessage(message=Message.assistant("Found {{result}}.")))
    add(
        ev.ClaimExtracted(
            claim_id="claim-A",
            source_event_id=answer.id,
            text="Found {{result}}.",
            kind=ClaimKind.NUMERIC,
            refs=[ValueRef(name="result", run_id="three")],
        )
    )
    return controls, pid, sid, material, q


@pytest.mark.anyio
async def test_hash_chain_and_fixed_decision(study):
    controls, pid, _sid, m, _ = study
    assert [r["id"] for r in program_lineage.downstream(controls, pid, m["id"])["runs"]] == [
        "one",
        "two",
        "three",
    ]
    assert [r["id"] for r in program_lineage.upstream(controls, pid, "claim-A")] == [m["id"]]
    decision = await program_record.save_decision(
        controls, pid, "Repeat the experiment", ["claim-A"]
    )
    assert decision["claims"][0]["text"] == "Found 13."
    assert decision["claims"][0]["revision"]
    second = await program_record.save_decision(
        controls, pid, "Order new data", ["claim-A"], decision["id"], decision["revision"]
    )
    with pytest.raises(ValueError):
        await program_record.save_decision(
            controls, pid, "Stale", ["claim-A"], decision["id"], decision["revision"]
        )
    assert [r["text"] for r in second["history"]] == ["Repeat the experiment", "Order new data"]
    assert program_lineage.downstream(controls, pid, m["id"])["decisions"]
    with pytest.raises((ValueError, LookupError)):
        await program_record.save_decision(controls, pid, "Other session", ["nonmember"])


@pytest.mark.anyio
async def test_five_session_handoff_and_verified_archive(study):
    controls, pid, _sid, m, _ = study
    for i in range(4):
        await programs.attach(controls, pid, controls.new_session("Follow-up " + str(i)))
    await program_record.save_decision(controls, pid, "Continue", ["claim-A"])
    summary = program_record.handoff(controls, pid)
    assert len(summary["sessions"]) == 5
    assert summary["question"] == "Should we repeat the experiment?"
    assert [g["text"] for g in summary["gaps"]] == ["Dose is unavailable"]
    assert summary["claims"][0]["status"] == "unverified"
    for item in [
        summary,
        *summary["sessions"],
        *summary["claims"],
        *summary["gaps"],
        *summary["materials"],
    ]:
        assert program_record.record(controls, pid, item["event_id"])["id"] == item["event_id"]
    exported = await program_record.export(controls, pid)
    with ZipFile(exported) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert all(
            sha256_bytes(archive.read(path)) == digest for path, digest in manifest["files"].items()
        )
        assert archive.read("objects/" + m["hash"]) == b"original file"
        nested = [p for p in archive.namelist() if p.startswith("sessions/")]
        assert len(nested) == 5
        assert "manifest.json" in ZipFile(io.BytesIO(archive.read(nested[0]))).namelist()
    assert program_lineage.downstream(controls, pid, m["id"])["bundles"]


@pytest.mark.anyio
async def test_project_routes_and_hidden_provider_payload(study):
    import httpx2

    from bioquorum.server.app import create_app

    controls, pid, sid, material, _ = study
    host = "127.0.0.1:8181"
    base = f"http://{host}"
    program = f"/api/programs/{pid}"
    transport = httpx2.ASGITransport(app=create_app(controls, host))
    async with httpx2.AsyncClient(
        transport=transport, base_url=base, headers={"Origin": base}
    ) as client:
        assert (await client.get(program + f"/materials/{material['id']}/dependencies")).json()[
            "runs"
        ]
        assert (await client.get(program + "/claims/claim-A/materials")).json()["materials"][0][
            "id"
        ] == material["id"]
        assert (await client.get(f"/api/sessions/{sid}/claims/claim-A/materials")).json()[
            "materials"
        ][0]["id"] == material["id"]
        save = await client.post(
            program + "/decisions", json={"text": "Proceed", "claim_ids": ["claim-A"]}
        )
        assert save.status_code == 201
        decision = save.json()
        data = await client.get(program + "/handoff/file")
        assert data.headers["content-disposition"].startswith("attachment")
        reference = await client.get(program + "/records/" + decision["revision"])
        assert reference.json()["details"]["text"] == "Proceed"
        secret = controls.store.append(
            ev.ErrorOccurred(where="private system", error="protected"),
            session_id=sid,
            actor=Actor.SYSTEM,
        )
        assert (await client.get(program + "/records/" + secret.id)).status_code == 404
        other = await programs.create(controls, "Separate research")
        assert (
            await client.get("/api/programs/" + other["id"] + "/records/" + decision["revision"])
        ).status_code == 404
        change = await client.post(
            program + f"/decisions/{decision['id']}/revise",
            json={
                "text": "Reconsider",
                "claim_ids": ["claim-A"],
                "prior_revision": decision["revision"],
            },
        )
        assert change.status_code == 201
        zipped = await client.post(program + "/bundle", json={})
        assert zipped.status_code == 200 and ZipFile(io.BytesIO(zipped.content)).testzip() is None


@pytest.mark.anyio
async def test_program_export_excludes_unattached_fork_ancestry(study):
    controls, pid, _, _, _ = study
    parent = controls.new_session("Outside this program")
    marker = "Private outside record QVN24"
    controls.store.append(
        ev.AssistantMessage(message=Message.assistant(marker)), session_id=parent, actor=Actor.AGENT
    )
    child = controls.fork(parent, "Attached branch")
    await programs.attach(controls, pid, child)
    exported = await program_record.export(controls, pid)
    with (
        ZipFile(exported) as archive,
        ZipFile(io.BytesIO(archive.read(f"sessions/{child}.zip"))) as nested,
    ):
        assert marker.encode() not in nested.read("conversation.jsonl")
        assert "ancestor" in nested.read("review/limitations.json").decode().lower()


@pytest.mark.anyio
async def test_decision_rejects_a_claim_changed_after_the_editor_opened(study):
    from bioquorum.application.controls import ConflictError
    from bioquorum.core.status import StatusTrigger, VerificationStatus

    controls, pid, sid, _, _ = study
    before = program_record.handoff(controls, pid)["claims"][0]
    controls.store.append(
        ev.ClaimStatusChanged(
            claim_id=before["id"],
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.GROUNDED,
            trigger=StatusTrigger.BINDINGS_RESOLVED,
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    with pytest.raises(ConflictError):
        await program_record.save_decision(
            controls,
            pid,
            "Approve",
            [before["id"]],
            claim_revisions={before["id"]: before["revision"]},
        )
    assert not program_record.handoff(controls, pid)["decisions"]


@pytest.mark.anyio
async def test_late_review_preserves_archived_decision_and_projection_replay(study):
    from bioquorum.core.status import StatusTrigger, VerificationStatus
    from bioquorum.storage.rebuild import rebuild, snapshot

    controls, pid, sid, m, _ = study
    original = await program_record.save_decision(
        controls, pid, "Preserve original choice", ["claim-A"]
    )
    controls.store.append(
        ev.ClaimStatusChanged(
            claim_id="claim-A",
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.GROUNDED,
            trigger=StatusTrigger.BINDINGS_RESOLVED,
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    await materials.withdraw(controls, pid, m["id"], "Vendor recalled input")
    summary = program_record.handoff(controls, pid)
    assert summary["claims"][0]["status"] == "grounded" and summary["claims"][0]["needs_review"]
    assert summary["pending"][0]["text"] == "Found 13."
    saved = summary["decisions"][0]
    assert saved["needs_review"] and saved["claims"] == original["claims"]
    assert saved["claims"][0]["status"] == "unverified"
    before = snapshot(controls.store.db)
    rebuild(controls.store.db)
    assert before == snapshot(controls.store.db) and summary == program_record.handoff(
        controls, pid
    )


@pytest.mark.anyio
async def test_snapshot_exports_only_captured_session_prefix(study, monkeypatch):
    from bioquorum.application import verify

    controls, pid, sid, _, _ = study
    actual = verify.export_bundle
    marker = "Message after enclosing handoff snapshot"

    async def append_then_export(controls, session, **kwargs):
        controls.store.append(
            ev.AssistantMessage(message=Message.assistant(marker)),
            session_id=session,
            actor=Actor.AGENT,
        )
        return await actual(controls, session, **kwargs)

    monkeypatch.setattr(verify, "export_bundle", append_then_export)
    path = await program_record.export(controls, pid)
    with (
        ZipFile(path) as archive,
        ZipFile(io.BytesIO(archive.read(f"sessions/{sid}.zip"))) as nested,
    ):
        assert marker.encode() not in archive.read("records.json")
        assert marker.encode() not in nested.read("conversation.jsonl")
