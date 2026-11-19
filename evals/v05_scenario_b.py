"""Run GSE96583 batch 2 in BioQuorum's real kernel and export a portable record."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from v02_demo import DemoClient
from v05_geo_analysis import MIN_GENES

from bioquorum.application import record, research, verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification
from bioquorum.core.hashing import sha256_file
from bioquorum.review.binding import find_bindings, render

GENE_FILES = (
    "GSE96583_RAW.tar",
    "GSE96583_batch2.genes.tsv.gz",
    "GSE96583_batch2.total.tsne.df.tsv.gz",
)
CLAIMS = (
    "Of {{cells_before}} submitted barcodes, {{cells_after}} author singlets met QC.",
    "QC required at least {{min_detected_genes}} detected genes and less than {{max_mt_percent}}% mitochondrial UMIs.",
    "Exploratory PCA and k-means used {{clustered_cells}} sampled cells and found {{clusters}} clusters.",
    "The author-labelled monocyte comparison used {{paired_donors}} paired donors with at least {{min_monocytes}} cells per condition.",
    "The paired analysis tested {{genes_tested}} genes. {{genes_at_fdr_05}} passed the declared BH FDR cutoff.",
)
QUERY = "Analyse GEO GSE96583 batch 2 with author singlet QC, exploratory clustering and donor-paired CD14+ monocyte differential expression."


async def run(root: Path, threshold: float, min_genes: int) -> None:
    if not 0 < threshold <= 100:
        raise ValueError("Provide a mitochondrial percentage greater than 0 and at most 100")
    if not 1 <= min_genes <= 35000:
        raise ValueError("Provide a valid minimum number of genes per barcode")
    input_paths = [f"data/{name}" for name in GENE_FILES]
    intake = "data/geo-inputs.json"
    intake_data = json.loads((root / intake).read_text())
    by_name = {r["filename"]: r for r in intake_data}
    for name, path in zip(GENE_FILES, input_paths, strict=True):
        if sha256_file(root / path) != by_name[name]["sha256"]:
            raise ValueError(f"An input differs from the checked GEO staging record: {name}")
    ws = workspace.load(root)
    if ws.classification != Classification.PUBLIC:
        raise ValueError("The GEO demonstration requires a public project")

    controls = ResearchControls(ws)
    sid = controls.new_session(
        f"GSE96583, mitochondrial cutoff {threshold:g}%, minimum {min_genes} genes"
    )
    relative = f"outputs/mt-{threshold:g}"
    if min_genes != MIN_GENES:
        relative += f"-genes-{min_genes}"
    code = Path(__file__).with_name("v05_geo_analysis.py").read_text()
    code += (
        "\nfrom IPython.display import SVG, display\n"
        f"run_geo({input_paths!r}, {threshold!r}, {min_genes!r}, {relative!r}, bq.value, lambda s: display(SVG(s)))\n"
    )
    client = DemoClient(
        controls.store,
        [
            ("read_file", {"path": intake}),
            (
                "python_execute",
                {"code": code, "input_files": [*input_paths, intake], "timeout_s": 220},
            ),
        ],
        "\n\n".join(CLAIMS),
        [{"text": line, "kind": "numeric"} for line in CLAIMS],
    )
    turn = await research.execute(
        ws,
        controls.store,
        sid,
        QUERY,
        client=client,
        permissions=lambda servers: workspace.default_permissions(ws, allow=[], servers=servers),
        max_steps=5,
    )
    evidence = research.answer_evidence(controls.store, sid, turn.answer_event_id)
    if not (bindings := find_bindings(turn.answer.text(), evidence)) or not all(
        b.ok for b in bindings
    ):
        errors = [
            p.error for e in controls.store.read(sid) if isinstance(p := e.parsed(), ev.RunFailed)
        ]
        raise RuntimeError(f"GEO workflow did not produce the named values. Run failures: {errors}")
    audit = await research.audit(
        ws, controls.store, sid, client, answer_event_id=turn.answer_event_id
    )
    events = [e.parsed() for e in controls.store.read(sid)]
    (start,) = [p for p in events if isinstance(p, ev.RunStarted)]
    (finish,) = [p for p in events if isinstance(p, ev.RunFinished)]
    drawer = record.binding(controls, sid, turn.answer_event_id, start.run_id, "value")
    assert drawer["code_hash"] == start.code_hash and len(finish.outputs) == 2
    registration = await verify.export_bundle(controls, sid)
    bundle = verify.bundle_file(controls, sid, registration["id"])

    output = root / relative
    for name, image in zip(("qc.svg", "clusters.svg"), finish.outputs, strict=True):
        (output / name).write_bytes(ws.cas().get(image))
    (output / "answer.md").write_text(render(turn.answer.text(), evidence), encoding="utf-8")
    summary = {
        "session_id": sid,
        "run_id": start.run_id,
        "isolation": start.isolation,
        "synthetic": start.synthetic,
        "input_hashes": dict(
            zip(start.input_paths, (ref.content_hash for ref in start.inputs), strict=True)
        ),
        "value_bindings": len(bindings),
        "values": {v.name: v.value for v in finish.values},
        "claims": [{"text": c.claim.text, "status": c.status.value} for c in audit.claims],
        "review_errors": audit.errors,
        "bundle": str(bundle.relative_to(root)),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--max-mt", type=float, default=10)
    parser.add_argument("--min-genes", type=int, default=MIN_GENES)
    args = parser.parse_args()
    asyncio.run(run(args.project.resolve(), args.max_mt, args.min_genes))


if __name__ == "__main__":
    main()
