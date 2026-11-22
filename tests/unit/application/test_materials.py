import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bioquorum.application import artifacts, materials
from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.ids import new_id
from bioquorum.storage.rebuild import rebuild, snapshot


@pytest.fixture
def project(tmp_path):
    controls = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    program_id = new_id()
    controls.store.append(
        ev.ProgramCreated(
            project_id=controls.ws.project_id, program_id=program_id, name="Source review"
        ),
        session_id=None,
        actor=Actor.USER,
    )
    return controls, program_id, controls.new_session("Source uploads")


async def upload(controls, sid, raw):
    async def stream():
        yield raw

    row = await artifacts.upload(controls, sid, "samples.csv", Classification.PUBLIC, stream())
    return row["id"]


@pytest.mark.anyio
async def test_revision_withdrawal_and_rebuild(project):
    controls, program, sid = project
    first = await upload(controls, sid, b"sample,group\nA,1\n")
    second = await upload(controls, sid, b"sample,group\nA,2\n")
    v1 = await materials.register(controls, program, first, "vendor_delivery", "Original delivery")
    v2 = await materials.supersede(
        controls, program, v1["id"], second, "Revised delivery", "Label correction"
    )
    assert [v1["version"], v2["version"]] == [1, 2]
    assert v1["material_id"] == v2["material_id"] and v1["hash"] != v2["hash"]
    withdrawn = await materials.withdraw(controls, program, v2["id"], "Vendor recall")
    assert withdrawn["status"] == "withdrawn" and withdrawn["withdrawal_reason"] == "Vendor recall"
    rows = materials.catalog(controls, program)
    assert [row["status"] for row in rows] == ["superseded", "withdrawn"]
    assert rows[1]["source"] == "Revised delivery" and rows[1]["reason"] == "Label correction"
    assert rows[0]["kind"] == rows[1]["kind"] == "vendor_delivery"
    assert all(row["registered_at"] and row["registered_by"] == "user" for row in rows)
    for row, raw in zip(rows, [b"sample,group\nA,1\n", b"sample,group\nA,2\n"], strict=True):
        path, filename = materials.download(controls, program, row["id"])
        assert filename == "samples.csv" and path.read_bytes() == raw
        assert row["hash"] == sha256_bytes(raw)
    changes = [event for event in controls.store.iter_all() if event.type.startswith("material_")]
    assert len(changes) == 3
    assert all(event.session_id is None and event.actor is Actor.USER for event in changes)
    assert all(
        controls.store.get(event.causation_id).type == "tool_call_requested" for event in changes
    )
    before = snapshot(controls.store.db)
    rebuild(controls.store.db)
    assert snapshot(controls.store.db) == before
    assert materials.catalog(controls, program) == rows
    with pytest.raises(ValueError):
        await materials.supersede(controls, program, v2["id"], first, "Replacement", "Stale")
    controls.ws.cas().path(v2["hash"]).write_bytes(b"changed")
    with pytest.raises(LookupError):
        materials.download(controls, program, v2["id"])


@pytest.mark.anyio
async def test_material_scope_and_validation(project):
    controls, program, sid = project
    upload_id = await upload(controls, sid, b"study")
    other = new_id()
    controls.store.append(
        ev.ProgramCreated(project_id=controls.ws.project_id, program_id=other, name="Other"),
        session_id=None,
        actor=Actor.USER,
    )
    v1 = await materials.register(controls, program, upload_id, "other", "Internal")
    with pytest.raises(ValueError):
        await materials.withdraw(controls, other, v1["id"], "Wrong program")
    assert materials.catalog(controls, other) == []
    with pytest.raises(LookupError):
        materials.download(controls, other, v1["id"])
    foreign = ev.FileRegistered(
        project_id=new_id(),
        file_id=new_id(),
        filename="samples.csv",
        ref=controls.ws.cas().put_bytes(b"foreign"),
        path="upload-other",
        classification=Classification.PUBLIC,
    )
    controls.store.append(foreign, session_id=sid, actor=Actor.USER)
    with pytest.raises(ValueError):
        await materials.register(controls, program, foreign.file_id, "other", "Rejected")
    credential = "sk-" + "dummy" * 6
    with pytest.raises(ValueError):
        await materials.register(controls, program, upload_id, "other", credential)
    with pytest.raises(ValueError):
        await materials.withdraw(controls, program, v1["id"], "   ")
    assert credential not in json.dumps([event.payload for event in controls.store.iter_all()])
    assert len(materials.catalog(controls, program)) == 1


@pytest.mark.anyio
async def test_concurrent_corrections_have_one_winner(project):
    controls, program, sid = project
    original = await upload(controls, sid, b"original")
    v1 = await materials.register(controls, program, original, "experimental_data", "Experiment")
    files = [await upload(controls, sid, raw) for raw in (b"first", b"second")]

    def compete(file_id):
        try:
            return asyncio.run(
                materials.supersede(controls, program, v1["id"], file_id, "Updated", "Correction")
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, files))
    assert sum(row is not None for row in results) == 1
    assert [row["version"] for row in materials.catalog(controls, program)] == [1, 2]


@pytest.mark.anyio
async def test_unchanged_registration_state_on_failed_writes(project):
    controls, program, sid = project
    first = await upload(controls, sid, b"original")
    lost = await upload(controls, sid, b"lost")
    controls.ws.cas().path(sha256_bytes(b"lost")).unlink()
    with pytest.raises(ValueError):
        await materials.register(controls, program, lost, "literature", "Study supplement")

    v1 = await materials.register(controls, program, first, "literature", "Study supplement")
    with pytest.raises(ValueError):
        await materials.register(controls, program, first, "literature", "Study supplement")
    with pytest.raises(ValueError):
        await materials.supersede(controls, program, v1["id"], first, "Duplicate", "Retry")
    with pytest.raises(ValueError):
        await materials.supersede(controls, program, v1["id"], lost, "Correction", "Retry")

    demo = ResearchControls(controls.ws, demo=True)
    with pytest.raises(ValueError):
        await materials.withdraw(demo, program, v1["id"], "Invalid demo mutation")
    controls.store.append(
        ev.ProgramUpdated(project_id=controls.ws.project_id, program_id=program, closed=True),
        session_id=None,
        actor=Actor.USER,
    )
    with pytest.raises(ValueError):
        await materials.withdraw(controls, program, v1["id"], "Closed")
    assert materials.catalog(controls, program) == [v1]
    changes = [
        event.type for event in controls.store.iter_all() if event.type.startswith("material_")
    ]
    assert changes == ["material_registered"]
