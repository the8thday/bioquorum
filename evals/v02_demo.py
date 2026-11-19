"""Run the v0.2 PBMC QC and metformin literature demos without paid model access."""

import argparse
import asyncio
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from rich.console import Console

from bioquorum.agent import run_turn
from bioquorum.application import workspace
from bioquorum.cli.runner import answer_evidence, citation_context, print_answer, print_review
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification, ModelRole, PermissionClass
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, ToolCallPart
from bioquorum.core.status import VerificationStatus
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.review import review_answer
from bioquorum.review.binding import ground_claim
from bioquorum.storage.events import EventStore
from bioquorum.tools import default_registry, invoke

PBMC_URL = (
    "https://cf.10xgenomics.com/samples/cell-exp/1.1.0/pbmc3k/"
    "pbmc3k_filtered_gene_bc_matrices.tar.gz"
)
LITERATURE = {
    "11118008": (
        "Hepatic glucose production | Human before-after metabolic study | "
        "Metformin lowered glucose production via lower gluconeogenesis "
        '[@PMID:11118008 "through a reduction in gluconeogenesis"]'
    ),
    "21947382": (
        "Complex I and AMPK | Primary human hepatocytes and rodent experiments | "
        "Metformin activated hepatic AMPK in association with a lower energy state "
        '[@PMID:21947382 "a decrease in cellular energy status"]'
    ),
    "20577053": (
        "AMPK-independent effect | Mouse genetics and hepatocytes | "
        "Glucose lowering persisted in mice without liver AMPK "
        '[@DOI:10.1172/JCI40671 "the hypoglycemic effect of metformin was maintained"]'
    ),
    "30038219": (
        "Complex I-independent effect | In vivo rodent study | "
        "A redox-dependent pathway was observed without complex I inhibition "
        '[@PMID:30038219 "All of these effects occurred independently of complex I inhibition"]'
    ),
    "27216492": (
        "Gut hormones | Randomized delayed-release studies | "
        "Delayed-release treatment increased gut hormones "
        '[@PMID:27216492 "enhances fasting and postprandial GLP-1 and PYY"]'
    ),
}
PBMC_CLAIMS = (
    "The input matrix contains {{cells_before}} cells and {{genes_before}} genes.",
    (
        "The cell filters retained cells with {{min_genes}} to {{max_genes}} detected genes "
        "and under {{max_mt_percent}}% mitochondrial UMIs."
    ),
    "Genes were kept if observed in at least {{min_cells}} retained cells.",
    "The filtered matrix contains {{cells_after}} cells and {{genes_after}} genes.",
)
LIMITATION = (
    "These studies support multiple candidate mechanisms. Their relative contributions to "
    "glucose lowering in patients remain uncertain."
)
ROUTER = Router(
    ModelsConfig.from_dict(
        {
            "providers": {"ollama": {}},
            "roles": {"worker": "ollama:scripted-demo"},
        }
    )
)
SCRIPTED_USAGE = dict.fromkeys(
    ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"), 0
)


class DemoClient(ModelClient):
    """A fixed decision path to exercise real tools, evidence and review end to end."""

    def __init__(self, store, actions, answer, claims):
        super().__init__(ROUTER, store)
        self.actions = iter(actions)
        self.answer = answer
        self.claims = claims

    async def _call(self, route, messages, tools, on_delta=None):
        if route.role is ModelRole.REVIEWER:
            if "split a research assistant" in messages[0].text():
                reply = {"claims": self.claims}
            else:
                reply = {
                    "verdicts": [
                        {
                            "claim_index": i,
                            "supported": True,
                            "reason": "Predeclared demo reference",
                        }
                        for i, claim in enumerate(self.claims, 1)
                        if claim["kind"] == "citation"
                    ]
                }
            return Message.assistant(json.dumps(reply)), SCRIPTED_USAGE
        action = next(self.actions, None)
        if action is None:
            return Message.assistant(self.answer), SCRIPTED_USAGE
        name, args = action
        return Message.assistant("").model_copy(
            update={
                "parts": [ToolCallPart(call_id=new_id(), name=name, args=args)],
            }
        ), SCRIPTED_USAGE


async def fetch(registry, ctx, url: str, name: str) -> Path:
    result = await invoke(
        registry.get("download_file"), {"url": url, "filename": name, "max_mb": 12}, ctx
    )
    if result.is_error:
        raise RuntimeError(result.text())
    saved = result.text().splitlines()[0].removeprefix("saved: ")
    return ctx.workdir / saved


def split_abstracts(xml: Path, root: Path) -> list[tuple[str, dict]]:
    articles = {
        node.findtext(".//PMID"): node for node in ET.parse(xml).findall(".//PubmedArticle")
    }
    actions = []
    for pmid in LITERATURE:
        article = articles[pmid]
        title = article.find(".//ArticleTitle")
        if title is None:
            raise ValueError(f"PMID {pmid} was returned without a title")
        entries = article.findall(".//Abstract/AbstractText")
        if not entries:
            raise ValueError(f"PMID {pmid} was returned without an abstract")
        doi = article.findtext('.//ArticleId[@IdType="doi"]')
        parts = [f"PMID: {pmid}", f"DOI: {doi}" if doi else "", "".join(title.itertext())]
        parts.extend("".join(entry.itertext()) for entry in entries)
        path = root / f"pubmed-{pmid}.txt"
        path.write_text("\n".join(parts) + "\n", encoding="utf-8")
        actions.append(("read_file", {"path": path.name}))
    return actions


async def run(case: str, output: Path, source_file: Path | None = None) -> dict:
    ws = workspace.init_project(output, classification=Classification.PUBLIC)
    if case == "B" and ws.kernel_isolation().value != "bwrap":
        raise RuntimeError("The PBMC demo requires working Linux bubblewrap")
    registry = default_registry()
    store = EventStore(ws.database())
    sid = workspace.start_session(ws, store, f"v0.2 scenario {case}")
    ctx = workspace.tool_context(
        ws,
        store,
        sid,
        workspace.default_permissions(
            ws,
            allow=[PermissionClass.NETWORK],
            servers=[],
        ),
    )

    async def source(url: str, name: str) -> Path:
        if source_file is None:
            return await fetch(registry, ctx, url, name)
        local = ws.root / "data" / name
        local.parent.mkdir()
        shutil.copyfile(source_file, local)
        return local

    try:
        if case == "B":
            data = await source(PBMC_URL, "pbmc3k.tar.gz")
            relative = data.relative_to(ws.root).as_posix()
            code = Path(__file__).with_name("v02_pbmc3k.py").read_text(encoding="utf-8")
            code += f"\nfrom IPython.display import SVG, display\ndisplay(SVG(run_qc({relative!r}, bq.value)))"
            actions = [("python_execute", {"code": code, "input_files": [relative]})]
            claims = [{"text": line, "kind": "numeric"} for line in PBMC_CLAIMS]
            answer = "\n\n".join(PBMC_CLAIMS) + "\n\nThe run output includes the QC scatter plot."
            question = "Apply explicitly stated QC thresholds to the Cell Ranger PBMC dataset."
        else:
            ids = ",".join(LITERATURE)
            url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids}&retmode=xml"
            xml = await source(url, "metformin-pubmed.xml")
            actions = split_abstracts(xml, ws.root)
            claims = [
                {"text": row.rsplit(" | ", 1)[-1], "kind": "citation"}
                for row in LITERATURE.values()
            ] + [{"text": LIMITATION, "kind": "causal"}]
            answer = (
                "Mechanism | Study design | Finding\n--- | --- | ---\n"
                + "\n".join(LITERATURE.values())
                + "\n\n"
                + LIMITATION
            )
            question = "Build a cited table of proposed routes for metformin glucose lowering."
        client = DemoClient(store, actions, answer, claims)
        turn = await run_turn(question, client=client, registry=registry, ctx=ctx)
    finally:
        assert ctx.kernels is not None
        await ctx.kernels.shutdown_all()
    evidence = answer_evidence(store, sid, turn.answer_event_id)
    grounding = ground_claim(turn.answer.text(), evidence)
    if not grounding.bindings or grounding.state != "grounded":
        unresolved = [(b.raw, b.problem) for b in grounding.bindings if not b.ok]
        raise RuntimeError(f"Demo answer has unbound evidence: {grounding.unbound}, {unresolved}")
    result = await review_answer(
        store,
        client,
        session_id=sid,
        classification=ws.classification,
        answer_event_id=turn.answer_event_id,
        online_citations=citation_context(ws, store, sid),
    )
    console = Console(record=True, width=120)
    print_answer(console, turn.answer.text(), evidence)
    print_review(console, result)
    console.save_text(ws.root / "answer.txt")
    events = [e.parsed() for e in store.read(sid)]
    summary = {
        "mode": "scripted worker and review; real tools and inputs",
        "session_id": sid,
        "claims": [{"text": r.claim.text, "status": r.status.value} for r in result.claims],
        "errors": result.errors,
        "findings": [f.evidence for f in result.findings],
    }
    if case == "B":
        (started,) = [p for p in events if isinstance(p, ev.RunStarted)]
        (finished,) = [p for p in events if isinstance(p, ev.RunFinished)]
        (figure,) = finished.outputs
        assert started.inputs, "The calculation must link to its downloaded data"
        (ws.root / "pbmc3k-qc.svg").write_bytes(ws.cas().get(figure))
        summary.update(
            values={v.name: v.value for v in finished.values},
            isolation=started.isolation,
            run_id=started.run_id,
            run_key=finished.run_key,
            input_sha256=started.inputs[0].content_hash,
            figure_sha256=figure.content_hash,
        )
    (ws.root / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if any(
        r.status is not VerificationStatus.REVIEWED
        for r in result.claims
        if r.claim.text != LIMITATION
    ):
        raise RuntimeError("Some planned evidence checks did not complete")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("B", "C"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--input", type=Path, help="Use an already fetched official source file")
    args = parser.parse_args()
    asyncio.run(run(args.case, args.output, args.input))


if __name__ == "__main__":
    main()
