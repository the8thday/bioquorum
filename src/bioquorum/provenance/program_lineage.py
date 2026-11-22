"""Trace declared input snapshots and successful outputs through recorded computations."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from bioquorum.core import events as ev
from bioquorum.core.refs import ArtifactRef, RunRef, ValueRef


@dataclass
class Lineage:
    rows: dict[str, dict[str, dict]] = field(
        default_factory=lambda: {
            k: {} for k in ("runs", "values", "figures", "claims", "decisions", "bundles")
        }
    )
    origins: dict[str, dict[str, set[str]]] = field(default_factory=lambda: defaultdict(dict))

    ambiguous_inputs: list[dict] = field(default_factory=list)

    def add(self, kind: str, row: dict, materials: set[str]) -> None:
        self.rows[kind][row["id"]] = row
        self.origins[kind][row["id"]] = set(materials)

    def downstream(self, version: str) -> dict:
        rows = {
            kind: [
                row for key, row in entries.items() if version in self.origins[kind].get(key, set())
            ]
            for kind, entries in self.rows.items()
        }
        rows["ambiguous_inputs"] = [
            item for item in self.ambiguous_inputs if version in item["candidate_versions"]
        ]
        return rows


def trace(
    events: Iterable[ev.Event],
    materials: list[dict],
    bundle_members: dict[str, tuple[set[str], set[str]]],
) -> Lineage:
    graph = Lineage()
    material_bytes: dict[str, set[str]] = defaultdict(set)
    for material in materials:
        material_bytes[material["hash"]].add(material["id"])
    by_id = {m["id"]: m for m in materials}
    overrides: dict[str, tuple[str, str]] = {}
    produced: dict[tuple[str | None, str], tuple[str, set[str]]] = {}
    state: dict[tuple[str | None, str, bool], set[str]] = defaultdict(set)
    starts: dict[str, ev.RunStarted] = {}
    writes: dict[str, dict[str, str]] = defaultdict(dict)
    decision_heads: dict[str, str] = {}
    for event in events:
        p = event.parsed()
        sid = event.session_id
        if isinstance(p, ev.RecomputeStarted):
            overrides[p.session_id] = (p.material_version_id, p.replacement_version_id)
        elif isinstance(p, ev.RunStarted):
            starts[p.run_id] = p
            refs = set(state[(sid, p.kernel_id, p.verification_run)])
            reasons = ["prior_kernel_state"] if refs else []
            scope = sid if p.verification_run or sid in overrides else None
            for i, ref in enumerate(p.inputs):
                if ref.weak_hash:
                    continue
                candidates = material_bytes.get(ref.content_hash, set())
                name = p.input_paths[i] if i < len(p.input_paths) else None
                exact = {v for v in candidates if name and by_id[v].get("upload_path") == name}
                if sid in overrides:
                    old, new = overrides[sid]
                    if new in candidates and by_id.get(old, {}).get("upload_path") == name:
                        exact = {new}
                if len(exact) == 1:
                    refs.update(exact)
                    reasons.append("registered_upload_path_and_hash")
                elif len(candidates) == 1:
                    refs.update(candidates)
                    reasons.append("matching_content_hash")
                elif candidates:
                    graph.ambiguous_inputs.append(
                        {
                            "run_id": p.run_id,
                            "session_id": sid,
                            "path": name,
                            "candidate_versions": sorted(candidates),
                        }
                    )
                upstream = produced.get((scope, name)) if name else None
                if upstream and upstream[0] == ref.content_hash:
                    refs.update(upstream[1])
                    reasons.append("prior_output_path_and_hash")
            graph.add(
                "runs",
                {
                    "id": p.run_id,
                    "language": p.language,
                    "session_id": sid,
                    "event_id": event.id,
                    "basis": list(dict.fromkeys(reasons)),
                },
                refs,
            )
        elif (
            isinstance(p, ev.LineageObserved)
            and p.direction == "output"
            and p.function == "declared_write"
            and p.post_run_hash
        ):
            writes[p.run_id][p.path] = p.post_run_hash
        elif isinstance(p, ev.RunFinished) and p.run_id in starts:
            start = starts[p.run_id]
            refs = graph.origins["runs"][p.run_id]
            state[(sid, start.kernel_id, start.verification_run)].update(refs)
            for path, digest in writes[p.run_id].items():
                scope = sid if start.verification_run or sid in overrides else None
                produced[(scope, path)] = (digest, refs)
            for value in p.values:
                graph.add(
                    "values",
                    {
                        "id": f"{p.run_id}/{value.name}",
                        "run_id": p.run_id,
                        "session_id": sid,
                        "event_id": event.id,
                        **value.model_dump(mode="json"),
                    },
                    refs,
                )
            for ref in p.outputs:
                if ref.kind.value == "figure":
                    graph.add(
                        "figures",
                        {
                            "id": f"{p.run_id}/{ref.content_hash}",
                            "run_id": p.run_id,
                            "session_id": sid,
                            "hash": ref.content_hash,
                            "event_id": event.id,
                        },
                        refs,
                    )
        elif isinstance(p, ev.ArtifactVersionCreated):
            refs = graph.origins["runs"].get(p.run_id or "", set())
            graph.origins["artifacts"][p.version_id] = refs
            if p.kind.value == "figure":
                graph.add(
                    "figures",
                    {
                        "id": p.version_id,
                        "run_id": p.run_id,
                        "session_id": sid,
                        "hash": p.content_hash,
                        "event_id": event.id,
                    },
                    refs,
                )
        elif isinstance(p, ev.ClaimExtracted):
            refs = set()
            for ref in p.refs:
                if isinstance(ref, ValueRef):
                    refs.update(graph.origins["values"].get(f"{ref.run_id}/{ref.name}", ()))
                elif isinstance(ref, RunRef):
                    refs.update(graph.origins["runs"].get(ref.run_id, ()))
                elif isinstance(ref, ArtifactRef):
                    refs.update(graph.origins["artifacts"].get(ref.artifact_version_id, ()))
            graph.add(
                "claims",
                {
                    "id": p.claim_id,
                    "session_id": sid,
                    "source_event_id": p.source_event_id,
                    "event_id": event.id,
                    "text": p.text,
                },
                refs,
            )
        elif isinstance(p, ev.DecisionRecorded):
            refs = set().union(*(graph.origins["claims"].get(c.id, set()) for c in p.claims))
            previous = decision_heads.get(p.decision_id)
            if previous is not None:
                graph.rows["decisions"][previous]["current"] = False
            decision_heads[p.decision_id] = event.id
            graph.add(
                "decisions",
                {
                    "id": event.id,
                    "decision_id": p.decision_id,
                    "text": p.text,
                    "event_id": event.id,
                    "current": True,
                },
                refs,
            )

        elif isinstance(p, ev.BundleExported):
            runs, claims = bundle_members.get(p.bundle_id, (set(), set()))
            refs = set().union(
                *(graph.origins["runs"].get(r, set()) for r in runs),
                *(graph.origins["claims"].get(c, set()) for c in claims),
            )
            graph.add("bundles", {"id": p.bundle_id, "session_id": sid, "event_id": event.id}, refs)
        elif isinstance(p, ev.ProgramBundleExported):
            refs = set(p.material_version_ids)
            for child in p.session_bundle_ids:
                refs.update(graph.origins["bundles"].get(child, ()))
            graph.add(
                "bundles", {"id": p.bundle_id, "session_id": None, "event_id": event.id}, refs
            )
    return graph
