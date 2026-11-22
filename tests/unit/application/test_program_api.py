"""Research lines and dated material versions through the workbench HTTP API."""

import httpx2
import pytest

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core.enums import Classification
from bioquorum.core.hashing import sha256_bytes
from bioquorum.server.app import create_app

HOST = "127.0.0.1:8181"
ORIGIN = f"http://{HOST}"
pytestmark = pytest.mark.anyio


async def test_program_and_material_versions_round_trip(tmp_path):
    controls = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    transport = httpx2.ASGITransport(app=create_app(controls, HOST))
    async with httpx2.AsyncClient(
        transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        programs = "/api/programs"
        assert (await client.get(programs)).json() == {"programs": []}
        result = await client.post(programs, json={"name": "Plate reread"})
        assert result.status_code == 201
        pid = result.json()["id"]
        program = f"{programs}/{pid}"
        sid = (await client.post("/api/sessions", json={"title": "First visit"})).json()["id"]
        assert (await client.post(program + "/sessions", json={"session_id": sid})).is_success
        detail = (await client.get(program)).json()
        assert detail["name"] == "Plate reread" and detail["sessions"][0]["id"] == sid
        assert (await client.post(program + "/rename", json={"name": "Reread"})).is_success
        assert (await client.get(programs)).json()["programs"][0]["name"] == "Reread"

        async def upload(content):
            reply = await client.post(
                f"/api/sessions/{sid}/artifacts?name=plate.csv",
                content=content,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert reply.status_code == 201
            return reply.json()["id"]

        base = program + "/materials"
        original = b"sample,signal\nA,3\n"
        corrected = b"sample,signal\nB,3\n"
        old = await client.post(
            base,
            json={"file_id": await upload(original), "kind": "experimental_data", "source": "Lab"},
        )
        assert old.status_code == 201
        v1 = old.json()["id"]
        change = await client.post(
            f"{base}/{v1}/supersede",
            json={
                "file_id": await upload(corrected),
                "source": "Lab",
                "reason": "Sample relabelled",
            },
        )
        assert change.status_code == 201
        v2 = change.json()["id"]
        history = await client.get(base)
        assert history.headers["cache-control"] == "no-store"
        rows = {row["id"]: row for row in history.json()["versions"]}
        assert rows[v1]["status"] == "superseded" and rows[v2]["status"] == "active"
        assert rows[v1]["hash"] == sha256_bytes(original)
        assert rows[v2]["hash"] == sha256_bytes(corrected)
        assert rows[v1]["material_id"] == rows[v2]["material_id"]
        assert (
            await client.post(f"{base}/{v2}/withdraw", json={"reason": "Plate recalled"})
        ).is_success
        assert "withdrawn" in {row["status"] for row in (await client.get(base)).json()["versions"]}
        for version, expected in [(v1, original), (v2, corrected)]:
            download = await client.get(f"{base}/{version}/file")
            assert download.content == expected
            assert download.headers["content-disposition"] == 'attachment; filename="plate.csv"'
            assert download.headers["x-content-type-options"] == "nosniff"

        second = (await client.post(programs, json={"name": "Other line"})).json()["id"]
        private_file = f"{programs}/{second}/materials/{v1}/file"
        assert (await client.get(private_file)).status_code == 404
        assert (await client.post(program + "/close", json={})).is_success
        assert (await client.get(program)).json()["status"] == "closed"
