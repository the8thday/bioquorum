"""First-use diagnostics derived from configuration and recorded connector checks."""

from bioquorum.application.controls import ResearchControls
from bioquorum.application.settings import project_state
from bioquorum.core.enums import Classification, ModelRole
from bioquorum.models.routing import Router


def disconnected(controls: ResearchControls) -> list[str]:
    _, checks = project_state(controls)
    return [
        s.name
        for s in controls.ws.mcp_servers()
        if checks.get(s.name, {}).get("status") == "unavailable"
    ]


def overview(controls: ResearchControls) -> dict:
    ws = controls.ws
    cfg = ws.models_config()
    worker = "ready"
    if not cfg.roles.get(ModelRole.WORKER):
        worker = "unconfigured"
    elif not Router(cfg).chain(ModelRole.WORKER, ws.classification):
        worker = "restricted"
    return {
        "worker": worker,
        "needs_local": ws.classification == Classification.CONFIDENTIAL,
        "disconnected": disconnected(controls),
    }
