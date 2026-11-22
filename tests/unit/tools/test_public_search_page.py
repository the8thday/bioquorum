import json

import pytest

from bioquorum.connectors.pages import gwas_associations, trial_studies


def test_malformed_result_is_distinct_from_a_successful_empty_search():
    assert gwas_associations({"page": {"totalElements": 0}}) == []
    assert trial_studies({"studies": []}) == []
    for payload in (
        {},
        {"message": "maintenance"},
        {"_embedded": {}, "page": {"totalElements": 1}},
    ):
        with pytest.raises(ValueError):
            gwas_associations(payload)
    with pytest.raises(ValueError):
        trial_studies({"error": "unavailable"})


@pytest.mark.anyio
async def test_public_source_tool_requires_declared_permission_and_returns_cited_page(ctx):
    import httpx2

    from bioquorum.core.enums import Classification
    from bioquorum.tools import invoke
    from bioquorum.tools.public_sources import public_sources

    async def respond(request):
        assert request.url.host == "clinicaltrials.gov"
        return httpx2.Response(200, json={"studies": []})

    ctx.classification = Classification.PUBLIC
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        tool = public_sources(client).get("clinical_trials")
        assert tool.classification_floor is Classification.PUBLIC
        assert tool.permission.value == "connector"
        out = await invoke(tool, {"query": "NCT00000000"}, ctx)
        assert not out.is_error
        assert "Response SHA-256:" in out.text()
        assert out.text().endswith("[]")


def test_parsers_accept_the_recorded_live_envelopes():
    from pathlib import Path

    directory = Path(__file__).parents[2] / "fixtures/public"
    for name, parse in (("gwas", gwas_associations), ("trials", trial_studies)):
        sample = json.loads((directory / f"{name}.json").read_text())
        assert len(sample["response_sha256"]) == 64
        assert len(parse(sample["projection"])) == 1


@pytest.mark.anyio
async def test_exact_trial_returns_posted_primary_outcome_with_arm_denominators(ctx):
    import httpx2

    from bioquorum.core.enums import Classification
    from bioquorum.tools import invoke
    from bioquorum.tools.public_sources import public_sources

    primary = {
        "type": "PRIMARY",
        "unitOfMeasure": "Participants",
        "groups": [{"id": "G1", "title": "Active"}],
        "denoms": [{"units": "Participants", "counts": [{"groupId": "G1", "value": "17"}]}],
        "classes": [{"categories": [{"measurements": [{"groupId": "G1", "value": "6"}]}]}],
    }
    record = {
        "protocolSection": {"identificationModule": {"nctId": "NCT00000001"}},
        "hasResults": True,
        "resultsSection": {"outcomeMeasuresModule": {"outcomeMeasures": [primary]}},
    }

    async def respond(request):
        fields = request.url.params["fields"].split(",")
        assert "HasResults" in fields and "OutcomeMeasuresModule" in fields
        return httpx2.Response(200, json={"studies": [record]})

    ctx.classification = Classification.PUBLIC
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        result = await invoke(
            public_sources(client).get("clinical_trials"), {"query": "NCT00000001"}, ctx
        )
    assert not result.is_error

    (row,) = json.loads(result.text().splitlines()[-1])
    assert row["hasResults"] and row["firstReportedPrimaryOutcome"] == primary
