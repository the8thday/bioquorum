"""FastAPI requests against the real event store and packaged cassette."""

import re
from pathlib import Path

import httpx2
import pytest

from bioquorum.application import demo
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.server.app import create_app

pytestmark = pytest.mark.anyio
HOST = "127.0.0.1:8181"
ORIGIN = f"http://{HOST}"


async def test_audit_is_scoped_paginated_and_omits_arguments(tmp_path):
    controls = demo.make_demo(tmp_path)
    sid = controls.new_session("Audit")
    other = controls.new_session("Other")
    for session, tool in ((other, "private_other_tool"), (sid, "private_input")):
        controls.store.append(
            ev.ToolCallRequested(
                call_id="request",
                tool=tool,
                args={"query": "sensitive question"},
                permission=PermissionClass.CONNECTOR,
            ),
            session_id=session,
            actor=Actor.AGENT,
        )
    controls.store.append(
        ev.PermissionDecided(
            request_id="ask", permission=PermissionClass.CONNECTOR, target="service", granted=False
        ),
        session_id=sid,
        actor=Actor.USER,
    )
    transport = httpx2.ASGITransport(app=create_app(controls, HOST))
    async with httpx2.AsyncClient(transport=transport, base_url=ORIGIN) as http:
        path = f"/api/sessions/{sid}/audit"
        response = await http.get(path + "?limit=1")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["entries"][0]["details"]["granted"] is False
        second = await http.get(path + f"?limit=1&before={data['next_before']}")
        assert second.json()["entries"][0]["details"]["tool"] == "private_input"
        assert "sensitive question" not in second.text and "private_other_tool" not in second.text
        assert second.json()["next_before"] is None
        assert (await http.get(path + "?limit=101")).status_code == 400


async def test_offline_record_reloads_and_forks(tmp_path: Path):
    controls = demo.make_demo(tmp_path)
    app = create_app(controls, HOST)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as http:
        page = await http.get("/")
        assert page.headers["content-security-policy"]
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.text)
        for suffix, mime in [
            (".js", "text/javascript"),
            (".css", "text/css"),
            (".svg", "image/svg+xml"),
        ]:
            (url,) = [path for path in assets if path.endswith(suffix)]
            response = await http.get(url)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mime)
            assert response.content
        logo = await http.get("/assets/logo.svg")
        assert logo.status_code == 200
        assert logo.headers["content-type"].startswith("image/svg+xml")
        assert b"<svg" in logo.content
        assert (await http.get("/assets/index.html")).status_code == 404
        plate = await http.get("/assets/radiolaria.webp")
        assert plate.headers["content-type"] == "image/webp"
        assert plate.content[:4] == b"RIFF" and plate.content[8:12] == b"WEBP"
        denied = await http.post(
            "/api/sessions", json={"title": "No"}, headers={"Origin": "https://example.org"}
        )
        assert denied.status_code == 403
        denied = await http.get("/api/sessions", headers={"Host": "other.invalid"})
        assert denied.status_code == 403
        row = await http.post("/api/sessions", json={"title": "Metformin"})
        sid = row.json()["id"]
        base = f"/api/sessions/{sid}"
        result = await http.post(
            base + "/turns", json={"question": demo.QUESTION, "steps": demo.STEPS}
        )
        turn = result.json()["turn_id"]
        pending = (await http.get(base)).json()
        assert pending["pending_plan"] == turn
        assert not pending["answers"]
        assert not controls.tasks
        assert not [e for e in controls.store.read(sid) if e.type == "model_call_started"]
        assert (
            await http.post(base + f"/turns/{turn}/plan", json={"approve": True})
        ).status_code == 204
        await controls.tasks[sid].task
        record = (await http.get(base)).json()
        assert record["turns"][0]["status"] == "completed", record["turns"]
        assert record["turns"][0]["plan"]["done"] == [0, 1]
        answer = record["answers"][0]
        assert [claim["status"] for claim in answer["claims"]] == ["reviewed"] * 4 + ["unverified"]
        refs = [
            part["ref"]
            for line in answer["paragraphs"]
            for part in line["segments"]
            if "ref" in part
        ]
        value, quote = refs[:2]
        address = f"{base}/answers/{answer['id']}"
        run = (await http.get(f"{address}/value/{value['run_id']}")).json()
        assert run["values"][0]["value"] == 3
        assert run["inputs"][0]["content_hash"]
        assert 'value("excerpts"' in run["code"]
        source = (await http.get(f"{address}/quote/{quote['call_id']}")).json()
        assert quote["quote"] in source["source"]
        image = await http.get(f"{base}/figures/{run['figures'][0]}")
        assert image.headers["content-type"] == "image/svg+xml"
        assert image.headers["content-security-policy"].startswith("sandbox")
        assert (await http.get(f"{base}/figures/{'f' * 64}")).status_code == 404
        figure = next(item for item in record["products"] if item["kind"] == "figure")
        assert figure["hash"] == run["figures"][0] and figure["turn"] == turn
        preview_path = f"/artifacts/{figure['id']}/preview/image"
        preview = await http.get(base + preview_path)
        assert preview.headers["content-security-policy"].startswith("sandbox")
        assert preview.content == image.content
        unrelated = controls.new_session("Independent")
        assert (await http.get(f"/api/sessions/{unrelated}" + preview_path)).status_code == 404
        fork = (await http.post(base + "/fork", json={"title": "Alternative"})).json()["id"]
        fork_base = f"/api/sessions/{fork}"
        fork_record = (await http.get(fork_base)).json()
        assert (await http.get(fork_base + preview_path)).content == image.content
        assert fork_record["answers"][0]["id"] == answer["id"]
        assert fork_record["answers"][0]["inherited"]
        assert fork_record["last_seq"] == 0
        reloaded = create_app(demo.make_demo(tmp_path), HOST)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=reloaded), base_url=ORIGIN
        ) as reopened:
            assert (await reopened.get(fork_base)).json() == fork_record


async def test_unplanned_control_skips_approval_gate(tmp_path: Path):
    controls = demo.make_demo(tmp_path)
    sid = controls.new_session("Unplanned control")
    controls.submit(sid, demo.QUESTION)
    await controls.tasks[sid].task
    events = controls.store.read(sid)
    assert events[-1].payload["outcome"] == "completed"
    assert any(e.type == "tool_call_requested" for e in events)
    assert not any(e.type in ("plan_proposed", "plan_approved", "plan_step_done") for e in events)


async def test_create_switch_and_reopen_project(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    controls = demo.make_demo(tmp_path / "original")
    app = create_app(controls, HOST)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
        ) as http,
    ):
        response = await http.post(
            "/api/projects", json={"name": "My project", "classification": "public"}
        )
        assert response.status_code == 201
        path = response.json()["href"]
        assert path.startswith("/projects/")
        assert (await http.get(path + "api/sessions")).json()["sessions"] == []
        created = await http.post(path + "api/sessions", json={"title": "Private to this project"})
        assert created.is_success
        assert created.json()["id"] not in (await http.get("/api/sessions")).text
        assert "My project" in (await http.get("/api/projects")).text
        assert (
            await http.post("/api/projects", json={"name": "  ", "classification": "public"})
        ).status_code == 400
    reopened = create_app(demo.make_demo(tmp_path / "original"), HOST)
    async with (
        reopened.router.lifespan_context(reopened),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=reopened), base_url=ORIGIN) as http,
    ):
        assert (await http.get("/api/projects")).json()[-1]["href"] == path
        assert "Private to this project" in (await http.get(path + "api/sessions")).text


async def test_review_registry_lookup_is_explicit_and_public_only(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bioquorum.application import research
    from bioquorum.application.controls import ResearchControls
    from bioquorum.application.workspace import init_project
    from bioquorum.core.enums import Classification
    from bioquorum.core.messages import Message

    calls = []

    async def audit(*args, online_citations=False, **kwargs):
        calls.append(online_citations)
        return SimpleNamespace(errors=[])

    monkeypatch.setattr(research, "audit", audit)
    for level in (Classification.INTERNAL, Classification.PUBLIC):
        ws = init_project(tmp_path / level.value, classification=level)
        controls = ResearchControls(ws)
        sid = controls.new_session("Review")
        event = controls.store.append(
            ev.AssistantMessage(message=Message.assistant("Recorded.")),
            session_id=sid,
            actor=Actor.AGENT,
        )
        path = f"/api/sessions/{sid}/answers/{event.id}/review"
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=create_app(controls, HOST)),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        ) as http:
            assert (await http.post(path, json={})).status_code == 204
            result = await http.post(path, json={"online_citations": True})
            assert result.status_code == (204 if level == Classification.PUBLIC else 400)
    assert calls == [False, False, True]
