"""Build a research-bundle manifest v0 from the event log (ARCHITECTURE.md §6.3, ADR-010).

A pure function over events: no database access, so it works the same on a live store,
an exported ``conversation.jsonl`` or a test stream. Bundle export (files on disk) comes in
v0.2; ``files`` stays empty until then.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from bioquorum.core import events as ev
from bioquorum.core.bundle import (
    Manifest,
    ManifestArtifact,
    ManifestModel,
    ManifestProject,
    ManifestRun,
)
from bioquorum.core.enums import Classification
from bioquorum.core.ids import utc_now
from bioquorum.core.status import VerificationStatus


def build_manifest(
    events: Iterable[ev.Event],
    *,
    project_name: str,
    classification: Classification,
    tool_version: str,
    created_at: datetime | None = None,
) -> Manifest:
    runs: dict[str, ManifestRun] = {}
    artifacts: dict[str, ManifestArtifact] = {}
    models: dict[tuple[str, str, str], ManifestModel] = {}

    for e in events:
        p = e.parsed()
        if isinstance(p, ev.RunStarted):
            runs[p.run_id] = ManifestRun(
                run_id=p.run_id,
                language=p.language,
                code_path=f"runs/{p.run_id}/code.{_ext(p.language)}",
                code_hash=p.code_hash,
                env_lock_hash=p.env_lock_hash,
                inputs=list(p.inputs),
            )
        elif isinstance(p, ev.RunFinished) and p.run_id in runs:
            runs[p.run_id] = runs[p.run_id].model_copy(
                update={"outputs": list(p.outputs), "run_key": p.run_key}
            )
        elif isinstance(p, ev.ArtifactVersionCreated):
            artifacts[p.version_id] = ManifestArtifact(
                artifact_id=p.artifact_id,
                version_id=p.version_id,
                name=p.name,
                kind=p.kind,
                version=p.version,
                content_hash=p.content_hash,
                path=f"artifacts/{p.name}",
                run_id=p.run_id,
                status=VerificationStatus.UNVERIFIED,
            )
        elif isinstance(p, ev.ArtifactStatusChanged) and p.version_id in artifacts:
            artifacts[p.version_id] = artifacts[p.version_id].model_copy(
                update={"status": p.to_status}
            )
        elif isinstance(p, ev.ModelCallFinished):
            key = (p.provider, p.model, p.role.value)
            models.setdefault(key, ManifestModel(provider=p.provider, model=p.model, role=key[2]))

    return Manifest(
        tool_version=tool_version,
        created_at=created_at or utc_now(),
        project=ManifestProject(name=project_name, classification=classification),
        runs=list(runs.values()),
        artifacts=list(artifacts.values()),
        models_used=list(models.values()),
    )


def _ext(language: str) -> str:
    return {"python": "py", "r": "R"}.get(language, "txt")
