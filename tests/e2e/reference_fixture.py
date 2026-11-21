"""A fixed follow-up response keyed to the expected messages and retrieval tools."""

import json
from pathlib import Path

from bioquorum.agent.prompts import kernel_instructions, worker_system
from bioquorum.application.controls import ResearchControls
from bioquorum.application.environment import package_inventory
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.messages import Message, fence_untrusted
from bioquorum.core.refs import NamedValue
from bioquorum.models import ModelCassette, ModelClient, Router
from bioquorum.models.cassette import request_key
from bioquorum.tools import default_registry


def reference_controls(directory: str) -> ResearchControls:
    ws = init_project(Path(directory), classification=Classification.PUBLIC)
    model = "openai:fixture"
    ws.config = {
        "providers": {"openai": {}},
        "roles": {"worker": model},
        "agent": {"references": True},
    }
    cassette = ModelCassette(Path(directory) / "reference.json")
    controls = ResearchControls(
        ws,
        make_client=lambda ws, store: ModelClient(
            Router(ws.models_config()), store, cassette=cassette
        ),
    )
    sid = controls.new_session("Reference fixture")
    code = 'bq.value("eligible", 17 + 6, unit="samples")'
    digest = sha256_bytes(code.encode())
    value = NamedValue(name="eligible", value=23, unit="samples")
    answer = Message.assistant("Eligible samples: {{eligible}}.")
    for payload in [
        ev.RunStarted(run_id="original-count", kernel_id="fixture", code=code, code_hash=digest),
        ev.RunFinished(run_id="original-count", run_key=digest, values=[value]),
        ev.AssistantMessage(message=answer),
    ]:
        controls.store.append(payload, session_id=sid, actor=Actor.AGENT)
    expected = {
        "kind": "value",
        "id": "original-count",
        "run": "original-count",
        "code": code,
        "code_hash": digest,
        "input_hashes": [],
        "declaration": value.model_dump(mode="json"),
    }
    question = Message.user(
        "这个数是怎么算的\uff1f\n\nSelected records:\n"
        + fence_untrusted(json.dumps(expected, ensure_ascii=False))
    )
    context = kernel_instructions(
        r_available=False, inventory=package_inventory(ws.root, use_pixi=False)
    )
    key = request_key(
        model,
        [Message.system("\n\n".join([worker_system(binding=True), *context])), answer, question],
        default_registry(r_available=False).definitions(),
    )
    cassette.store(
        key,
        model,
        Message.assistant("The recorded code adds 17 and 6 eligible samples."),
        {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0},
    )
    return controls
