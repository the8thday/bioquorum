"""Frozen paired domain-Skill checks, billed through one persistent campaign ledger."""

import asyncio
import json
import os
from pathlib import Path

import httpx2
from v08_budget import DEVELOPMENT_MODEL, MAX_OUTPUT, MeteredOpenAI, SpendingLog

from bioquorum.application import research, workspace
from bioquorum.core.enums import Classification, ModelRole
from bioquorum.core.messages import Message
from bioquorum.models import ModelClient, Router
from bioquorum.models.registry import build_model
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.skills.loader import DOMAIN_SKILLS
from bioquorum.storage.events import EventStore
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke
from bioquorum.tools.registry import ToolRegistry

ROOT = Path(__file__).parent / "results/v11"
CASES = Path(__file__).parent / "questions/v11-domain-heldout.json"


async def run():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing")
    ROOT.mkdir(parents=True, exist_ok=True)
    ledger = SpendingLog(ROOT / "spending.sqlite", 2_000_000)
    cases = json.loads(CASES.read_text())
    for index, (skill, question, expected) in enumerate(cases):
        for enabled in (False, True) if index % 2 == 0 else (True, False):
            directory = ROOT / f"{index:02d}-{int(enabled)}"
            output = directory / "answer.json"
            if output.is_file():
                continue
            directory.mkdir(exist_ok=True)
            project = directory / "workspace"
            ws = (
                workspace.load(project)
                if (project / "bioquorum.toml").is_file()
                else workspace.init_project(project, classification=Classification.PUBLIC)
            )
            ws.config = {
                "agent": {"domain_skills": enabled},
                "providers": {"openai": {}},
                "extensions": {"skills": {k: {"enabled": k == skill} for k in DOMAIN_SKILLS}},
                "roles": {"worker": "openai:" + DEVELOPMENT_MODEL},
            }
            store = EventStore(ws.database())
            sid = workspace.start_session(ws, store, "Held-out domain question")
            policy = PermissionPolicy()
            registry = ToolRegistry()
            prompts = research._project_context(
                ws, store, sid, registry, policy, ws.agent_options()
            )
            system = 'Choose one answer. Return JSON {"choice":"A","reason":"one sentence"} using the selected letter.'
            messages = [Message.system("\n".join([system, *prompts])), Message.user(question)]
            ctx = ToolContext(store, sid, policy, classification=ws.classification)
            meter = MeteredOpenAI(ledger, model=DEVELOPMENT_MODEL)
            opened = []
            usages = []
            async with httpx2.AsyncClient(transport=meter, timeout=90) as http:
                client = ModelClient(
                    Router(ws.models_config()),
                    store,
                    factory=lambda route: build_model(route, http_client=http),
                    max_output_tokens=MAX_OUTPUT,
                )
                for attempt in range(3):
                    result = await client.complete(
                        ModelRole.WORKER,
                        messages,
                        registry.definitions() if attempt < 2 else [],
                        session_id=sid,
                        classification=ws.classification,
                    )
                    usages.append(result.usage)
                    messages.append(result.message)
                    if not result.message.tool_calls():
                        break
                    for call in result.message.tool_calls():
                        part = await invoke(
                            registry.get(call.name), call.args, ctx, call_id=call.call_id
                        )
                        messages.append(Message.tool(part))
                        if not part.is_error:
                            opened.append(call.args.get("name"))
            raw = result.message.text()
            try:
                correct = json.loads(raw)["choice"] == expected
            except (ValueError, TypeError, KeyError):
                correct = False
            output.write_text(
                json.dumps(
                    dict(
                        skill=skill,
                        case=index,
                        enabled=enabled,
                        opened=opened,
                        answer=raw,
                        correct=correct,
                        usage=usages,
                    ),
                    indent=2,
                )
            )
            print(index, skill, "on" if enabled else "off", correct, flush=True)


if __name__ == "__main__":
    asyncio.run(run())
