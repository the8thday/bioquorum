"""Run scenario A expansion with model-selected queries against public sources."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

import httpx2
import yaml
from pydantic import BaseModel, Field
from v05_scenario_a import EPMC, FDA, HPA, clean, read_record

from bioquorum.application import research, verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import (
    Actor,
    Classification,
    MessageRole,
    PermissionClass,
    PermissionScope,
)
from bioquorum.core.hashing import canonical_json
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, ToolCallPart
from bioquorum.models import ModelClient, ModelUnavailableError, Router
from bioquorum.review.binding import find_bindings, render
from bioquorum.tools import ToolRegistry
from bioquorum.tools.invoke import invoke_by_name
from bioquorum.tools.spec import ToolResult, ToolSpec

TARGET = "target-validation-public"
TRIALS = "https://clinicaltrials.gov/api/v2/studies"
GWAS = "https://www.ebi.ac.uk/gwas/rest/api/v2/associations"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pubmed.cgi/BioC_json"
CASES = Path(__file__).parent / "questions" / "scenario-a.yaml"

REFERENCE_ACTIONS = {
    "q-A-02": [("get_abstract", {"pmid": pmid}) for pmid in ("27807284", "30578352", "39167366")]
    + [("us_drug_label", {"name": "SOTYKTU"}), ("clinical_trials", {"query": "NCT04999839"})],
    "q-A-03": [
        ("get_abstract", {"pmid": pmid})
        for pmid in ("12730697", "16554528", "16909389", "28304224")
    ]
    + [("us_drug_label", {"name": name}) for name in ("REPATHA", "PRALUENT", "LEQVIO")],
    "q-A-04": [
        ("get_abstract", {"pmid": "29562163"}),
        ("baseline_expression", {"symbol": "HSD17B13"}),
        ("clinical_trials", {"query": "NCT05583344"}),
    ],
    "q-A-05": [("get_abstract", {"pmid": pmid}) for pmid in ("16804397", "14570728", "11385576")],
}


class Search(BaseModel):
    query: str = Field(min_length=2, max_length=220)


class Paper(BaseModel):
    pmid: str = Field(pattern=r"^[0-9]{5,9}$")


class Gene(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,24}$")


class Variant(BaseModel):
    rsid: str = Field(pattern=r"^rs[0-9]{1,12}$")


class Drug(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{1,35}$")


def _text(source: str, contents: object) -> ToolResult:
    return ToolResult.text(source + "\n" + canonical_json(contents).decode())


def tools(http: httpx2.AsyncClient) -> ToolRegistry:
    async def search(args: Search, _ctx) -> ToolResult:
        data, source = await read_record(
            http, EPMC, {"query": args.query, "pageSize": "5", "format": "json"}
        )
        rows = data["resultList"]["result"]
        return _text(
            source,
            [
                {"pmid": r.get("pmid"), "year": r.get("pubYear"), "title": r.get("title")}
                for r in rows
            ],
        )

    async def paper(args: Paper, _ctx) -> ToolResult:
        try:
            collections, source = await read_record(http, f"{BIOC}/{args.pmid}/unicode", {})
            doc = next(
                (
                    doc
                    for collection in collections
                    for doc in collection.get("documents", ())
                    if doc.get("id") == args.pmid
                ),
                None,
            )
            if doc is not None:
                passages = doc.get("passages", ())
                title = next(
                    (
                        part["text"]
                        for part in passages
                        if part.get("infons", {}).get("type") == "title"
                    ),
                    "",
                )
                abstract = " ".join(
                    part["text"]
                    for part in passages
                    if part.get("infons", {}).get("type") == "abstract"
                )
                if abstract:
                    return ToolResult.text(
                        f"{source}\nPMID: {args.pmid}\n{clean(title)}\n{clean(abstract)[:4200]}"
                    )
        except (httpx2.HTTPError, ValueError):
            pass
        data, source = await read_record(
            http, EPMC, {"query": "EXT_ID:" + args.pmid, "resultType": "core", "format": "json"}
        )
        row = next((r for r in data["resultList"]["result"] if r.get("pmid") == args.pmid), None)
        if row is None or not row.get("abstractText"):
            return ToolResult.text(
                "No Europe PMC abstract found for the supplied PMID", is_error=True
            )
        return ToolResult.text(
            f"{source}\nPMID: {args.pmid}\n{row.get('title', '')}\n{clean(row['abstractText'])[:4200]}"
        )

    async def label(args: Drug, _ctx) -> ToolResult:
        query = f'openfda.brand_name:"{args.name}" OR openfda.generic_name:"{args.name}"'
        data, source = await read_record(
            http, FDA, {"search": query, "sort": "effective_time:desc", "limit": "1"}
        )
        row = data["results"][0]
        return _text(
            source,
            {
                field: " ".join(row.get(field, []))[:1500]
                for field in ("mechanism_of_action", "indications_and_usage")
            },
        )

    async def trials(args: Search, _ctx) -> ToolResult:
        data, source = await read_record(
            http,
            TRIALS,
            {
                "query.term": args.query,
                "pageSize": "5",
                "fields": "NCTId,BriefTitle,OverallStatus,Phase,InterventionName",
            },
        )
        return _text(source, [row["protocolSection"] for row in data.get("studies", [])])

    async def gwas(args: Variant, _ctx) -> ToolResult:
        data, source = await read_record(http, GWAS, {"rs_id": args.rsid, "size": "6"})
        rows = data.get("_embedded", {}).get("associations", [])
        fields = (
            "accession_id",
            "mapped_genes",
            "reported_trait",
            "snp_effect_allele",
            "pubmed_id",
        )
        return _text(source, [{key: row.get(key) for key in fields} for row in rows])

    async def expression(args: Gene, _ctx) -> ToolResult:
        data, source = await read_record(
            http,
            HPA,
            {
                "search": args.symbol,
                "format": "json",
                "columns": "g,eg,rnats,rnascs",
                "compress": "no",
            },
        )
        return _text(source, [row for row in data if row.get("Gene") == args.symbol])

    entries = (
        (
            "search_papers",
            "Search Europe PMC for primary papers. Use short gene and topic terms. Returns PMIDs and titles.",
            Search,
            search,
        ),
        ("get_abstract", "Read the original abstract for a known or retrieved PMID.", Paper, paper),
        (
            "us_drug_label",
            "Get the current US label for one known brand or generic name.",
            Drug,
            label,
        ),
        (
            "clinical_trials",
            "Search ClinicalTrials.gov for study IDs, interventions, statuses and phases.",
            Search,
            trials,
        ),
        ("gwas_variant", "Look up associations for an exact rsID in GWAS Catalog.", Variant, gwas),
        (
            "baseline_expression",
            "Get the Human Protein Atlas RNA specificity for one gene symbol.",
            Gene,
            expression,
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
            target=lambda *_: TARGET,
        )
        for name, description, schema, handler in entries
    )


async def run_case(row: dict, root: Path, model_url: str, model: str, reference: bool) -> dict:
    case_id = row["id"]
    output = root / case_id
    output.mkdir(parents=True, exist_ok=True)
    ws = (
        workspace.load(output)
        if (output / ".bioquorum").exists()
        else workspace.init_project(output, classification=Classification.PUBLIC)
    )
    if ws.classification is not Classification.PUBLIC:
        raise ValueError("Public evaluation requires a public project")
    ws.config = {
        "providers": {"local": {"kind": "ollama", "base_url": model_url}},
        "roles": {"worker": f"local:{model}", "reviewer": f"local:{model}"},
        "agent": {"binding_retries": 1},
    }
    controls = ResearchControls(ws)
    sid = controls.new_session(case_id)
    client = ModelClient(Router(ws.models_config()), controls.store, max_output_tokens=900)

    def permissions(servers):
        policy = workspace.default_permissions(ws, allow=[], servers=servers)
        policy.grant(PermissionClass.CONNECTOR, TARGET, PermissionScope.SESSION, session_id=sid)
        return policy

    prompt = row["question"].strip() + (
        " Retrieve and read original evidence before answering. Search for a paper, read its abstract, "
        "and check relevant labels or trial records. Link each factual statement to a short verbatim "
        'passage with [@s1 "exact words"]. Clearly label evidence gaps and inferences.'
    )
    async with httpx2.AsyncClient(timeout=8, headers={"accept": "application/json"}) as http:
        registry = tools(http)
        if reference:
            calls = [
                ToolCallPart(call_id=new_id(), name=name, args=args)
                for name, args in REFERENCE_ACTIONS[case_id]
            ]
            ctx = workspace.tool_context(ws, controls.store, sid, permissions([]))
            asked = controls.store.append(
                ev.UserMessage(
                    message=Message.user(
                        "Collect the preselected evidence records for this evaluation."
                    )
                ),
                session_id=sid,
                actor=Actor.USER,
            )
            selected = controls.store.append(
                ev.AssistantMessage(message=Message(role=MessageRole.ASSISTANT, parts=calls)),
                session_id=sid,
                actor=Actor.SYSTEM,
                causation_id=asked.id,
            )
            capacity = asyncio.Semaphore(3)

            async def collect(call: ToolCallPart) -> None:
                async with capacity:
                    await invoke_by_name(
                        registry,
                        call.name,
                        call.args,
                        ctx,
                        call_id=call.call_id,
                        causation_id=selected.id,
                    )

            try:
                await asyncio.gather(*(collect(call) for call in calls))
            finally:
                assert ctx.kernels is not None
                await ctx.kernels.shutdown_all()
            prompt = row["question"].strip() + (
                " Use the supplied original evidence. Attach an exact short verbatim passage to each "
                'factual statement with [@s1 "exact words"]. Distinguish missing data from evidence of absence.'
            )
        turn = await research.execute(
            ws,
            controls.store,
            sid,
            prompt,
            client=client,
            permissions=permissions,
            registry_factory=ToolRegistry if reference else lambda: registry,
            max_steps=4 if reference else 9,
        )
    evidence = research.answer_evidence(controls.store, sid, turn.answer_event_id)
    bindings = find_bindings(turn.answer.text(), evidence)
    review = None
    audit_error = None
    try:
        async with asyncio.timeout(40):
            review = await research.audit(
                ws, controls.store, sid, client, answer_event_id=turn.answer_event_id
            )
    except (TimeoutError, ModelUnavailableError) as exc:
        audit_error = f"review_incomplete: {type(exc).__name__}"
    exports = await verify.export_bundle(controls, sid) if evidence.sources else None
    events = [event.parsed() for event in controls.store.read(sid)]
    report = {
        "case": case_id,
        "session": sid,
        "model": model,
        "mode": "preselected reference retrieval" if reference else "model-selected API calls",
        "rubric": row["rubric"],
        "retrieval": [
            {"tool": p.tool, "ok": not p.result.is_error}
            for p in events
            if isinstance(p, ev.ToolCallFinished)
        ],
        "tool_errors": [p.error for p in events if isinstance(p, ev.ToolCallFailed)],
        "sources": [{"alias": s.alias, "tool": s.tool, "text": s.text} for s in evidence.sources],
        "bindings": [{"text": b.raw, "ok": b.ok, "problem": b.problem} for b in bindings],
        "claims": [{"text": p.claim.text, "status": p.status.value} for p in review.claims]
        if review
        else [],
        "review_errors": review.errors if review else [audit_error],
        "findings": [f.evidence for f in review.findings] if review else [],
        "bundle": exports
        and str(verify.bundle_file(controls, sid, exports["id"]).relative_to(output)),
    }
    (output / "answer.md").write_text(turn.answer.text() + "\n", encoding="utf-8")
    (output / "rendered.md").write_text(
        render(turn.answer.text(), evidence) + "\n", encoding="utf-8"
    )
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    counts = Counter(p["status"] for p in report["claims"])
    print(
        case_id,
        "sources",
        len(evidence.sources),
        "bindings",
        sum(b.ok for b in bindings),
        dict(counts),
        flush=True,
    )
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Existing local Ollama /v1 endpoint")
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--reference", action="store_true", help="Retrieve fixed source IDs before model synthesis"
    )
    args = parser.parse_args()
    selected = set(args.case or ("q-A-02", "q-A-03", "q-A-04", "q-A-05"))
    rows = [case for case in yaml.safe_load(CASES.read_text()) if case["id"] in selected]
    if {row["id"] for row in rows} != selected:
        parser.error("Unknown scenario A case")
    if args.reference and not selected <= REFERENCE_ACTIONS.keys():
        parser.error("Reference records are defined for cases 02-05")
    errors = {}
    for row in rows:
        try:
            async with asyncio.timeout(210):
                await run_case(row, args.output.resolve(), args.url, args.model, args.reference)
        except Exception as exc:
            errors[row["id"]] = f"{type(exc).__name__}: {exc}"
            print(row["id"], errors[row["id"]], flush=True)
    if errors:
        (args.output / "errors.json").write_text(json.dumps(errors, indent=2) + "\n")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
