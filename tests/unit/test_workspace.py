import pytest
from typer.testing import CliRunner

from bioquorum.cli import workspace as wsp
from bioquorum.cli.main import app
from bioquorum.core.enums import Classification, PermissionClass
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
