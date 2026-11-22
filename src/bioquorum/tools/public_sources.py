"""Read-only public evidence sources, exposed through the audited tool registry."""

import re

import httpx2
from pydantic import BaseModel, Field

from bioquorum.connectors.pages import gwas_associations, trial_studies
from bioquorum.connectors.records import clean, read_record
from bioquorum.core.enums import Classification, PermissionClass
from bioquorum.core.hashing import canonical_json
from bioquorum.tools.registry import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
HPA = "https://www.proteinatlas.org/api/search_download.php"
FDA = "https://api.fda.gov/drug/label.json"
TARGET = "target-validation-public"
TRIALS = "https://clinicaltrials.gov/api/v2/studies"
GWAS = "https://www.ebi.ac.uk/gwas/rest/api/v2/associations"
BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pubmed.cgi/BioC_json"


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


def public_sources(http: httpx2.AsyncClient) -> ToolRegistry:
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
        fields = (
            "NCTId,BriefTitle,OverallStatus,Phase,InterventionName,Condition,"
            "PrimaryOutcomeMeasure,PrimaryOutcomeTimeFrame,LastUpdatePostDate,HasResults"
        )
        query = args.query.strip()
        params = {"pageSize": "5", "fields": fields}
        exact = re.fullmatch(r"NCT[0-9]{8}", query.upper())
        if exact:
            params.update(
                {"filter.ids": query.upper(), "fields": fields + ",OutcomeMeasuresModule"}
            )
        else:
            params["query.term"] = query
        data, source = await read_record(http, TRIALS, params)
        records = []
        for row in trial_studies(data):
            record = dict(row["protocolSection"])
            if "hasResults" in row:
                record["hasResults"] = row["hasResults"]
            if exact:
                outcomes = row.get("resultsSection", {}).get("outcomeMeasuresModule", {})
                primary = next(
                    (m for m in outcomes.get("outcomeMeasures", []) if m.get("type") == "PRIMARY"),
                    None,
                )
                if primary is not None:
                    record["firstReportedPrimaryOutcome"] = primary
            records.append(record)
        return _text(source, records)

    async def gwas(args: Variant, _ctx) -> ToolResult:
        data, source = await read_record(http, GWAS, {"rs_id": args.rsid, "size": "6"})
        rows = gwas_associations(data)
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
            "Search ClinicalTrials.gov for interventions, phases, primary endpoints and results availability. "
            "Look up an exact NCT ID to read the first posted primary outcome with arm-level data.",
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
            parallel_read=True,
        )
        for name, description, schema, handler in entries
    )
