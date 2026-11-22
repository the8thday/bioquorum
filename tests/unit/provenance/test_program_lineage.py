from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, DataKind
from bioquorum.core.hashing import code_hash, sha256_bytes
from bioquorum.core.refs import DataRef, NamedValue, ValueRef
from bioquorum.provenance.program_lineage import trace

DIGEST = sha256_bytes(b"identical bytes")
REF = DataRef(uri="cas://" + DIGEST, content_hash=DIGEST, size=15)


def event(payload, sid):
    return ev.Event.build(payload, session_id=sid, seq=0, actor=Actor.TOOL)


def start(rid, path, *, verification=False, source=REF, kernel=None):
    return ev.RunStarted(
        run_id=rid,
        kernel_id=kernel or rid,
        code="sample",
        code_hash=code_hash("sample"),
        inputs=[source],
        input_paths=[path],
        verification_run=verification,
    )


def test_identical_material_hashes_require_recorded_identity():
    versions = [
        {"id": "old", "hash": DIGEST, "upload_path": "v1.csv"},
        {"id": "new", "hash": DIGEST, "upload_path": "v2.csv"},
    ]
    events = [
        event(start("first", "v1.csv"), "A"),
        event(start("copied", "alias.csv"), "A"),
        event(
            ev.RecomputeStarted(
                project_id="P",
                program_id="L",
                material_version_id="old",
                replacement_version_id="new",
                session_id="B",
                recompute_id="op",
                run_ids=["first"],
                claim_ids=[],
            ),
            None,
        ),
        event(start("corrected", "v1.csv"), "B"),
        event(start("weak", "v2.csv", source=REF.model_copy(update={"weak_hash": True})), "B"),
    ]
    graph = trace(events, versions, {})
    for version, expected in [("old", "first"), ("new", "corrected")]:
        impact = graph.downstream(version)
        assert [r["id"] for r in impact["runs"]] == [expected]
        assert [r["run_id"] for r in impact["ambiguous_inputs"]] == ["copied"]


def test_private_replay_does_not_replace_shared_file_producer():
    digest = sha256_bytes(b"output")
    output = DataRef(uri="cas://" + digest, content_hash=digest, size=6, kind=DataKind.FIGURE)
    versions = [{"id": "v1", "hash": DIGEST, "upload_path": "uploaded.csv"}]
    events = [
        event(start("producer", "uploaded.csv"), "A"),
        event(
            ev.LineageObserved(
                run_id="producer",
                function="declared_write",
                direction="output",
                path="shared.csv",
                post_run_hash=digest,
            ),
            "A",
        ),
        event(
            ev.RunFinished(
                run_id="producer",
                run_key="success",
                outputs=[output],
                values=[NamedValue(name="count", value=3)],
            ),
            "A",
        ),
        event(start("isolated", "other.csv", source=output, verification=True), "B"),
        event(
            ev.LineageObserved(
                run_id="isolated",
                function="declared_write",
                direction="output",
                path="shared.csv",
                post_run_hash=digest,
            ),
            "B",
        ),
        event(ev.RunFinished(run_id="isolated", run_key="success"), "B"),
        event(start("consumer", "shared.csv", source=output), "C"),
        event(
            ev.ClaimExtracted(
                claim_id="real",
                source_event_id="src",
                kind=ClaimKind.NUMERIC,
                text="A value",
                refs=[ValueRef(name="count", run_id="producer")],
            ),
            "A",
        ),
        event(
            ev.ClaimExtracted(
                claim_id="invalid",
                source_event_id="src",
                kind=ClaimKind.NUMERIC,
                text="No value",
                refs=[ValueRef(name="absent", run_id="producer")],
            ),
            "A",
        ),
    ]
    graph = trace(events, versions, {})
    linked = graph.downstream("v1")
    assert [r["id"] for r in linked["runs"]] == ["producer", "consumer"]
    assert [r["id"] for r in linked["claims"]] == ["real"]
    assert linked["figures"][0]["hash"] == digest


def test_artifact_figure_supersedes_raw_run_figure_without_duplication():
    from bioquorum.core.enums import ArtifactKind

    digest = sha256_bytes(b"figure bytes")
    fig = DataRef(uri="cas://" + digest, content_hash=digest, size=12, kind=DataKind.FIGURE)
    versions = [{"id": "v1", "hash": DIGEST, "upload_path": "uploaded.csv"}]
    events = [
        event(start("r1", "uploaded.csv"), "A"),
        event(ev.RunFinished(run_id="r1", run_key="k1", outputs=[fig]), "A"),
        event(
            ev.ArtifactVersionCreated(
                artifact_id="art1",
                version_id="ver1",
                project_id="P",
                name="plot.png",
                kind=ArtifactKind.FIGURE,
                version=1,
                content_hash=digest,
                run_id="r1",
                event_range=(0, 2),
            ),
            "A",
        ),
    ]
    linked = trace(events, versions, {}).downstream("v1")
    assert [f["id"] for f in linked["figures"]] == ["ver1"]
