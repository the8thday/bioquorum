"""Send a real chart through BioQuorum to the native macOS Vision OCR model."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bioquorum.application import research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Classification, MessageRole, ModelRole
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, ToolCallPart
from bioquorum.tools import default_registry, invoke

SWIFT = """import Foundation
import Vision
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
try VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1])).perform([request])
let result = (request.results ?? []).compactMap { observation -> [String: Any]? in
    guard let text = observation.topCandidates(1).first else { return nil }
    return ["text": text.string, "y": observation.boundingBox.midY]
}
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
"""


def handler(source: Path) -> type[BaseHTTPRequestHandler]:
    class VisionHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            try:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                packet = json.loads(raw)
                images = [
                    part["image_url"]["url"]
                    for msg in packet["messages"]
                    if isinstance(msg.get("content"), list)
                    for part in msg["content"]
                    if part.get("type") == "image_url"
                ]
                if images:
                    with tempfile.TemporaryDirectory(prefix="bq-vision-") as directory:
                        image = Path(directory) / "input.png"
                        image.write_bytes(
                            base64.b64decode(images[0].split(",", 1)[1], validate=True)
                        )
                        out = (
                            subprocess.run(
                                ["swift", str(source), str(image)],
                                capture_output=True,
                                check=True,
                                timeout=90,
                            )
                            .stdout.decode()
                            .strip()
                        )
                else:
                    out = "[]"
                self.reply(
                    200,
                    {
                        "id": "vision-ocr",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "vision-ocr",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": out},
                            }
                        ],
                    },
                )
            except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
                self.reply(400, {"error": {"message": type(exc).__name__}})

        def reply(self, status: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return VisionHandler


async def run(image: Path, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="bq-native-vision-") as directory:
        root = Path(directory)
        source = root / "Recognize.swift"
        source.write_text(SWIFT)
        chart = root / "chart.png"
        chart.write_bytes(image.read_bytes())
        ws = workspace.init_project(root, classification=Classification.PUBLIC)
        controls = ResearchControls(ws)
        sid = controls.new_session("GSE96583 figure transport")
        policy = workspace.default_permissions(ws, allow=[], servers=[])
        ctx = workspace.tool_context(ws, controls.store, sid, policy)
        call = ToolCallPart(
            name="python_execute",
            call_id=new_id(),
            args={
                "code": "from IPython.display import Image, display\ndisplay(Image(filename='chart.png'))",
                "input_files": ["chart.png"],
            },
        )
        try:
            fig = await invoke(
                default_registry().get(call.name), call.args, ctx, call_id=call.call_id
            )
        finally:
            assert ctx.kernels is not None
            await ctx.kernels.shutdown_all()
        if fig.is_error:
            raise RuntimeError(fig.text())
        messages = [
            Message.user("Read the text on this quality-control figure."),
            Message(role=MessageRole.ASSISTANT, parts=[call]),
            Message.tool(fig),
        ]
        with ThreadingHTTPServer(("127.0.0.1", 0), handler(source)) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                results = []
                for enabled in (False, True):
                    ws.config = {
                        "providers": {
                            "vision": {
                                "kind": "ollama",
                                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                            }
                        },
                        "roles": {"worker": "vision:vision-ocr"},
                        "models": {"vision:vision-ocr": {"vision": True}},
                        "agent": {"vision": enabled},
                    }
                    client = research.model_client(ws, controls.store)
                    value = await client.complete(
                        ModelRole.WORKER, messages, session_id=sid, classification=ws.classification
                    )
                    results.append({"vision": enabled, "regions": json.loads(value.message.text())})
            finally:
                server.shutdown()
                thread.join()
    expected = ["GSE96583", "Detected genes", "Retained"]
    text = " ".join(region["text"] for region in results[-1]["regions"])
    report = {
        "backend": "macOS Vision VNRecognizeTextRequest",
        "figure": image.name,
        "expected_labels": expected,
        "matched": [label for label in expected if label in text],
        "runs": results,
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(report["matched"])
    if results[0]["regions"] or report["matched"] != expected:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.image, args.output))
