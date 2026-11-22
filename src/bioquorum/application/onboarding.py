"""First-use diagnostics derived from configuration and recorded connector checks."""

from bioquorum.application import model_setup
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
    routes = Router(cfg).chain(ModelRole.WORKER, ws.classification)
    if worker == "ready" and not routes:
        worker = "restricted"
    selected = routes[0].provider if routes else None
    if selected is None and model_setup.starter(cfg, ws.classification, model_setup.OPENAI_MODEL):
        selected = model_setup.provider(cfg, "openai")
    return {
        "model": str(routes[0].spec) if routes else None,
        "credential": model_setup.credential(selected) if selected else None,
        "worker": worker,
        "needs_local": ws.classification == Classification.CONFIDENTIAL,
        "disconnected": disconnected(controls),
    }
