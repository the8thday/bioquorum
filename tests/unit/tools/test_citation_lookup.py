import json

import httpx2
import pytest

from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.tools import invoke

pytestmark = pytest.mark.anyio


async def test_citation_lookup_uses_policy_and_checks_exact_registry_record(ctx, monkeypatch):
    from bioquorum.tools.builtin import citation

    received = []

    def handle(request):
        received.append(request)
        if request.url.host == "doi.org":
            return httpx2.Response(404, json={"responseCode": 100, "handle": "10.1000/missing"})
        assert request.url.params["id"] == "21475136"
        return httpx2.Response(
            200,
            json={
                "result": {"uids": ["21475136"], "21475136": {"uid": "21475136", "title": "Study"}}
            },
        )

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        citation.httpx2,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx2.MockTransport(handle),
            **kw,
        ),
    )
    ctx.permissions.grant(PermissionClass.NETWORK, "*", PermissionScope.GLOBAL)
    ctx.classification = Classification.CONFIDENTIAL
    assert (await invoke(citation.CITATION_LOOKUP, {"key": "PMID:21475136"}, ctx)).is_error
    assert received == []
    ctx.classification = Classification.PUBLIC
    found = await invoke(citation.CITATION_LOOKUP, {"key": "PMID:21475136"}, ctx)
    missing = await invoke(citation.CITATION_LOOKUP, {"key": "DOI:10.1000/missing"}, ctx)
    assert json.loads(found.text())["exists"] is True
    assert json.loads(missing.text())["exists"] is False
    assert len(received) == 2
