"""Project-scoped research lines and material version downloads."""

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from bioquorum.application import materials, programs
from bioquorum.application.controls import ResearchControls
from bioquorum.server.program_record import record_routes
from bioquorum.server.request_body import Empty, read_body


class SessionTitle(BaseModel):
    title: str


class Name(BaseModel):
    name: str


class Attachment(BaseModel):
    session_id: str


class Registration(BaseModel):
    file_id: str
    kind: str
    source: str


class Correction(BaseModel):
    file_id: str
    source: str
    reason: str


class Withdrawal(BaseModel):
    reason: str


def program_routes(controls: ResearchControls) -> list[Route]:
    base = "/api/programs"
    program = base + "/{pid}"
    versions = program + "/materials"
    version = versions + "/{vid}"
    headers = {"Cache-Control": "no-store"}

    async def index(request: Request) -> JSONResponse:
        if request.method == "POST":
            body = await read_body(request, Name)
            row = await programs.create(controls, body.name)
            return JSONResponse(row, status_code=201, headers=headers)
        return JSONResponse({"programs": programs.list_programs(controls)}, headers=headers)

    async def detail(request: Request) -> JSONResponse:
        return JSONResponse(programs.show(controls, request.path_params["pid"]), headers=headers)

    async def rename(request: Request) -> JSONResponse:
        body = await read_body(request, Name)
        row = await programs.rename(controls, request.path_params["pid"], body.name)
        return JSONResponse(row, headers=headers)

    async def close(request: Request) -> JSONResponse:
        await read_body(request, Empty)
        row = await programs.close(controls, request.path_params["pid"])
        return JSONResponse(row, headers=headers)

    async def attach(request: Request) -> JSONResponse:
        body = await read_body(request, Attachment)
        row = await programs.attach(controls, request.path_params["pid"], body.session_id)
        return JSONResponse(row, headers=headers)

    async def new_session(request: Request) -> JSONResponse:
        body = await read_body(request, SessionTitle)
        sid = await programs.new_session(controls, request.path_params["pid"], body.title)
        return JSONResponse({"id": sid}, status_code=201, headers=headers)

    async def catalog(request: Request) -> JSONResponse:
        pid = request.path_params["pid"]
        if request.method == "POST":
            body = await read_body(request, Registration)
            row = await materials.register(controls, pid, body.file_id, body.kind, body.source)
            return JSONResponse(row, status_code=201, headers=headers)
        return JSONResponse({"versions": materials.catalog(controls, pid)}, headers=headers)

    async def supersede(request: Request) -> JSONResponse:
        body = await read_body(request, Correction)
        row = await materials.supersede(
            controls,
            request.path_params["pid"],
            request.path_params["vid"],
            body.file_id,
            body.source,
            body.reason,
        )
        return JSONResponse(row, status_code=201, headers=headers)

    async def withdraw(request: Request) -> JSONResponse:
        body = await read_body(request, Withdrawal)
        row = await materials.withdraw(
            controls, request.path_params["pid"], request.path_params["vid"], body.reason
        )
        return JSONResponse(row, headers=headers)

    async def download(request: Request) -> FileResponse:
        path, name = materials.download(
            controls, request.path_params["pid"], request.path_params["vid"]
        )
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=name,
            headers={**headers, "X-Content-Type-Options": "nosniff"},
        )

    return [
        *record_routes(controls),
        Route(base, index, methods=["GET", "POST"]),
        Route(program, detail),
        Route(program + "/rename", rename, methods=["POST"]),
        Route(program + "/close", close, methods=["POST"]),
        Route(program + "/sessions", attach, methods=["POST"]),
        Route(program + "/sessions/new", new_session, methods=["POST"]),
        Route(versions, catalog, methods=["GET", "POST"]),
        Route(version + "/supersede", supersede, methods=["POST"]),
        Route(version + "/withdraw", withdraw, methods=["POST"]),
        Route(version + "/file", download),
    ]
