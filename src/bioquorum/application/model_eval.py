"""Score configured, policy-eligible models on fixed, role-specific offline question material."""

from bioquorum.application.workspace import Workspace
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ModelRole
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.models import ModelClient, ModelUnavailableError, Router
from bioquorum.policy.secrets import redact
from bioquorum.storage.events import EventStore

# Each item is deliberately small enough to inspect. These measure narrow capabilities.
CASES = {
    ModelRole.WORKER: (
        ("The measured inputs are 4 and 9. Which total follows? A: 12. B: 13. C: 14.", "B"),
        (
            "A record labels its unit as mg. Which unit can be copied directly? A: mg. B: mL. C: nM.",
            "A",
        ),
    ),
    ModelRole.REVIEWER: (
        (
            "Source: two samples failed QC. Report: three samples failed QC. A: supported. B: contradicted. C: missing source.",
            "B",
        ),
        (
            "A table gives a mean only. A report claims significance. A: proven. B: disproven. C: insufficient evidence.",
            "C",
        ),
    ),
    ModelRole.PLANNER: (
        (
            "A table has duplicate sample IDs. What comes before estimating group means? A: resolve IDs. B: publish. C: label every row independent.",
            "A",
        ),
        (
            "A plan requires source verification before synthesis. Choose the order. A: synthesize then verify. B: omit verification. C: verify then synthesize.",
            "C",
        ),
    ),
    ModelRole.SUMMARIZER: (
        (
            "Compress: A passed; B failed; C was not tested. A: all passed. B: mixed with one unknown. C: C failed.",
            "B",
        ),
        (
            "Compress: measurements agree within one laboratory; external replication is pending. A: replicated across sites. B: no evidence. C: within-lab agreement only.",
            "C",
        ),
    ),
}


async def evaluate(ws: Workspace, store: EventStore, client: ModelClient) -> None:
    for spec, allowed in client.router.worker_options(ws.classification).items():
        if not allowed:
            continue
        pinned = client.for_evaluation(spec, ws.classification)
        sid = new_id()
        scores = {}
        responses = {}
        for role, cases in CASES.items():
            correct = failed = 0
            replies: list[str | None] = []
            for prompt, expected in cases:
                try:
                    response = await pinned.complete(
                        role,
                        [
                            Message.system("Return the single selected letter."),
                            Message.user(prompt),
                        ],
                        session_id=sid,
                        classification=ws.classification,
                    )
                    reply = redact(response.message.text()).strip()
                    replies.append(reply[:256])
                    correct += reply.upper() == expected
                except ModelUnavailableError:
                    failed += 1
                    replies.append(None)
            responses[role.value] = replies
            scores[role.value] = ev.RoleScore(correct=correct, total=len(cases), failed=failed)
        store.append(
            ev.ModelEvaluated(
                project_id=ws.project_id,
                model=spec,
                evaluation_id=sid,
                scores=scores,
                responses=responses,
            ),
            actor=Actor.SYSTEM,
            session_id=None,
        )


def results(ws: Workspace, store: EventStore) -> dict:
    allowed = Router(ws.models_config()).worker_options(ws.classification)
    scores = {
        p["model"]: p["scores"]
        for e in store.read(None, types=["model_evaluated"])
        if (p := e.payload)["project_id"] == ws.project_id and allowed.get(p["model"])
    }
    suggestions = {}
    for role in CASES:
        eligible = {
            model: entry[role.value]["correct"]
            for model, entry in scores.items()
            if role.value in entry
            and not entry[role.value]["failed"]
            and entry[role.value]["correct"]
        }
        if eligible:
            suggestions[role.value] = sorted(
                model for model, n in eligible.items() if n == max(eligible.values())
            )
    return {"scores": scores, "suggestions": suggestions}
