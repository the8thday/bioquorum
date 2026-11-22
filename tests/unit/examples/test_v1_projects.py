from pathlib import Path

import pytest
from examples.v1.prepare import prepare

from bioquorum.core.hashing import sha256_file


@pytest.mark.anyio
async def test_demo_refuses_to_replace_an_existing_project(tmp_path: Path):
    user_file = tmp_path / "notes.txt"
    user_file.write_text("Keep this")
    with pytest.raises(ValueError, match="empty"):
        await prepare("target", tmp_path)
    assert user_file.read_text() == "Keep this"


def test_omics_snapshot_matches_the_signed_off_handoff():
    root = Path(__file__).resolve().parents[3] / "examples/v1/data"
    assert (
        sha256_file(root / "paired-samples.csv")
        == "61f31cfe06dfd687805656cea00dfce80aef242304debc453f169a859efd658f"
    )
    assert (
        sha256_file(root / "paired-counts.csv")
        == "7550acff0644d93db619279db4e72c6aa7a24c079d68a4d2baea87be050090d6"
    )


@pytest.mark.parametrize("scenario", ["target", "omics", "review", "correction"])
def test_distributed_project_replays_without_kernel_or_model(tmp_path, scenario):
    from zipfile import ZipFile

    from bioquorum.application import record, workspace
    from bioquorum.application.controls import ResearchControls
    from bioquorum.core import events as ev
    from bioquorum.storage.rebuild import rebuild, snapshot

    root = Path(__file__).resolve().parents[3]
    with ZipFile(root / f"examples/v1/projects/{scenario}.zip") as archive:
        archive.extractall(tmp_path)
    controls = ResearchControls(workspace.load(tmp_path, global_config=tmp_path / "unused.toml"))
    db = controls.store.db
    previous = snapshot(db)
    assert rebuild(db) > 0
    assert snapshot(db) == previous
    sessions = controls.sessions()
    assert any(record.snapshot(controls, s["id"])["answers"] for s in sessions)
    values = {
        v.name: v.value
        for e in controls.store.iter_all()
        if isinstance(p := e.parsed(), ev.RunFinished)
        for v in p.values
    }
    expected = {
        "target": ("pasi_gap", 45.7),
        "omics": ("donors", 8),
        "review": ("excerpts", 3),
        "correction": ("control_mean", 8),
    }
    if scenario in ("target", "omics"):
        code = (root / f"examples/v1/data/{scenario}.py").read_text()
        assert any(
            isinstance(run := e.parsed(), ev.RunStarted) and run.code == code
            for e in controls.store.iter_all()
        )
    key, value = expected[scenario]
    assert values[key] == value


@pytest.mark.anyio
async def test_locked_demo_requires_a_provisioned_environment(tmp_path):
    with pytest.raises(ValueError, match="locked pixi"):
        await prepare("target", tmp_path, locked=True)
    assert not (tmp_path / ".bioquorum").exists()


@pytest.mark.anyio
async def test_locked_demo_preserves_existing_user_files(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("Keep")
    with pytest.raises(ValueError, match="empty"):
        await prepare("target", tmp_path, locked=True)
    assert notes.read_text() == "Keep"
