from bioquorum.core import events as ev
from bioquorum.core.bundle import Manifest
from bioquorum.core.enums import Actor, ArtifactKind, Classification, ModelRole
from bioquorum.core.hashing import code_hash, run_key, sha256_text
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.provenance import build_manifest


def test_manifest_from_events(store):
    sid = "S"
    code = "print(1)\n"
    store.append(
        ev.RunStarted(run_id="R", kernel_id="k", code=code, code_hash=code_hash(code)),
        session_id=sid,
        actor=Actor.TOOL,
    )
    key = run_key(code_hash(code), None, [])
    store.append(ev.RunFinished(run_id="R", run_key=key), session_id=sid, actor=Actor.TOOL)
    store.append(
        ev.ArtifactVersionCreated(
            artifact_id="A",
            version_id="V",
            project_id="P",
            name="umap.png",
            kind=ArtifactKind.FIGURE,
            version=1,
            content_hash=sha256_text("png"),
            run_id="R",
            event_range=(0, 1),
        ),
        session_id=sid,
        actor=Actor.TOOL,
    )
    store.append(
        ev.ArtifactStatusChanged(
            version_id="V",
            from_status=VerificationStatus.UNVERIFIED,
            to_status=VerificationStatus.REVIEWED,
            trigger=StatusTrigger.REVIEW_PASSED,
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    for i in range(2):
        store.append(
            ev.ModelCallFinished(
                call_id=f"M{i}",
                provider="anthropic",
                model="m",
                role=ModelRole.WORKER,
                classification=Classification.PUBLIC,
            ),
            session_id=sid,
            actor=Actor.AGENT,
        )

    m = build_manifest(
        store.read(sid),
        project_name="p",
        classification=Classification.PUBLIC,
        tool_version="0.0.1",
    )
    assert isinstance(m, Manifest)
    (run,) = m.runs
    assert run.code_path == "runs/R/code.py" and run.run_key == key
    (art,) = m.artifacts
    assert art.status is VerificationStatus.REVIEWED and art.run_id == "R"
    assert len(m.models_used) == 1
    Manifest.model_validate_json(m.model_dump_json())  # JSON round-trip
