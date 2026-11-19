"""Resolve a public PMID or DOI through its authoritative registry."""

import asyncio
import json
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, Field

from bioquorum.core.enums import Classification, PermissionClass
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

PUBMED_HOST = "eutils.ncbi.nlm.nih.gov"
DOI_HOST = "doi.org"
_KEY_PATTERN = r"^(?:PMID:[1-9][0-9]{0,8}|DOI:10\.[0-9]{4,9}/[^\s]{1,220})$"


class CitationArgs(BaseModel):
    key: str = Field(pattern=_KEY_PATTERN, max_length=240)

    @property
    def host(self) -> str:
        return PUBMED_HOST if self.key.startswith("PMID:") else DOI_HOST


async def _pubmed(client: httpx2.AsyncClient, pmid: str) -> bool | None:
    response = await client.get(
        f"https://{PUBMED_HOST}/entrez/eutils/esummary.fcgi",
        params={"db": "pubmed", "id": pmid, "retmode": "json", "tool": "bioquorum"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(result := data.get("result"), dict):
        return None
    entry = result.get(pmid)
    if not isinstance(entry, dict) or pmid not in result.get("uids", []):
        return None
    if entry.get("uid") == pmid and isinstance(entry.get("title"), str) and entry["title"].strip():
        return True
    if str(entry.get("error", "")).lower().startswith("cannot get document summary"):
        return False
    return None


async def _doi(client: httpx2.AsyncClient, doi: str) -> bool | None:
    response = await client.get(f"https://{DOI_HOST}/api/handles/{quote(doi, safe='')}")
    if response.status_code not in (200, 404):
        response.raise_for_status()
        return None
    data = response.json()
    if not isinstance(data, dict) or str(data.get("handle", "")).casefold() != doi.casefold():
        return None
    if response.status_code == 200 and data.get("responseCode") == 1:
        return True
    if data.get("responseCode") == 100:
        return False
    return None


async def _lookup(args: CitationArgs, ctx: ToolContext) -> ToolResult:
    kind, value = args.key.split(":", maxsplit=1)
    resolve = _pubmed if kind == "PMID" else _doi
    exists: bool | None = None
    async with httpx2.AsyncClient(
        timeout=10, follow_redirects=False, trust_env=False, proxy=ctx.outbound_proxy
    ) as client:
        for attempt in range(3):
            try:
                exists = await resolve(client, value)
                break
            except httpx2.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.4 * (attempt + 1))
    if exists is None:
        return ToolResult.text("Citation registry returned no definitive answer.", is_error=True)
    return ToolResult.text(json.dumps({"key": args.key, "exists": exists, "registry": args.host}))


CITATION_LOOKUP = ToolSpec(
    name="citation_lookup",
    description="Check whether a PMID or DOI exists in its public registry.",
    handler=_lookup,
    args_model=CitationArgs,
    permission=PermissionClass.NETWORK,
    classification_floor=Classification.PUBLIC,
    target=lambda args, _ctx: args.host,
)
