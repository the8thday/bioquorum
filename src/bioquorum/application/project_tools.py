"""Invoke local researcher commands in the project event stream, including empty projects."""

from typing import Any

from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Actor, PermissionClass, PermissionScope
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.tools import ToolContext, ToolSpec, invoke


async def invoke_project(
    controls: ResearchControls, tool: ToolSpec[None], arguments: dict[str, Any]
) -> str:
    if controls.demo:
        raise ValueError("Open a research project to change programs or materials")
    if tool.permission is not PermissionClass.WRITE_FS or tool.target is not None:
        raise ValueError("Expected a local project command with a fixed target")
    ws = controls.ws
    policy = PermissionPolicy()
    policy.grant(tool.permission, tool.name, PermissionScope.PROJECT, project_id=ws.project_id)
    ctx = ToolContext(
        store=controls.store,
        session_id=None,
        permissions=policy,
        project_id=ws.project_id,
        classification=ws.classification,
        actor=Actor.USER,
        workdir=ws.root,
        cas=ws.cas(),
    )
    result = await invoke(tool, arguments, ctx)
    if result.is_error:
        raise ValueError(result.text())
    return result.text()
