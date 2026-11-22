"""Use a deterministic worker with a real kernel and the normal workbench tool registry."""

import json
from pathlib import Path

from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core.enums import Classification
from bioquorum.models import ModelClient, Router


def upload_controls(directory: str) -> ResearchControls:
    ws = init_project(Path(directory), classification=Classification.PUBLIC)
    ws.config = {
        "agent": {"references": True, "literature_tools": ["biomcp__biomcp"]},
        "mcp": {
            "servers": {
                "biomcp": {
                    "replay": str(
                        Path(__file__).resolve().parents[1] / "fixtures" / "mcp" / "biomcp.json"
                    ),
                    "clearance": "public",
                }
            }
        },
        "providers": {"openai": {}},
        "roles": {"worker": "openai:fixture", "reviewer": "openai:review"},
    }

    def respond(messages, info):
        parts = [p for m in messages for p in m.parts]
        if any(
            isinstance(p, pa.SystemPromptPart) and "split a research assistant" in p.content
            for p in parts
        ):
            return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
        if any(
            isinstance(p, pa.SystemPromptPart) and "Summarize the older" in p.content for p in parts
        ):
            return pa.ModelResponse(
                parts=[pa.TextPart(content="The earlier recorded sample count was requested.")]
            )
        last_user = max(i for i, part in enumerate(parts) if isinstance(part, pa.UserPromptPart))
        user = parts[last_user]
        assert isinstance(user, pa.UserPromptPart) and isinstance(user.content, str)
        prompt = user.content
        returned = any(isinstance(p, pa.ToolReturnPart) for p in parts[last_user + 1 :])
        if prompt.startswith("Recalculate after excluding"):
            if returned:
                return pa.ModelResponse(
                    parts=[pa.TextPart(content="Remaining count: {{remaining}}.")]
                )
            selected = json.loads(
                prompt.split("<untrusted-tool-output>\n")[-1].split("\n</untrusted-tool-output>")[0]
            )
            samples = json.loads(selected["selected_content"])["samples"]
            assert samples == ["b"] and selected["run"]
            code = (
                f"retained = [r for r in rows if r['sample'] not in {samples!r}]\n"
                "remaining = sum(int(r['count']) for r in retained)\n"
                "with open('recomputed.svg', 'w') as f: "
                'f.write(f\'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><text x="5" y="25">{remaining}</text></svg>\')\n'
                "bq.value('remaining', remaining)"
            )
            return pa.ModelResponse(
                parts=[
                    pa.ToolCallPart(
                        "python_execute", {"code": code, "output_files": ["recomputed.svg"]}
                    )
                ]
            )
        if prompt.startswith("Identify my structure selection"):
            selected = json.loads(
                prompt.split("<untrusted-tool-output>\n")[-1].split("\n</untrusted-tool-output>")[0]
            )
            pick = selected["selection"]
            assert pick["residue"] == 52 and pick["insertion"] == "A" and pick["chain"] == "H"
            return pa.ModelResponse(parts=[pa.TextPart(content="Selected author residue H 52A.")])
        if any(isinstance(p, pa.ToolReturnPart) for p in parts[last_user + 1 :]):
            name = "total" if "Attached project files" in prompt else "initial_count"
            return pa.ModelResponse(parts=[pa.TextPart(content="Total samples: {{" + name + "}}.")])
        if "Selected records:" in prompt:
            assert "initial_count" in prompt and "sum([2, 3])" in prompt
            return pa.ModelResponse(
                parts=[pa.TextPart(content="The selected calculation summed the two groups.")]
            )
        if prompt == "Calculate the initial count.":
            return pa.ModelResponse(
                parts=[
                    pa.ToolCallPart(
                        "python_execute", {"code": "bq.value('initial_count', sum([2, 3]))"}
                    )
                ]
            )
        if prompt == "Describe the groups.":
            return pa.ModelResponse(
                parts=[pa.TextPart(content="The initial calculation used two groups.")]
            )
        raw = prompt.split("<untrusted-tool-output>\n")[-1].split("\n</untrusted-tool-output>")[0]
        path = json.loads(raw)[0]["path"]
        code = (
            "import csv\n"
            f"with open({path!r}) as source:\n"
            "    rows = list(csv.DictReader(source))\n"
            "total = sum(int(row['count']) for row in rows)\n"
            "with open('summary.csv', 'w') as f: f.write(f'count\\n{total}\\n')\n"
            "from html import escape\n"
            'bars = \'\'.join(f\'<text x="12" y="{46+i*42}">{escape(r["sample"])}</text>\''
            ' f\'<rect x="44" y="{31+i*42}" width="{int(r["count"])*60}" height="21" fill="#6455ae"/>\''
            ' f\'<text x="{52+int(r["count"])*60}" y="{47+i*42}">{int(r["count"])}</text>\' for i,r in enumerate(rows))\n'
            "with open('counts.svg', 'w') as f: f.write('<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 360 125\">' + bars + '</svg>')\n"
            "with open('report.md', 'w') as f: f.write(f'# Count report\\n\\n## Total samples\\n| count |\\n| --- |\\n| {total} |\\n\\n## Counts by sample\\n![Counts by sample](counts.svg)')\n"
            "with open('marked.faa','w') as f: f.write('>fixture\\nMLPK\\n')\n"
            "with open('marked.faa.annotations.tsv','w') as f: f.write('record\\tstart\\tend\\tlabel\\n1\\t2\\t3\\tmapped segment\\n')\n"
            "bq.figure_points('counts.svg', [r['sample'] for r in rows], [.3, .7], [.35, .7])\n"
            "bq.value('total', total)"
        )
        return pa.ModelResponse(
            parts=[pa.ToolCallPart("python_execute", {"code": code, "input_files": [path]})]
        )

    controls = ResearchControls(
        ws,
        make_client=lambda ws, store: ModelClient(
            Router(ws.models_config()), store, factory=lambda route: FunctionModel(respond)
        ),
    )
    controls.new_session("Upload fixture")
    return controls
