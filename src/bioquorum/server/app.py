"""Local HTTP and SSE surface for the shared research commands."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from bioquorum.application import demo, project_memory, record, verify, workspace
from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.core import events as ev
from bioquorum.provenance.cas import IntegrityError
from bioquorum.runtime.sandbox import Isolation, IsolationUnavailable

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
WEB = files("bioquorum.server").joinpath("web")
ASSETS = {
    "index.html": "text/html",
    "workbench.css": "text/css",
    "workbench.js": "text/javascript",
    "radiolaria.webp": "image/webp",
}


class LocalOrigin:
    """Protect a loopback-only app against cross-origin requests and DNS rebinding."""

    def __init__(self, app: ASGIApp, *, host: str) -> None:
        self.app, self.host = app, host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            origin = f"http://{self.host}"
            headers = request.headers
            if (
                headers.get("host") != self.host
                or (
                    headers.get("sec-fetch-site") in ("cross-site", "same-site")
                    and request.url.path.startswith("/api/")
                )
                or headers.get("origin") not in (None, origin)
                or (request.method not in ("GET", "HEAD") and headers.get("origin") != origin)
            ):
                await JSONResponse({"detail": "Local workbench origin required"}, status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


class Title(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=12000)
    steps: list[str] | None = Field(default=None, max_length=12)


class PlanDecision(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=600)


class PermissionDecision(BaseModel):
    grant: bool


async def _body[T: BaseModel](request: Request, model: type[T]) -> T:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError("Expected application/json")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 32_768:
            raise ValueError("Request body exceeds 32 KiB")
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise ValueError("Invalid request fields") from None


def _sid(request: Request) -> str:
    return request.path_params["sid"]


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if re.match(rb"\s*(?:<\?xml[^>]*>\s*)?(?:<!DOCTYPE svg[^>]*>\s*)?<svg(?:\s|>)", data):
        return "image/svg+xml"
    raise LookupError("Unsupported image format")


def surface(controls: ResearchControls, host: str) -> dict[str, Any]:
    """Build routes, middleware and lifespan together for the ASGI entry point."""

    def kernel_notice() -> str:
        if controls.demo:
            return ""
        try:
            isolated = controls.ws.kernel_isolation()
        except (IsolationUnavailable, workspace.WorkspaceError) as exc:
            return str(exc)
        if isolated is Isolation.NONE:
            return "Python isolation is unavailable here. Code in this public project runs without a sandbox."
        if controls.ws.kernel_network():
            return "Network access is enabled in this public project's Python kernel."
        return ""

    async def asset(request: Request) -> Response:
        name = request.path_params.get("asset", "index.html")
        if name not in ASSETS or (name == "index.html" and "asset" in request.path_params):
            raise LookupError("Asset not found")
        return Response(
            WEB.joinpath(name).read_bytes(),
            media_type=ASSETS[name],
            headers={
                "Content-Security-Policy": CSP,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-cache",
            },
        )

    async def sessions(request: Request) -> Response:
        if request.method == "POST":
            body = await _body(request, Title)
            title = body.title.strip()
            if not title:
                raise ValueError("Enter a title")
            return JSONResponse({"id": controls.new_session(title)}, status_code=201)
        return JSONResponse(
            {
                "project": controls.ws.name,
                "project_notes": controls.ws.agent_options().project_notes,
                "kernel_notice": kernel_notice(),
                "classification": controls.ws.classification.value,
                "sessions": controls.sessions(),
                "example": {"question": demo.QUESTION, "steps": demo.STEPS}
                if controls.demo
                else None,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def read(request: Request) -> Response:
        return JSONResponse(
            record.snapshot(controls, _sid(request)), headers={"Cache-Control": "no-store"}
        )

    async def audit_history(request: Request) -> Response:
        before = request.query_params.get("before")
        payload = record.audit_log(
            controls,
            _sid(request),
            before=int(before) if before is not None else None,
            limit=int(request.query_params.get("limit", "50")),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def ask(request: Request) -> Response:
        body = await _body(request, Question)
        turn = controls.submit(_sid(request), body.question, steps=body.steps)
        return JSONResponse({"turn_id": turn.id}, status_code=202)

    async def plan(request: Request) -> Response:
        body = await _body(request, PlanDecision)
        await controls.decide_plan(
            _sid(request), request.path_params["turn_id"], approve=body.approve, reason=body.reason
        )
        return Response(status_code=204)

    async def permission(request: Request) -> Response:
        body = await _body(request, PermissionDecision)
        controls.decide_permission(_sid(request), request.path_params["request_id"], body.grant)
        return Response(status_code=204)

    async def stop(request: Request) -> Response:
        await _body(request, Empty)
        await controls.stop(_sid(request))
        return Response(status_code=204)

    async def fork(request: Request) -> Response:
        body = await _body(request, Title)
        title = body.title.strip()
        if not title:
            raise ValueError("Enter a title")
        return JSONResponse({"id": controls.fork(_sid(request), title)}, status_code=201)

    async def audit(request: Request) -> Response:
        await _body(request, Empty)
        await controls.review(_sid(request), request.path_params["answer_id"])
        return Response(status_code=204)

    async def project_note(request: Request) -> Response:
        sid = _sid(request)
        if request.method == "POST":
            edit = await _body(request, project_memory.SaveNote)
            note = await project_memory.save(controls, sid, edit)
        else:
            note = project_memory.snapshot(controls, sid)
        return JSONResponse(note, headers={"Cache-Control": "no-store"})

    async def rerun(request: Request) -> Response:
        await _body(request, Empty)
        return JSONResponse(
            await verify.rerun(controls, _sid(request), request.path_params["claim_id"])
        )

    async def replication(request: Request) -> Response:
        sid, claim = _sid(request), request.path_params["claim_id"]
        if request.method == "GET":
            return JSONResponse(verify.replication_quote(controls, sid, claim))
        body = await _body(request, verify.BlindRequest)
        return JSONResponse(await verify.replicate(controls, sid, claim, body))

    async def bundles(request: Request) -> Response:
        await _body(request, Empty)
        return JSONResponse(await verify.export_bundle(controls, _sid(request)), status_code=201)

    async def download_bundle(request: Request) -> Response:
        path = verify.bundle_file(controls, _sid(request), request.path_params["bundle_id"])
        return FileResponse(
            path,
            filename=f"bioquorum-{path.name}",
            media_type="application/zip",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def evidence(request: Request) -> Response:
        args = request.path_params
        kind: Literal["quote", "value"] = args["kind"]
        if kind not in ("quote", "value"):
            raise LookupError("Evidence type not found")
        return JSONResponse(
            record.binding(controls, _sid(request), args["answer_id"], args["reference"], kind),
            headers={"Cache-Control": "no-store"},
        )

    async def image(request: Request) -> Response:
        sid = _sid(request)
        controls.require_session(sid)
        digest = request.path_params["digest"]
        ref = next(
            (
                p.ref
                for e in controls.store.history(sid)
                if isinstance(p := e.parsed(), ev.RunOutput)
                and p.ref
                and p.ref.content_hash == digest
            ),
            None,
        )
        if ref is None or ref.size > 20_000_000:
            raise LookupError("Figure not found or exceeds the preview size limit")
        try:
            data = controls.ws.cas().get(ref)
        except (FileNotFoundError, IntegrityError):
            raise LookupError("Figure unavailable") from None
        return Response(
            data,
            media_type=image_type(data),
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    async def events(request: Request) -> Response:
        sid = _sid(request)
        available = controls.require_session(sid)
        try:
            cursor = int(
                request.headers.get("last-event-id", request.query_params.get("after", "-1"))
            )
        except ValueError:
            raise ValueError("Invalid event cursor") from None
        if not -1 <= cursor <= available[-1].seq:
            raise ValueError("Invalid event cursor")

        async def stream():
            nonlocal cursor
            yield "retry: 1500\n\n"
            progress = None
            idle = 0
            while not await request.is_disconnected():
                changes = controls.store.read(sid, from_seq=cursor + 1)
                for event in changes:
                    cursor = event.seq
                    yield f"id: {cursor}\nevent: change\ndata: {json.dumps(event.type)}\n\n"
                live = controls.tasks.get(sid)
                seen = (live.turn_id, live.characters) if live and live.characters else None
                if seen != progress:
                    progress = seen
                    yield f"event: progress\ndata: {json.dumps(seen)}\n\n"
                idle = 0 if changes else idle + 1
                if idle >= 40:
                    yield ": keepalive\n\n"
                    idle = 0
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    async def error(request: Request, exc: Exception) -> Response:
        code = (
            409 if isinstance(exc, ConflictError) else 404 if isinstance(exc, LookupError) else 400
        )
        return JSONResponse(
            {"detail": str(exc)}, status_code=code, headers={"Cache-Control": "no-store"}
        )

    @asynccontextmanager
    async def lifespan(app):
        with workspace.project_lock(controls.ws.root):
            await controls.recover()
            await controls.recover_verification()
            await controls.recover_literature()
            try:
                yield
            finally:
                await controls.close()

    base = "/api/sessions"
    session = base + "/{sid}"
    return {
        "middleware": [Middleware(LocalOrigin, host=host)],
        "exception_handlers": {ValueError: error, LookupError: error},
        "lifespan": lifespan,
        "routes": [
            Route("/", asset),
            Route("/assets/{asset}", asset),
            Route(base, sessions, methods=["GET", "POST"]),
            Route(session, read),
            Route(session + "/audit", audit_history),
            Route(session + "/events", events),
            Route(session + "/turns", ask, methods=["POST"]),
            Route(session + "/turns/{turn_id}/plan", plan, methods=["POST"]),
            Route(session + "/permissions/{request_id}", permission, methods=["POST"]),
            Route(session + "/stop", stop, methods=["POST"]),
            Route(session + "/fork", fork, methods=["POST"]),
            Route(session + "/answers/{answer_id}/review", audit, methods=["POST"]),
            Route(session + "/answers/{answer_id}/{kind}/{reference}", evidence),
            Route(session + "/figures/{digest}", image),
            Route(session + "/claims/{claim_id}/rerun", rerun, methods=["POST"]),
            Route(session + "/claims/{claim_id}/replication", replication, methods=["GET", "POST"]),
            Route(session + "/bundles", bundles, methods=["POST"]),
            Route(session + "/project-note", project_note, methods=["GET", "POST"]),
            Route(session + "/bundles/{bundle_id}", download_bundle),
        ],
    }


class Empty(BaseModel):
    pass


def create_app(controls: ResearchControls, host: str):
    from fastapi import FastAPI

    # Explicit Starlette routes share the same ASGI middleware and event transport.
    return FastAPI(docs_url=None, redoc_url=None, openapi_url=None, **surface(controls, host))
