import pytest

from bioquorum.core.enums import PermissionClass, PermissionScope, Trust
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.evidence import Evidence
from bioquorum.skills.loader import discover
from bioquorum.tools import ToolContext
from bioquorum.tools.invoke import invoke
from bioquorum.tools.skills import skill_tool

pytestmark = pytest.mark.anyio


async def test_activation_is_required_to_read_resources_and_is_not_evidence(store, tmp_path):
    root = tmp_path / "q-rule"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: q-rule\ndescription: A workflow.\n---\nRead the limits."
    )
    (root / "references" / "limits.md").write_text("Set the named project method.")
    tool = skill_tool(discover(tmp_path))
    assert tool.input_schema["properties"]["name"]["enum"] == ["q-rule"]
    permissions = PermissionPolicy()
    permissions.grant(
        PermissionClass.READ_FS, "project-skills", PermissionScope.SESSION, session_id="S"
    )
    ctx = ToolContext(store=store, session_id="S", permissions=permissions)
    payload = {"name": "q-rule", "resource": "references/limits.md"}
    assert (await invoke(tool, payload, ctx)).is_error
    opened = await invoke(tool, {"name": "q-rule"}, ctx)
    assert not opened.is_error and opened.trust is Trust.UNTRUSTED and "SHA-256:" in opened.text()
    reference = await invoke(tool, payload, ctx)
    assert not reference.is_error
    assert "named project method" in reference.text()
    assert not Evidence.from_events(store.read("S")).sources
