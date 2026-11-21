import json

import httpx2
import pytest

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project, load
from bioquorum.server.app import create_app
from bioquorum.storage.rebuild import rebuild


@pytest.mark.anyio
async def test_settings_are_event_backed_scoped_and_secret_free(tmp_path, monkeypatch):
    init_project(tmp_path, name="Settings")
    config = tmp_path / "bioquorum.toml"
    config.write_text(
        '[providers.openai]\napi_key="env:BQ_FIXTURE_KEY"\n[roles]\nworker="openai:fixture"\n'
    )
    monkeypatch.setenv("BQ_FIXTURE_KEY", "private-fixture-value")
    ws = load(tmp_path)
    controls = ResearchControls(ws)
    origin = "http://127.0.0.1:8181"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=create_app(controls, origin[7:])),
        base_url=origin,
        headers={"Origin": origin},
    ) as http:
        before = (await http.get("/api/settings")).json()
        assert before["keys"] == [{"reference": "env:BQ_FIXTURE_KEY", "state": "set"}]
        change = {"revision": before["revision"], "patch": {"agent": {"skills": True}}}
        result = await http.post("/api/settings", json=change)
        assert result.status_code == 200, result.text
        assert "private-fixture-value" not in result.text
        assert (await http.post("/api/settings", json=change)).status_code == 409
        for key in ("providers", "security"):
            bad = await http.post(
                "/api/settings",
                json={
                    "revision": result.json()["revision"],
                    "patch": {key: {"api_key": "private-fixture-value"}},
                },
            )
            assert bad.status_code == 400 and "private-fixture-value" not in bad.text
    assert load(tmp_path).agent_options().skills
    rebuild(ws.database())
    assert load(tmp_path).agent_options().skills
    assert "skills" not in config.read_text()
    assert all(
        "private-fixture-value" not in json.dumps(e.payload) for e in controls.store.iter_all()
    )


def test_role_prices_budget_and_revocations_take_effect_on_reopen(tmp_path):
    from bioquorum.application.settings import SettingsEdit, save
    from bioquorum.application.workspace import default_permissions
    from bioquorum.core.enums import ModelRole, PermissionClass

    init_project(tmp_path)
    (tmp_path / "bioquorum.toml").write_text(
        '[providers.openai]\n[roles]\nworker="openai:a"\n[models."openai:b"]\n'
    )
    controls = ResearchControls(load(tmp_path))
    save(
        controls,
        SettingsEdit(
            revision=None,
            patch={
                "roles": {"reviewer": "openai:b"},
                "models": {
                    "openai:b": {
                        "input_usd_per_million": 0,
                        "output_usd_per_million": 2,
                        "context_window": 4096,
                    }
                },
                "agent": {"budget_tokens": 1200, "plan_default": True},
                "permissions": {"revoked": [["execute", "python"]]},
            },
        ),
    )
    ws = load(tmp_path)
    cfg = ws.models_config()
    assert str(cfg.roles[ModelRole.REVIEWER][0]) == "openai:b"
    assert cfg.prices["openai:b"] == (0, 2) and cfg.context_windows["openai:b"] == 4096
    assert ws.agent_options().budget.tokens == 1200
    assert not any(
        g.permission == PermissionClass.EXECUTE
        for g in default_permissions(ws, allow=[], servers=[]).grants()
    )


@pytest.mark.anyio
async def test_workbench_preferences_follow_user_across_projects_and_ports(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for port in (8181, 8182):
        origin = f"http://127.0.0.1:{port}"
        controls = ResearchControls(init_project(tmp_path / str(port)))
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=create_app(controls, origin[7:])),
            base_url=origin,
            headers={"Origin": origin},
        ) as http:
            prefs = {"reading_size": "large", "initial_panel": "Usage"}
            if port == 8181:
                assert (await http.get("/api/workbench-settings")).json()[
                    "reading_size"
                ] == "standard"
                assert (await http.post("/api/workbench-settings", json=prefs)).status_code == 200
                assert (
                    await http.post("/api/workbench-settings", json={**prefs, "roles": {}})
                ).status_code == 400
            assert (await http.get("/api/workbench-settings")).json() == prefs


def test_settings_snapshot_replays_cleanly_from_base_config(tmp_path):
    from bioquorum.application.settings import SettingsEdit, save, snapshot

    init_project(tmp_path)
    (tmp_path / "bioquorum.toml").write_text('[providers.openai]\n[roles]\nworker="openai:a"\n')
    controls = ResearchControls(load(tmp_path))
    first = save(
        controls,
        SettingsEdit(
            revision=None,
            patch={"models": {"openai:a": {"context_window": 4096}}},
        ),
    )
    assert first["models"]["openai:a"]["context_window"] == 4096
    cleared = save(
        controls,
        SettingsEdit(
            revision=first["revision"],
            patch={"models": {"openai:a": {"context_window": None}}},
        ),
    )
    assert cleared["models"]["openai:a"]["context_window"] is None
    assert snapshot(controls)["models"]["openai:a"]["context_window"] is None


def test_project_library_skips_corrupted_project_metadata(tmp_path, monkeypatch):
    from pathlib import Path

    from bioquorum.application import project_library
    from bioquorum.core.enums import Classification
    from bioquorum.core.ids import new_id

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    current = init_project(tmp_path / "current", name="Current")
    good = project_library.create("Good Project", Classification.PUBLIC)
    broken_dir = project_library.library_root() / new_id() / ".bioquorum"
    broken_dir.mkdir(parents=True)
    (broken_dir / "project.json").write_text("{not valid json", encoding="utf-8")

    rows = project_library.entries(current)
    names = [r["name"] for r in rows]
    assert "Current" in names and good["name"] in names


def test_project_language_inventory_reads_only_configured_prefix(tmp_path):
    from bioquorum.application.environment import package_inventory
    from bioquorum.core.hashing import env_lock_hash

    prefix = tmp_path / ".pixi/envs/default"
    for name in ("bin/R", "bin/python", "share/jupyter/kernels/ir/kernel.json"):
        path = prefix / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    description = prefix / "lib/R/library/DESeq2/DESCRIPTION"
    description.parent.mkdir(parents=True)
    description.write_text("Package: DESeq2\nVersion: 1.44.0\n")
    (tmp_path / "pixi.lock").write_bytes(b"locked")
    enabled = package_inventory(tmp_path, use_pixi=True)
    assert enabled["lock_hash"] == env_lock_hash(b"locked")
    assert ("DESeq2", "1.44.0") in enabled["r_packages"]
    assert package_inventory(tmp_path, use_pixi=False)["r_packages"] == []
