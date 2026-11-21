import pytest

from bioquorum.core.enums import PermissionClass
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.tools import ToolContext, invoke
from bioquorum.tools.materials import REGISTER


@pytest.mark.anyio
async def test_agent_cannot_register_material_even_with_a_write_grant(store):
    context = ToolContext(
        store=store, session_id=None, project_id="P", permissions=PermissionPolicy.allow_all()
    )
    assert REGISTER.permission is PermissionClass.WRITE_FS
    result = await invoke(
        REGISTER, dict(program_id="line", file_id="file", kind="other", source="Supplier"), context
    )
    assert result.is_error and "Only a researcher" in result.text()
    assert [e.type for e in store.iter_all()] == ["tool_call_requested", "tool_call_failed"]
