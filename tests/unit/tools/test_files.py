import pytest

from bioquorum.core.enums import PermissionClass, PermissionScope
from bioquorum.policy import PermissionPolicy
from bioquorum.tools import default_registry, invoke

pytestmark = pytest.mark.anyio
REG = default_registry()


async def test_read_file_relative_to_workdir(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    part = await invoke(REG.get("read_file"), {"path": "a.txt"}, ctx)
    assert not part.is_error and part.text().endswith("hello")
    assert "sha256:" in part.text()


async def test_permission_target_is_the_resolved_path(ctx, tmp_path):
    allowed = tmp_path / "data"
    allowed.mkdir()
    (allowed / "x.csv").write_text("a,b")
    (tmp_path / "secret.txt").write_text("no")
    ctx.permissions = PermissionPolicy()
    ctx.permissions.grant(PermissionClass.READ_FS, str(allowed), PermissionScope.GLOBAL)
    ok = await invoke(REG.get("read_file"), {"path": "data/x.csv"}, ctx)
    assert not ok.is_error
    escape = await invoke(REG.get("read_file"), {"path": "data/../secret.txt"}, ctx)
    assert escape.is_error and "denied" in escape.text()


async def test_symlink_out_of_granted_dir_is_denied(ctx, tmp_path):
    allowed = tmp_path / "data"
    allowed.mkdir()
    (tmp_path / "secret.txt").write_text("no")
    (allowed / "link").symlink_to(tmp_path / "secret.txt")
    ctx.permissions = PermissionPolicy()
    ctx.permissions.grant(PermissionClass.READ_FS, str(allowed), PermissionScope.GLOBAL)
    part = await invoke(REG.get("read_file"), {"path": "data/link"}, ctx)
    assert part.is_error


async def test_binary_file_not_dumped(ctx, tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00")
    part = await invoke(REG.get("read_file"), {"path": "b.bin"}, ctx)
    assert "binary file" in part.text()


async def test_list_files(ctx, tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "f.txt").write_text("abc")
    part = await invoke(REG.get("list_files"), {"path": "."}, ctx)
    assert "d/" in part.text() and "f.txt\t3" in part.text()
