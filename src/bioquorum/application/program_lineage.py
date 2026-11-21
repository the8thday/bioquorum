"""Scoped reads of upstream and downstream evidence, including legacy session exports."""

import json
from zipfile import BadZipFile, ZipFile

from bioquorum.application import programs as program_views
from bioquorum.application import verify
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.hashing import sha256_bytes
from bioquorum.provenance.program_lineage import Lineage, trace
from bioquorum.storage import materials, programs


def _legacy_members(
    controls: ResearchControls, sid: str, p: ev.BundleExported
) -> tuple[set[str], set[str]] | None:
    try:
        path = verify.bundle_file(controls, sid, p.bundle_id)
        with ZipFile(path) as archive:
            if (
                archive.getinfo("manifest.json").file_size > 32 * 1024**2
                or archive.getinfo("review/claims.json").file_size > 32 * 1024**2
            ):
                return None
            raw = archive.read("manifest.json")
            if sha256_bytes(raw) != p.manifest_hash:
                return None
            manifest = json.loads(raw)
            raw_claims = archive.read("review/claims.json")
            if sha256_bytes(raw_claims) != manifest["files"]["review/claims.json"]:
                return None
            return {r["run_id"] for r in manifest["runs"]}, {
                c["claim_id"] for c in json.loads(raw_claims)
            }
    except (LookupError, OSError, ValueError, BadZipFile, TypeError):
        return None


def _graph(
    controls: ResearchControls, pid: str, *, include_exports: bool = True
) -> tuple[Lineage, list[dict], list[str]]:
    versions = materials.list_versions(controls.store.db, controls.ws.project_id, pid)
    events = programs.event_log(controls.store.db, controls.ws.project_id, pid)
    known = {}
    unavailable = []
    for event in events:
        if (
            not include_exports
            or event.type != ev.BundleExported.event_type
            or not event.session_id
        ):
            continue
        p = ev.BundleExported.model_validate(event.payload)
        members = (
            (set(p.run_ids), set(p.claim_ids))
            if p.run_ids is not None and p.claim_ids is not None
            else _legacy_members(controls, event.session_id, p)
        )
        if members is None:
            unavailable.append(p.bundle_id)
        else:
            known[p.bundle_id] = members
    paths = []
    for version in versions:
        _, upload = materials.find_upload(
            controls.store.db, controls.ws.project_id, version["file_id"]
        )
        paths.append({**version, "upload_path": upload.path})
    return trace(events, paths, known), versions, unavailable


def downstream(controls: ResearchControls, pid: str, vid: str) -> dict:
    graph, versions, unknown = _graph(controls, pid)
    if not any(row["id"] == vid for row in versions):
        raise LookupError("Material version not found in this program")
    result = graph.downstream(vid)
    members = frozenset(
        row["id"]
        for row in programs.show(controls.store.db, controls.ws.project_id, pid)["sessions"]
    )
    result["claims"] = program_views.render_claim_rows(controls, result["claims"], members)
    return {**result, "unverified_bundles": unknown}


def upstream(controls: ResearchControls, pid: str, cid: str) -> list[dict]:
    graph, versions, _ = _graph(controls, pid)
    if cid not in graph.rows["claims"]:
        raise LookupError("Claim not found in this program")
    return [row for row in versions if row["id"] in graph.origins["claims"][cid]]


def session_materials(controls: ResearchControls, sid: str, cid: str) -> list[dict]:
    history = controls.require_session(sid)
    if not any(e.type == "claim_extracted" and e.payload["claim_id"] == cid for e in history):
        raise LookupError("Claim not found in this session")
    program = programs.for_session(controls.store.db, controls.ws.project_id, sid)
    return upstream(controls, program, cid) if program else []


def review_state(
    controls: ResearchControls, pid: str
) -> tuple[dict[str, set[str]], dict[tuple[str, str], str]]:
    """Include earlier corrections discovered after a session joined the program."""
    changes = controls.store.read(
        None,
        types=(
            "material_superseded",
            "material_withdrawn",
            "claim_review_flagged",
            "claim_review_cleared",
        ),
    )
    invalid = {}
    recorded = {}
    cleared = set()
    for event in changes:
        p = event.payload
        if p.get("project_id") != controls.ws.project_id or p.get("program_id") != pid:
            continue
        if event.type == "material_superseded":
            invalid[p["prior_version_id"]] = event.id
        elif event.type == "material_withdrawn":
            invalid[p["version_id"]] = event.id
        else:
            key = (p["claim_id"], p["material_version_id"])
            if event.type == "claim_review_cleared":
                cleared.add(key)
                recorded.pop(key, None)
            else:
                cleared.discard(key)
                recorded[key] = event.id
    if invalid:
        graph, _, _ = _graph(controls, pid, include_exports=False)
        for claim, origins in graph.origins["claims"].items():
            for version in invalid.keys() & origins:
                key = (claim, version)
                if key not in cleared:
                    recorded.setdefault(key, invalid[version])
    outstanding: dict[str, set[str]] = {}
    for claim, version in recorded:
        outstanding.setdefault(claim, set()).add(version)
    return outstanding, recorded
