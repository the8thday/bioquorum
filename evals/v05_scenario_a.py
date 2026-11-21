"""Exercise the IL23R dossier with live public sources and a local Ollama model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

import httpx2
from pydantic import BaseModel
from v02_demo import DemoClient

from bioquorum.application import record, research, verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.connectors.records import clean, read_record
from bioquorum.core import events as ev
from bioquorum.core.enums import ClaimKind, Classification, PermissionClass, PermissionScope
from bioquorum.core.hashing import canonical_json
from bioquorum.core.messages import Message
from bioquorum.core.refs import QuoteRef
from bioquorum.review import CitationSupports, ClaimInput, deterministic_checks
from bioquorum.review import review as run_review
from bioquorum.review.binding import find_bindings, render
from bioquorum.review.evidence import Evidence
from bioquorum.tools import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
GWAS = "https://www.ebi.ac.uk/gwas/rest/api/v2/associations/216760354"
HPA = "https://www.proteinatlas.org/api/search_download.php"
FDA = "https://api.fda.gov/drug/label.json"
ARTICLES = {"genetics": "17068223", "functional": "22022372", "trial": "35644154"}
LITERATURE = "europe_pmc_article"


class Article(BaseModel):
    topic: Literal["genetics", "functional", "trial"]


class NoArguments(BaseModel):
    pass


def source_tools(client: httpx2.AsyncClient) -> ToolRegistry:
    async def article(args: Article, _ctx) -> ToolResult:
        pmid = ARTICLES[args.topic]
        data, source = await read_record(
            client, EPMC, {"query": f"EXT_ID:{pmid}", "resultType": "core", "format": "json"}
        )
        pub = next(row for row in data["resultList"]["result"] if row.get("pmid") == pmid)
        return ToolResult.text(
            "\n".join(
                (
                    source,
                    f"PMID: {pmid}",
                    f"DOI: {pub['doi']}",
                    pub["title"],
                    clean(pub["abstractText"]),
                )
            )
        )

    async def genetics(_args, _ctx) -> ToolResult:
        data, source = await read_record(client, GWAS, {})
        fields = (
            "association_id",
            "accession_id",
            "reported_trait",
            "mapped_genes",
            "snp_effect_allele",
            "or_per_copy_num",
            "ci_lower",
            "ci_upper",
            "pubmed_id",
        )
        return ToolResult.text(
            source + "\n" + canonical_json({k: data[k] for k in fields}).decode()
        )

    async def expression(_args, _ctx) -> ToolResult:
        data, source = await read_record(
            client,
            HPA,
            {
                "search": "IL23R",
                "format": "json",
                "columns": "g,eg,rnascs,rnats,sc_RNA_MAIT_T-cells",
                "compress": "no",
            },
        )
        gene = next(row for row in data if row["Ensembl"] == "ENSG00000162594")
        return ToolResult.text(source + "\n" + canonical_json(gene).decode())

    async def label(_args, _ctx) -> ToolResult:
        data, source = await read_record(
            client,
            FDA,
            {"search": "openfda.brand_name:SKYRIZI", "sort": "effective_time:desc", "limit": "1"},
        )
        document = data["results"][0]
        fields = ("effective_time", "indications_and_usage", "mechanism_of_action")
        return ToolResult.text(
            source + "\n" + canonical_json({k: document[k] for k in fields}).decode()
        )

    definitions = (
        (
            LITERATURE,
            "Retrieve a Europe PMC primary IL23R genetics, functional study or Crohn trial abstract",
            article,
            Article,
        ),
        (
            "gwas_record",
            "Get the GWAS Catalog rs11209026 Crohn association record",
            genetics,
            NoArguments,
        ),
        (
            "hpa_expression",
            "Get Human Protein Atlas IL23R baseline tissue and single-cell RNA specificity",
            expression,
            NoArguments,
        ),
        (
            "fda_label",
            "Get the U.S. FDA risankizumab drug label mechanism and indications",
            label,
            NoArguments,
        ),
    )
    return ToolRegistry(
        ToolSpec(
            name,
            description,
            handler,
            PermissionClass.CONNECTOR,
            args_model=schema,
            classification_floor=Classification.PUBLIC,
            target=lambda *_: "scenario-a-public-sources",
        )
        for name, description, handler, schema in definitions
    )


LOOKUP = (
    "Use europe_pmc_article with topic genetics to retrieve the original IL23R association "
    "study. Find a verbatim passage for the direction of the R381Q association."
)
QUESTION = """Evaluate IL23R as a therapeutic target for inflammatory bowel disease. Read gwas_record, hpa_expression and fda_label. Provide separate evidence rows for the genetic direction, baseline RNA expression and the approved pathway drug. Use the primary genetics abstract from the previous turn or retrieve it now. Distinguish ligand blockade from receptor targeting. Summarise direction qualitatively. Use one short verbatim BioQuorum excerpt for each evidence row using the form [@s1 "verbatim passage"]. State gaps. Do not claim any clinical evidence for direct receptor inhibition without a source."""


class GuidedDossier(DemoClient):
    """Predeclare source collection and wording to test the record path reliably."""

    def __init__(self, controls: ResearchControls, sid: str):
        self.sid = sid
        self.review_claims: list[ClaimInput] = []
        evidence = Evidence.from_events(controls.store.history(sid))
        present = {source.tool for source in evidence.sources}
        actions = [
            (tool, {})
            for tool in ("gwas_record", "hpa_expression", "fda_label")
            if tool not in present
        ]
        if not any("confers strong protection" in s.text for s in evidence.sources):
            actions.append((LITERATURE, {"topic": "genetics"}))
        if not any(
            "phase 3 ADVANCE and MOTIVATE induction trials" in s.text for s in evidence.sources
        ):
            actions.append((LITERATURE, {"topic": "trial"}))
        super().__init__(controls.store, actions, "", [])

    async def _call(self, route, messages, tools, on_delta=None):
        message, usage = await super()._call(route, messages, tools, on_delta=on_delta)
        if message.tool_calls():
            return message, usage
        evidence = Evidence.from_events(self.store.history(self.sid))

        def quote(tool: str, passage: str) -> str:
            source = next(
                (s for s in evidence.sources if s.tool == tool and passage in s.text), None
            )
            if source is None:
                raise ValueError(f"The live {tool} record omitted the predeclared passage")
            return f"[@{source.alias} {json.dumps(passage)}]"

        variant = quote(LITERATURE, "confers strong protection against Crohn's disease")
        catalog = quote("gwas_record", "rs11209026-A")
        tissue = quote("hpa_expression", "Tissue enhanced")
        single = quote("hpa_expression", "Cell type enriched")
        mechanism = quote(
            "fda_label", "selectively binds to the p19 subunit of human IL-23 cytokine"
        )
        crohn = quote("fda_label", "moderately to severely active Crohn's disease in adults")
        uc = quote("fda_label", "moderately to severely active ulcerative colitis in adults")
        trial = quote(LITERATURE, "phase 3 ADVANCE and MOTIVATE induction trials")
        claims = [
            f"The original IL23R R381Q (rs11209026) association supports a protective allele for Crohn's disease {variant}.",
            f"The GWAS Catalog record identifies the A effect allele {catalog}.",
            f"Human Protein Atlas classifies IL23R RNA as tissue-enhanced {tissue} and cell-type enriched in its single-cell summary {single}.",
            f"Risankizumab binds the p19 subunit of the IL-23 ligand {mechanism}.",
            f"The US label includes adult Crohn's disease {crohn} and ulcerative colitis {uc}.",
            f"The retrieved induction trial publication covers ADVANCE and MOTIVATE {trial}.",
        ]
        self.review_claims = [
            ClaimInput(i, claim, ClaimKind.CITATION) for i, claim in enumerate(claims, 1)
        ]
        answer = (
            "# IL23R in inflammatory bowel disease\n\n"
            f"**Human genetics.** {' '.join(claims[:2])}\n\n"
            f"**Baseline expression.** {claims[2]} These are baseline RNA categories.\n\n"
            f"**Clinical pathway evidence.** {' '.join(claims[3:])}\n\n"
            "**Dossier interpretation.** Human genetics and ligand blockade support the pathway. "
            "Direct receptor inhibition would require its own pharmacology and clinical evidence."
        )
        return Message.assistant(answer), usage


async def run(
    output: Path, model_url: str, model: str, delegated: bool, guided: bool, resume: str | None
) -> None:
    ws = (
        workspace.load(output)
        if resume
        else workspace.init_project(output, classification=Classification.PUBLIC)
    )
    ws.config = {
        "providers": {"local": {"kind": "ollama", "base_url": model_url}},
        "roles": {"worker": f"local:{model}", "reviewer": f"local:{model}"},
        "agent": {
            "literature_subtasks": delegated,
            "literature_tools": [LITERATURE],
            "binding_retries": 1,
        },
    }
    controls = ResearchControls(ws)
    sid = resume or controls.new_session("IL23R and IBD target dossier")
    controls.require_session(sid)
    client = research.model_client(ws, controls.store)

    def permissions(servers):
        policy = workspace.default_permissions(ws, allow=[], servers=servers)
        policy.grant(
            PermissionClass.CONNECTOR,
            "scenario-a-public-sources",
            PermissionScope.SESSION,
            session_id=sid,
        )
        return policy

    async with httpx2.AsyncClient(timeout=12.0, headers={"accept": "application/json"}) as http:
        if resume is None:
            await research.execute(
                ws,
                controls.store,
                sid,
                ("/literature " if delegated else "") + LOOKUP,
                client=client,
                permissions=permissions,
                registry_factory=lambda: source_tools(http),
                max_steps=4,
            )
        ws.config["agent"]["literature_subtasks"] = False
        dossier_client = GuidedDossier(controls, sid) if guided else client
        result = await research.execute(
            ws,
            controls.store,
            sid,
            QUESTION,
            client=dossier_client,
            permissions=permissions,
            registry_factory=lambda: source_tools(http),
            max_steps=9,
        )
    evidence = research.answer_evidence(controls.store, sid, result.answer_event_id)
    bindings = find_bindings(result.answer.text(), evidence)
    if isinstance(dossier_client, GuidedDossier):
        support = CitationSupports(client, session_id=sid, classification=ws.classification)
        review = await run_review(
            controls.store,
            session_id=sid,
            target_event_id=result.answer_event_id,
            claims=dossier_client.review_claims,
            evidence=evidence,
            checks=[*deterministic_checks(), support],
            classification=ws.classification,
        )
    else:
        review = await research.audit(
            ws, controls.store, sid, client, answer_event_id=result.answer_event_id
        )
    events = [event.parsed() for event in controls.store.read(sid)]
    export = await verify.export_bundle(controls, sid)
    bundle = verify.bundle_file(controls, sid, export["id"])
    drawers = [
        record.binding(controls, sid, result.answer_event_id, b.ref.call_id, "quote")
        for b in bindings
        if b.ok and isinstance(b.ref, QuoteRef)
    ]
    report = {
        "session_id": sid,
        "model": model,
        "delegated": delegated,
        "guided_dossier": guided,
        "literature": [p.outcome for p in events if isinstance(p, ev.LiteratureFinished)],
        "tools": [
            {"tool": p.tool, "ok": not p.result.is_error}
            for p in events
            if isinstance(p, ev.ToolCallFinished)
        ],
        "bindings": [{"text": b.raw, "ok": b.ok, "problem": b.problem} for b in bindings],
        "claims": [
            {"text": item.claim.text, "status": item.status.value} for item in review.claims
        ],
        "review_errors": review.errors,
        "findings": [finding.evidence for finding in review.findings],
        "drawers": [{"tool": d["tool"], "child": bool(d.get("task_id"))} for d in drawers],
        "bundle": str(bundle.relative_to(output)),
    }
    (output / "answer.md").write_text(render(result.answer.text(), evidence), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("session_id", "literature", "tools", "bundle")}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", required=True, help="Existing, local Ollama /v1 endpoint")
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--direct", action="store_true")
    parser.add_argument(
        "--guided", action="store_true", help="Use predeclared dossier steps and statements"
    )
    parser.add_argument("--resume", help="Existing case session in the output project")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        run(args.output.resolve(), args.url, args.model, not args.direct, args.guided, args.resume)
    )


if __name__ == "__main__":
    main()
