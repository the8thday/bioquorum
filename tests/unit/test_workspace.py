import pytest
from typer.testing import CliRunner

from bioquorum.application import workspace as wsp
from bioquorum.cli.main import app
from bioquorum.core.enums import Classification, ModelRole, PermissionClass
from bioquorum.policy.permissions import PermissionRequest
from bioquorum.storage.events import EventStore
from bioquorum.storage.rebuild import snapshot

runner = CliRunner()


def test_init_load_and_project_event(tmp_path):
    ws = wsp.init_project(tmp_path, name="ibd", classification=Classification.PUBLIC)
    loaded = wsp.load(tmp_path)
    assert loaded.project_id == ws.project_id and loaded.classification is Classification.PUBLIC
    (row,) = snapshot(ws.database())["projects"]
    assert row["name"] == "ibd" and row["root_dir"] == str(tmp_path.resolve())
    with pytest.raises(wsp.WorkspaceError):
        wsp.init_project(tmp_path)


def test_found_from_subdirectory_and_config(tmp_path):
    wsp.init_project(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "bioquorum.toml").write_text(
        '[project]\ndata_dirs = ["data"]\n'
        '[mcp.servers.ot]\nurl = "https://x/mcp"\n'
        '[mcp.servers.biomcp]\ncommand = "biomcp"\nclearance = "local"\n'
    )
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    ws = wsp.load(sub)
    assert ws.root == tmp_path.resolve() and ws.data_dirs == [(tmp_path / "data").resolve()]
    assert {s.name: s.clearance for s in ws.mcp_servers()} == {
        "ot": Classification.PUBLIC,
        "biomcp": None,
    }


def test_no_project_is_an_error(tmp_path):
    with pytest.raises(wsp.WorkspaceError):
        wsp.load(tmp_path)


def test_default_permissions(tmp_path):
    ws = wsp.init_project(tmp_path)
    servers = [wsp.ServerConfig(name="ot", url="https://x/mcp")]
    policy = wsp.default_permissions(ws, allow=[], servers=servers)

    def ok(perm, target):
        req = PermissionRequest(perm, target, session_id="S", project_id=ws.project_id)
        return policy.lookup(req) is not None

    assert ok(PermissionClass.READ_FS, str(ws.root / "x.csv"))
    assert not ok(PermissionClass.READ_FS, "/etc/passwd")
    assert ok(PermissionClass.EXECUTE, "python")
    assert ok(PermissionClass.EXTERNAL_MCP, "mcp:ot")
    assert not ok(PermissionClass.EXTERNAL_MCP, "mcp:unknown")
    assert not ok(PermissionClass.NETWORK, "example.org")
    allowed = wsp.default_permissions(ws, allow=[PermissionClass.NETWORK], servers=[])
    assert allowed.lookup(PermissionRequest(PermissionClass.NETWORK, "example.org")) is not None


def test_start_session_writes_event(tmp_path):
    ws = wsp.init_project(tmp_path)
    store = EventStore(ws.database())
    sid = wsp.start_session(ws, store, "t")
    assert store.read(sid)[0].type == "session_created"


def test_cli_project_init_and_show(tmp_path):
    r = runner.invoke(app, ["project", "init", "-C", str(tmp_path), "--classification", "public"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["project", "show", "-C", str(tmp_path)])
    assert r.exit_code == 0 and "public" in r.output
    assert runner.invoke(app, ["project", "show", "-C", str(tmp_path / "..")]).exit_code == 1


def test_cli_tools_lists_builtins():
    r = runner.invoke(app, ["tools"])
    assert r.exit_code == 0 and "python_execute" in r.output


def test_global_config_merged_under_project(tmp_path, monkeypatch):
    g = tmp_path / "global.toml"
    g.write_text(
        '[providers.deepseek]\n[providers.openai]\n[roles]\nworker = "deepseek:deepseek-v4-pro"\n'
    )
    monkeypatch.setenv("BQ_CONFIG", str(g))
    proj = tmp_path / "p"
    proj.mkdir()
    wsp.init_project(proj, classification=Classification.PUBLIC)
    (proj / "bioquorum.toml").write_text('[roles]\nreviewer = "openai:gpt-6.1-sol"\n')
    cfg = wsp.load(proj).models_config()
    assert set(cfg.providers) == {"deepseek", "openai"}
    assert [str(s) for s in cfg.roles[ModelRole.WORKER]] == ["deepseek:deepseek-v4-pro"]
    assert [str(s) for s in cfg.roles[ModelRole.REVIEWER]] == ["openai:gpt-6.1-sol"]
    r = runner.invoke(app, ["models", "-C", str(proj)])
    assert r.exit_code == 0 and "deepseek:deepseek-v4-pro" in r.output


def test_models_command_shows_confidential_block(tmp_path, monkeypatch):
    g = tmp_path / "global.toml"
    g.write_text('[providers.deepseek]\n[roles]\nworker = "deepseek:deepseek-v4-pro"\n')
    monkeypatch.setenv("BQ_CONFIG", str(g))
    (tmp_path / "c").mkdir()
    wsp.init_project(tmp_path / "c", classification=Classification.CONFIDENTIAL)
    r = runner.invoke(app, ["models", "-C", str(tmp_path / "c")])
    assert r.exit_code == 0 and "none allowed" in r.output


# ------------------------------------------------------------ kernel isolation (ADR-017)


def test_proxy_managed_public_projects_fail_closed_without_kernel_isolation(tmp_path, monkeypatch):
    from bioquorum.runtime.sandbox import IsolationUnavailable

    monkeypatch.setattr("bioquorum.application.workspace.bwrap_available", lambda: False)
    project = _project(tmp_path, Classification.PUBLIC)
    project.config = {"security": {"outbound_proxy": "http://127.0.0.1:8888"}}
    with pytest.raises(IsolationUnavailable):
        project.kernel_isolation()
    project.config["kernel"] = {"network": True}
    with pytest.raises(wsp.WorkspaceError, match="Kernel network is unavailable"):
        project.kernel_isolation()


def _project(tmp_path, classification, toml=""):
    from bioquorum.application import workspace as wsp

    wsp.init_project(tmp_path, classification=classification)
    (tmp_path / "bioquorum.toml").write_text(toml)
    return wsp.load(tmp_path, global_config=tmp_path / "no-global.toml")


def test_sandbox_protects_state_config_and_data(tmp_path):
    from bioquorum.core.enums import Classification

    (tmp_path / "raw").mkdir()
    ws = _project(tmp_path, Classification.INTERNAL, '[project]\ndata_dirs = ["raw"]\n')
    spec = ws.kernel_sandbox()
    assert spec.workdir == ws.root and spec.network is False
    assert ws.state_dir in spec.hidden
    assert ws.root / "bioquorum.toml" in spec.read_only
    assert ws.root / ".git" in spec.read_only
    assert (tmp_path / "raw").resolve() in spec.read_only


def test_only_public_projects_may_run_unisolated(tmp_path, monkeypatch):
    import pytest

    from bioquorum.application import workspace as wsp
    from bioquorum.core.enums import Classification
    from bioquorum.runtime.sandbox import Isolation, IsolationUnavailable

    monkeypatch.setattr(wsp, "bwrap_available", lambda: False)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    public = _project(tmp_path / "a", Classification.PUBLIC)
    assert public.kernel_isolation() is Isolation.NONE
    for level in (Classification.INTERNAL, Classification.CONFIDENTIAL):
        d = tmp_path / level.value
        d.mkdir()
        with pytest.raises(IsolationUnavailable):
            _project(d, level).kernel_isolation()

    monkeypatch.setattr(wsp, "bwrap_available", lambda: True)
    assert (
        _project(tmp_path / "b", Classification.CONFIDENTIAL).kernel_isolation() is Isolation.BWRAP
    )


def test_kernel_network_is_a_public_only_choice(tmp_path, monkeypatch):
    import pytest

    from bioquorum.application import workspace as wsp
    from bioquorum.core.enums import Classification
    from bioquorum.runtime.sandbox import Isolation

    monkeypatch.setattr(wsp, "bwrap_available", lambda: True)
    toml = "[kernel]\nnetwork = true\n"
    (tmp_path / "pub").mkdir()
    (tmp_path / "int").mkdir()
    assert (
        _project(tmp_path / "pub", Classification.PUBLIC, toml).kernel_isolation()
        is Isolation.BWRAP_NET
    )
    with pytest.raises(wsp.WorkspaceError):
        _project(tmp_path / "int", Classification.INTERNAL, toml).kernel_isolation()


def test_agent_binding_defaults_and_retry_opt_in(tmp_path):
    ws = wsp.init_project(tmp_path)
    assert ws.agent_options() == wsp.AgentOptions(binding=True, binding_retries=0)
    (tmp_path / "bioquorum.toml").write_text("[agent]\nbinding = false\nbinding_retries = 2\n")
    assert wsp.load(tmp_path).agent_options() == wsp.AgentOptions(binding=False, binding_retries=2)
    (tmp_path / "bioquorum.toml").write_text("[agent]\nbinding_retries = 5\n")
    with pytest.raises(wsp.WorkspaceError):
        wsp.load(tmp_path).agent_options()  # above the cap of 2 (ARCHITECTURE.md §6.5)
