"""Register files as research-line materials and retrieve specific historical versions."""

from pathlib import Path

from bioquorum.application.controls import ResearchControls
from bioquorum.application.project_tools import invoke_project
from bioquorum.core.enums import Classification
from bioquorum.policy.classification import allows
from bioquorum.storage import materials
from bioquorum.tools.materials import REGISTER, SUPERSEDE, WITHDRAW, flag_affected


def catalog(controls: ResearchControls, program_id: str) -> list[dict]:
    return materials.list_versions(controls.store.db, controls.ws.project_id, program_id)


def _version(controls: ResearchControls, program_id: str, version_id: str) -> dict:
    return materials.version(controls.store.db, controls.ws.project_id, program_id, version_id)


def download(controls: ResearchControls, program_id: str, version_id: str) -> tuple[Path, str]:
    row = _version(controls, program_id, version_id)
    if not allows(Classification(row["classification"]), controls.ws.classification):
        raise ValueError("The material exceeds the project clearance")
    return controls.ws.cas().verified_path(row["hash"]), Path(row["name"]).name


async def register(
    controls: ResearchControls, program_id: str, file_id: str, kind: str, source: str
) -> dict:
    version_id = await invoke_project(
        controls, REGISTER, dict(program_id=program_id, file_id=file_id, kind=kind, source=source)
    )
    return _version(controls, program_id, version_id)


def _affected_claims(
    controls: ResearchControls, program_id: str, version_id: str
) -> tuple[str, ...]:
    from bioquorum.application.program_lineage import downstream

    controls.ensure_idle()
    try:
        return tuple(row["id"] for row in downstream(controls, program_id, version_id)["claims"])
    except LookupError as exc:
        raise ValueError(str(exc)) from None


async def supersede(
    controls: ResearchControls,
    program_id: str,
    version_id: str,
    file_id: str,
    source: str,
    reason: str,
) -> dict:
    new_id = await invoke_project(
        controls,
        flag_affected(SUPERSEDE, _affected_claims(controls, program_id, version_id)),
        dict(
            program_id=program_id,
            version_id=version_id,
            file_id=file_id,
            source=source,
            reason=reason,
        ),
    )
    return _version(controls, program_id, new_id)


async def withdraw(
    controls: ResearchControls, program_id: str, version_id: str, reason: str
) -> dict:
    await invoke_project(
        controls,
        flag_affected(WITHDRAW, _affected_claims(controls, program_id, version_id)),
        dict(program_id=program_id, version_id=version_id, reason=reason),
    )
    return _version(controls, program_id, version_id)
