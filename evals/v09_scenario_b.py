"""Run and inspect the donor-paired GSE96583 Python-to-R scenario without model fees."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from v02_demo import DemoClient
from v05_scenario_b import GENE_FILES

from bioquorum.application import record, research, verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Classification
from bioquorum.core.hashing import sha256_file
from bioquorum.review.binding import find_bindings, render

HERE = Path(__file__).resolve().parent
HANDOFF = ["outputs/v09/paired-counts.csv", "outputs/v09/paired-samples.csv"]
R_OUTPUT = "outputs/v09/deseq2-results.csv"
PY_CLAIM = "Python selected {{paired_donors}} paired donors and {{genes_tested}} eligible genes."
R_CLAIM = (
    "The donor-adjusted DESeq2 stimulated-versus-control analysis included {{r_pairs}} donors "
    "and {{r_prefiltered}} prefiltered genes. Of {{r_adjusted}} genes with adjusted p-values, "
    "{{r_fdr05}} met the prespecified 5% BH FDR threshold."
)


async def run(root: Path, python_only: bool):
    intake = "data/geo-inputs.json"
    paths = ["data/" + name for name in GENE_FILES]
    hashes = {r["filename"]: r["sha256"] for r in json.loads((root / intake).read_text())}
    if any(sha256_file(root / path) != hashes[Path(path).name] for path in paths):
        raise ValueError("GEO source differs from the checked intake record")
    ws = workspace.load(root)
    if ws.classification != Classification.PUBLIC:
        raise ValueError("This scenario requires a public project")
    controls = ResearchControls(ws)
    sid = controls.new_session("GSE96583 paired monocyte counts")
    prep = (HERE / "v05_geo_analysis.py").read_text() + (
        f"\nrun_geo({paths!r}, 10, 200, 'outputs/v09', bq.value, lambda _: None, export_paired_counts=True)"
    )
    calls = [
        ("python_execute", {"code": prep, "input_files": [*paths, intake], "output_files": HANDOFF})
    ]
    claims = [PY_CLAIM]
    if not python_only:
        calls.append(
            (
                "r_execute",
                {
                    "code": (HERE.parent / "examples/v09/paired_deseq2.R").read_text(),
                    "input_files": HANDOFF,
                    "output_files": [R_OUTPUT],
                },
            )
        )
        claims.append(R_CLAIM)
    client = DemoClient(
        controls.store, calls, "\n\n".join(claims), [{"text": c, "kind": "numeric"} for c in claims]
    )
    turn = await research.execute(
        ws,
        controls.store,
        sid,
        "Analyse paired CD14+ monocytes from GSE96583.",
        client=client,
        permissions=lambda servers: workspace.default_permissions(ws, allow=[], servers=servers),
    )
    evidence = research.answer_evidence(controls.store, sid, turn.answer_event_id)
    bindings = find_bindings(turn.answer.text(), evidence)
    if not bindings or not all(b.ok for b in bindings):
        raise RuntimeError(f"Missing scenario bindings in session {sid}")
    for value in evidence.values.values():
        drawer = (
            record.binding(controls, sid, turn.answer_event_id, value.run_id, "value")
            if value.value.name in {"paired_donors", "r_pairs"}
            else None
        )
        if drawer is not None and not drawer["code"]:
            raise RuntimeError("Evidence drawer has no recorded code")
    if not python_only:
        for left, right in (("paired_donors", "r_pairs"), ("genes_tested", "r_prefiltered")):
            if evidence.values[left].value.value != evidence.values[right].value.value:
                raise ValueError("The two kernels disagree about the handoff dimensions")
    bundle = await verify.export_bundle(controls, sid)
    print(
        json.dumps(
            {
                "session": sid,
                "answer": render(turn.answer.text(), evidence),
                "bindings": len(bindings),
                "bundle": str(verify.bundle_file(controls, sid, bundle["id"]).relative_to(root)),
                "handoff_sha256": {name: sha256_file(root / name) for name in HANDOFF},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--python-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.project.resolve(), args.python_only))
