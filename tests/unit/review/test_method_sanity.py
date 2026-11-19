import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.core.enums import ClaimKind, Classification, Severity
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.review.checks.methods import MethodSanity
from bioquorum.review.evidence import ClaimInput, Evidence, RunEvidence
from bioquorum.review.modeljson import ReviewModelError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "answer",
    [
        '{"findings":[{"claim_index":1,"severity":"error","reason":"Rows share donors"}]}',
        '{"findings":[{"claim_index":99,"severity":"error","reason":"Unknown"}]}',
    ],
)
async def test_method_check_fences_evidence_and_rejects_unknown_claims(store, answer):
    def scripted(messages, info):
        text = str(messages[-1].parts[-1])
        assert "<untrusted-tool-output>" in text and "check donors" in text
        return pa.ModelResponse(parts=[pa.TextPart(content=answer)])

    config = ModelsConfig.from_dict(
        {"providers": {"deepseek": {}}, "roles": {"reviewer": "deepseek:test"}}
    )
    client = ModelClient(Router(config), store, factory=lambda _: FunctionModel(scripted))
    checker = MethodSanity(client, "S", Classification.PUBLIC)
    claims = [ClaimInput(1, "Cells are independent.", ClaimKind.NUMERIC)]
    evidence = Evidence(runs=[RunEvidence("r", True, "check donors", "donors=4")])
    if '"claim_index":99' in answer:
        with pytest.raises(ReviewModelError):
            await checker.run(claims, evidence)
    else:
        result = await checker.run(claims, evidence)
        assert result[0].severity == Severity.ERROR
        assert result[0].claim_index == 1
