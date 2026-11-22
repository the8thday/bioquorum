"""Offline workbench fixture for the reusable Scenario D demonstration."""

import asyncio
import sys
from pathlib import Path

from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application.controls import ResearchControls
from bioquorum.models import ModelClient, Router


def scenario_d_controls(directory: str) -> ResearchControls:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from evals.v10_scenario_d import seed
    finally:
        sys.path.pop(0)

    controls = asyncio.run(seed(Path(directory)))
    controls.ws.config.update(
        {
            "providers": {"openai": {}},
            "roles": {"worker": "openai:fixture", "reviewer": "openai:review"},
        }
    )

    # The calculation and the failed execution are real. The repair-response fixture
    # only checks that the researcher can send a draft and reopen its recorded answer.
    def respond(messages, info):
        parts = [part for message in messages for part in message.parts]
        if any(
            isinstance(p, pa.SystemPromptPart) and "split a research assistant" in p.content
            for p in parts
        ):
            return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
        return pa.ModelResponse(
            parts=[
                pa.TextPart(
                    content="Validate the arm categories, then read the corrected arm column before recomputing."
                )
            ]
        )

    controls.make_client = lambda ws, store: ModelClient(
        Router(ws.models_config()), store, factory=lambda _: FunctionModel(respond)
    )
    return controls
