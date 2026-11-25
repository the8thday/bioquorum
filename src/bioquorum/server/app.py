"""Local HTTP and SSE surface for the shared research commands."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from bioquorum.application import (
    artifacts,
    context_panel,
    demo,
    extensions,
    onboarding,
    project_library,
    project_memory,
    record,
    settings,
    usage,
    verify,
    workbench_preferences,
    workspace,
)
from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification
from bioquorum.provenance.cas import IntegrityError
from bioquorum.runtime.sandbox import Isolation, IsolationUnavailable
from bioquorum.server.accounts import account_routes
from bioquorum.server.programs import program_routes
from bioquorum.server.recompute import recompute_routes
from bioquorum.server.request_body import Empty
from bioquorum.server.request_body import read_body as _body

CSP = (
    "default-src 'none'; script-src 'self'; worker-src 'self'; frame-src 'self'; style-src 'self' 'sha256-ITHmamcImsZ/Je1xrdtDLZVvRSpj1Zokb6uHXORB824=' 'sha256-NTwDUA6nIvRRpI2lIBtHJqkV0TlTj4HVmI6vI81fIfM='; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
WEB = files("bioquorum.server").joinpath("web")
ASSETS = {
    "viewer-licenses.md": "text/plain",
    "RDKit_minimal.wasm": "application/wasm",
    "index.html": "text/html",
    "app.css": "text/css",
    "app.js": "text/javascript",
    "logo.svg": "image/svg+xml",
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
                    and "/api/" in request.url.path
                )
                or headers.get("origin") not in (None, origin)
                or (request.method not in ("GET", "HEAD") and headers.get("origin") != origin)
            ):
                await JSONResponse({"detail": "Local workbench origin required"}, status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


class ReviewOptions(BaseModel):
    online_citations: bool = False


class Title(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ProjectName(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    classification: Classification
    instructions: str = Field(default="", max_length=6000)


@dataclass
class ManagedProject:
    app: ASGIApp
    controls: ResearchControls
    stack: AsyncExitStack
    retired: asyncio.Event
    requests: int = 0


class Question(BaseModel):
    attachments: list[str] = Field(default_factory=list, max_length=8)
    references: list[ev.TurnReference] = Field(default_factory=list, max_length=8)
    question: str = Field(min_length=1, max_length=12000)
    steps: list[str] | None = Field(default=None, max_length=12)
    worker_model: str | None = Field(default=None, min_length=1, max_length=256)
    reasoning_effort: ev.ReasoningEffort | None = None


class ResumeTurn(BaseModel):
    checked_calls: list[str] = Field(default_factory=list)


class PlanQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=12000)


class PlanDecision(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=600)


class PermissionDecision(BaseModel):
    grant: bool
    remember: bool = False


def _sid(request: Request) -> str:
    return request.path_params["sid"]


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if re.match(rb"\s*(?:<\?xml[^>]*>\s*)?(?:<!DOCTYPE svg[^>]*>\s*)?<svg(?:\s|>)", data):
        return "image/svg+xml"
    raise LookupError("Unsupported image format")


def surface(controls: ResearchControls, host: str, *, library: bool = True) -> dict[str, Any]:
    """Build routes, middleware and lifespan together for the ASGI entry point."""

    def kernel_notice() -> str:
        if controls.demo:
            return ""
        try:
            isolated = controls.ws.kernel_isolation()
        except (IsolationUnavailable, workspace.WorkspaceError) as exc:
            return f"Kernel unavailable. Python calculations cannot run in this project. {exc}"
        if isolated is Isolation.NONE:
            return "Python isolation is unavailable here. Code in this public project runs without a sandbox."
        if controls.ws.kernel_network():
            return "Network access is enabled in this public project's Python kernel."
        return ""

    async def asset(request: Request) -> Response:
        name = request.path_params.get("asset", "index.html")
        if (
            (
                name not in ASSETS
                and not re.fullmatch(r"(?:viewer|chem-worker)-[A-Za-z0-9_-]+\.js", name)
            )
            or (name == "index.html" and "asset" in request.path_params)
            or not WEB.joinpath(name).is_file()
        ):
            raise LookupError("Asset not found")
        return Response(
            WEB.joinpath(name).read_bytes(),
            media_type=ASSETS.get(name, "text/javascript"),
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'unsafe-eval'; connect-src 'none'"
                    if name.startswith("chem-worker-")
                    else CSP
                ),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-cache",
            },
        )

    async def project_extensions(request: Request) -> Response:
        if request.method == "POST":
            data = extensions.save(controls, await _body(request, extensions.Edit, limit=1_410_000))
        else:
            data = extensions.snapshot(controls)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def openai_models(request: Request) -> Response:
        from bioquorum.application.model_setup import provider
        from bioquorum.models.registry import openai_model_ids

        await _body(request, Empty)
        name = request.path_params["name"]
        config = controls.ws.models_config()
        if controls.demo or (name != "openai" and name not in config.providers):
            raise ValueError("OpenAI model discovery is unavailable for this connection")
        connection = provider(config, name)
        if (
            connection.kind != "openai"
            or controls.ws.classification not in connection.allowed_classifications
        ):
            raise ValueError("OpenAI model discovery is unavailable for this project")
        proxy = None if connection.is_local else controls.ws.outbound_proxy()
        return JSONResponse(
            await openai_model_ids(connection, proxy), headers={"Cache-Control": "no-store"}
        )

    async def plan_models(request: Request) -> Response:
        from bioquorum.application.model_setup import provider
        from bioquorum.models.chatgpt_plan import catalog

        await _body(request, Empty)
        connection = provider(controls.ws.models_config(), "chatgpt")
        if (
            controls.demo
            or controls.ws.classification not in connection.allowed_classifications
            or connection.kind != "chatgpt"
        ):
            raise ValueError("ChatGPT plan use is unavailable in this project")
        return JSONResponse(
            await catalog(controls.ws.outbound_proxy()), headers={"Cache-Control": "no-store"}
        )

    async def project_settings(request: Request) -> Response:
        if request.method == "POST":
            edit = await _body(request, settings.SettingsEdit)
            before = controls.ws.classification
            data = settings.save(controls, edit)
            if controls.ws.classification != before:
                await controls.reset_session_kernels()
        else:
            data = settings.snapshot(controls)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def reconnect(request: Request) -> Response:
        await _body(request, Empty)
        return JSONResponse(
            await settings.reconnect(controls, _sid(request), request.path_params["name"])
        )

    async def tasks(request: Request) -> Response:
        from bioquorum.application.tasks import task_list

        return JSONResponse(task_list(controls), headers={"Cache-Control": "no-store"})

    async def pending_items(request: Request) -> Response:
        from bioquorum.application.tasks import inbox

        return JSONResponse(inbox(controls), headers={"Cache-Control": "no-store"})

    async def sessions(request: Request) -> Response:
        if request.method == "POST":
            body = await _body(request, Title)
            title = body.title.strip()
            if not title:
                raise ValueError("Enter a title")
            return JSONResponse({"id": controls.new_session(title)}, status_code=201)
        agent = controls.ws.agent_options()
        return JSONResponse(
            {
                "project": controls.ws.name,
                "instructions": controls.ws.config.get("project", {}).get("instructions", ""),
                "plan_default": controls.ws.config.get("agent", {}).get("plan_default", False),
                "references": agent.references,
                "first_pass": agent.first_pass,
                "project_notes": agent.project_notes,
                "literature_subtasks": agent.literature_subtasks,
                "worker_models": controls.worker_models(),
                "default_worker": controls.default_worker(),
                "project_permissions": controls.project_permissions(),
                "onboarding": onboarding.overview(controls),
                "kernel_notice": kernel_notice(),
                "classification": controls.ws.classification.value,
                "sessions": controls.sessions(request.query_params.get("q", "")),
                "example": {"question": demo.QUESTION, "steps": demo.STEPS}
                if controls.demo
                else None,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def rename(request: Request) -> Response:
        body = await _body(request, Title)
        controls.rename(_sid(request), body.title)
        return Response(status_code=204)

    async def archive(request: Request) -> Response:
        controls.archive(_sid(request))
        await controls.session_kernels.release(_sid(request))
        return Response(status_code=204)

    async def read(request: Request) -> Response:
        return JSONResponse(
            record.snapshot(
                controls,
                _sid(request),
                turn_limit=int(request.query_params["turn_limit"])
                if "turn_limit" in request.query_params
                else None,
            ),
            headers={"Cache-Control": "no-store"},
        )

    async def context_info(request: Request) -> Response:
        if request.method == "POST":
            await context_panel.compact(controls, _sid(request))
        return JSONResponse(
            context_panel.view(controls, _sid(request), request.query_params.get("worker")),
            headers={"Cache-Control": "no-store"},
        )

    async def usage_report(request: Request) -> Response:
        return JSONResponse(
            usage.report(controls.ws, controls.store, _sid(request)),
            headers={"Cache-Control": "no-store"},
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
        turn = controls.submit(
            _sid(request),
            body.question,
            steps=body.steps,
            worker_model=body.worker_model,
            references=body.references,
            attachments=body.attachments,
            reasoning_effort=body.reasoning_effort,
        )
        return JSONResponse({"turn_id": turn.id}, status_code=202)

    async def draft_plan(request: Request) -> Response:
        body = await _body(request, PlanQuestion)
        return JSONResponse(await controls.draft_plan(_sid(request), body.question))

    async def plan(request: Request) -> Response:
        body = await _body(request, PlanDecision)
        await controls.decide_plan(
            _sid(request), request.path_params["turn_id"], approve=body.approve, reason=body.reason
        )
        return Response(status_code=204)

    async def permission(request: Request) -> Response:
        body = await _body(request, PermissionDecision)
        controls.decide_permission(
            _sid(request),
            request.path_params["request_id"],
            body.grant,
            remember=body.remember,
        )
        return Response(status_code=204)

    async def reset_permissions(request: Request) -> Response:
        await _body(request, Empty)
        controls.reset_session_permissions(_sid(request))
        return Response(status_code=204)

    async def resume(request: Request) -> Response:
        body = await _body(request, ResumeTurn)
        controls.resume(_sid(request), request.path_params["turn_id"], body.checked_calls)
        return JSONResponse({"turn_id": request.path_params["turn_id"]}, status_code=202)

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
        options = await _body(request, ReviewOptions)
        await controls.review(
            _sid(request),
            request.path_params["answer_id"],
            online_citations=options.online_citations,
        )
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

    async def products(request: Request) -> Response:
        sid = _sid(request)
        if request.method == "POST":
            if request.headers.get("content-type") != "application/octet-stream":
                raise ValueError("Upload requires a binary file body")
            query = request.query_params
            data = await artifacts.upload(
                controls,
                sid,
                query.get("name", ""),
                Classification(query.get("classification", controls.ws.classification.value)),
                request.stream(),
            )
            return JSONResponse(data, status_code=201)
        return JSONResponse(artifacts.catalog(controls, sid), headers={"Cache-Control": "no-store"})

    async def product_file(request: Request) -> Response:
        path, name = artifacts.download(controls, _sid(request), request.path_params["id"])
        return FileResponse(
            path,
            filename=name,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def product_preview(request: Request) -> Response:
        sid, ident = _sid(request), request.path_params["id"]
        if request.path_params["format"] == "chrom-sizes":
            return Response(
                artifacts.chromosome_extents(controls, sid, ident),
                media_type="text/plain",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
        path, _ = artifacts.download(controls, sid, ident)
        if request.path_params["format"] == "image":
            if path.stat().st_size > 20_000_000:
                raise ValueError("Figure exceeds the preview size limit")
            data = path.read_bytes()
            return Response(
                data,
                media_type=image_type(data),
                headers={
                    "Content-Security-Policy": "sandbox; default-src 'none'",
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if request.path_params["format"] == "html":
            if path.stat().st_size > 5_000_000:
                raise ValueError("HTML report exceeds the preview size limit")
            data = path.read_bytes()
            return Response(
                data,
                media_type="text/html; charset=utf-8",
                headers={
                    "Content-Security-Policy": (
                        "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
                    ),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if request.path_params["format"] != "text":
            raise LookupError("Unknown preview format")
        text = artifacts.read_report(path)
        return JSONResponse({"text": text}, headers={"Cache-Control": "no-store"})

    async def scientific_data(request: Request) -> Response:
        data = artifacts.scientific_data(controls, _sid(request), request.path_params["id"])
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def product_table(request: Request) -> Response:
        from starlette.concurrency import run_in_threadpool

        params = request.query_params
        data = await run_in_threadpool(
            artifacts.table,
            controls,
            _sid(request),
            request.path_params["id"],
            int(params.get("page", "1")),
            query=params.get("q", ""),
            sort=int(params["sort"]) if "sort" in params else None,
            descending=params.get("order") == "desc",
            stats=int(params["stats"]) if "stats" in params else None,
        )
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def export_notebook(request: Request) -> Response:
        from io import BytesIO
        from zipfile import ZIP_DEFLATED, ZipFile

        sid = _sid(request)
        python = artifacts.notebook(controls, sid)
        r = artifacts.notebook(controls, sid, "r")
        if python["cells"] and r["cells"]:
            output = BytesIO()
            with ZipFile(output, "w", ZIP_DEFLATED) as archive:
                for name, data in (("python", python), ("r", r)):
                    archive.writestr(f"research-{name}.ipynb", json.dumps(data, ensure_ascii=False))
            return Response(
                output.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": 'attachment; filename="research-notebooks.zip"',
                    "Cache-Control": "no-store",
                },
            )
        data = r if r["cells"] else python
        return JSONResponse(
            data,
            headers={
                "Content-Disposition": 'attachment; filename="research.ipynb"',
                "Cache-Control": "no-store",
            },
        )

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
        controls.check_session(sid)
        try:
            cursor = int(
                request.headers.get("last-event-id", request.query_params.get("after", "-1"))
            )
        except ValueError:
            raise ValueError("Invalid event cursor") from None
        if not -1 <= cursor <= controls.store.last_seq(sid):
            raise ValueError("Invalid event cursor")

        async def stream():
            nonlocal cursor
            yield "retry: 1500\n\n"
            progress = None
            idle = 0
            while not await request.is_disconnected():
                retired = request.scope.get("project_retired")
                if retired is not None and retired.is_set():
                    return
                changes = controls.store.read(sid, from_seq=cursor + 1, limit=256)
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
                await asyncio.sleep(0 if len(changes) == 256 else 0.25)

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

    children = AsyncExitStack()
    example_lock = asyncio.Lock()
    example_app = None

    async def offline_example(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal example_app
        async with example_lock:
            if example_app is None:
                child = create_app(
                    demo.make_demo(controls.ws.state_dir / "examples" / "metformin"),
                    host,
                    library=False,
                )
                await children.enter_async_context(child.router.lifespan_context(child))
                example_app = child
        await example_app(scope, receive, send)

    managed: dict[str, ManagedProject] = {}

    async def preferences(request: Request) -> Response:
        if request.method == "POST":
            value = await _body(request, workbench_preferences.Preferences)
            workbench_preferences.save(value)
        else:
            value = workbench_preferences.read()
        return JSONResponse(value.model_dump(), headers={"Cache-Control": "no-store"})

    async def projects(request: Request) -> Response:
        if request.method == "POST":
            body = await _body(request, ProjectName)
            return JSONResponse(
                project_library.create(body.name, body.classification, body.instructions),
                status_code=201,
            )
        return JSONResponse(
            project_library.entries(controls.ws), headers={"Cache-Control": "no-store"}
        )

    async def trash(request: Request) -> Response:
        return JSONResponse(
            project_library.entries(controls.ws, trashed=True),
            headers={"Cache-Control": "no-store"},
        )

    async def move_project(request: Request) -> Response:
        await _body(request, Empty)
        key = request.path_params["key"]
        restore = request.url.path.endswith("/restore")
        async with example_lock:
            if not restore and (entry := managed.get(key)):
                entry.controls.ensure_idle()
                if entry.requests:
                    raise ConflictError("A project request is in progress. Try again shortly.")
                entry.retired.set()
                await entry.stack.aclose()
                entry.controls.store.db.engine.dispose()
                del managed[key]
            project_library.move(key, restore=restore, current=controls.ws)
        return Response(status_code=204)

    async def open_project(scope: Scope, receive: Receive, send: Send) -> None:
        key = scope["path_params"]["key"]
        async with example_lock:
            if key not in managed:
                root = project_library.project_path(key)
                if root.resolve() == controls.ws.root:
                    raise ValueError("This project is already open at /")
                child_controls = ResearchControls(workspace.load(root))
                child = create_app(child_controls, host, library=False)
                stack = AsyncExitStack()
                try:
                    await stack.enter_async_context(child.router.lifespan_context(child))
                except workspace.WorkspaceError as exc:
                    raise ValueError(str(exc)) from None
                await children.enter_async_context(stack)
                managed[key] = ManagedProject(child, child_controls, stack, asyncio.Event())
            entry = managed[key]
            scope["project_retired"] = entry.retired
            stream = scope["method"] == "GET" and scope["path"].endswith("/events")
            if not stream:
                entry.requests += 1
        try:
            await entry.app(scope, receive, send)
        finally:
            if not stream:
                entry.requests -= 1

    @asynccontextmanager
    async def lifespan(app):
        nonlocal example_app
        with workspace.project_lock(controls.ws.root):
            await controls.recover_literature()
            await controls.recover()
            await controls.recover_verification()
            try:
                yield
            finally:
                await children.aclose()
                example_app = None
                managed.clear()
                await controls.close()

    base = "/api/sessions"
    session = base + "/{sid}"
    return {
        "middleware": [Middleware(LocalOrigin, host=host)],
        "exception_handlers": {ValueError: error, LookupError: error},
        "lifespan": lifespan,
        "routes": [
            Route("/", asset),
            *(
                [
                    *account_routes(host, proxy=controls.ws.outbound_proxy()),
                    Route("/api/workbench-settings", preferences, methods=["GET", "POST"]),
                    Route("/api/projects", projects, methods=["GET", "POST"]),
                    Route("/api/projects/trash", trash),
                    Route("/api/projects/{key}/trash", move_project, methods=["POST"]),
                    Route("/api/projects/{key}/restore", move_project, methods=["POST"]),
                    Mount("/projects/{key}", app=open_project),
                ]
                if library
                else []
            ),
            *([] if controls.demo else [Mount("/example", app=offline_example)]),
            Route("/assets/{asset}", asset),
            *program_routes(controls),
            *recompute_routes(controls),
            Route("/api/extensions", project_extensions, methods=["GET", "POST"]),
            Route("/api/settings/openai-models/{name}", openai_models, methods=["POST"]),
            Route("/api/settings/chatgpt-models", plan_models, methods=["POST"]),
            Route("/api/settings", project_settings, methods=["GET", "POST"]),
            Route(session + "/connectors/{name}/reconnect", reconnect, methods=["POST"]),
            Route("/api/tasks", tasks),
            Route("/api/inbox", pending_items),
            Route(base, sessions, methods=["GET", "POST"]),
            Route(session, read),
            Route(session + "/title", rename, methods=["POST"]),
            Route(session + "/archive", archive, methods=["POST"]),
            Route(session + "/context", context_info, methods=["GET", "POST"]),
            Route(session + "/usage", usage_report),
            Route(session + "/audit", audit_history),
            Route(session + "/events", events),
            Route(session + "/plan-draft", draft_plan, methods=["POST"]),
            Route(session + "/turns", ask, methods=["POST"]),
            Route(session + "/turns/{turn_id}/resume", resume, methods=["POST"]),
            Route(session + "/turns/{turn_id}/plan", plan, methods=["POST"]),
            Route(session + "/permissions/reset", reset_permissions, methods=["POST"]),
            Route(session + "/permissions/{request_id}", permission, methods=["POST"]),
            Route(session + "/stop", stop, methods=["POST"]),
            Route(session + "/fork", fork, methods=["POST"]),
            Route(session + "/answers/{answer_id}/review", audit, methods=["POST"]),
            Route(session + "/answers/{answer_id}/{kind}/{reference}", evidence),
            Route(session + "/figures/{digest}", image),
            Route(session + "/claims/{claim_id}/rerun", rerun, methods=["POST"]),
            Route(session + "/claims/{claim_id}/replication", replication, methods=["GET", "POST"]),
            Route(session + "/artifacts", products, methods=["GET", "POST"]),
            Route(session + "/artifacts/{id}/file", product_file),
            Route(session + "/artifacts/{id}/preview/{format}", product_preview),
            Route(session + "/artifacts/{id}/annotations", scientific_data),
            Route(session + "/artifacts/{id}/table", product_table),
            Route(session + "/notebook", export_notebook),
            Route(session + "/bundles", bundles, methods=["POST"]),
            Route(session + "/project-note", project_note, methods=["GET", "POST"]),
            Route(session + "/bundles/{bundle_id}", download_bundle),
        ],
    }


def create_app(controls: ResearchControls, host: str, *, library: bool = True):
    from fastapi import FastAPI

    # Explicit Starlette routes share the same ASGI middleware and event transport.
    return FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, **surface(controls, host, library=library)
    )
