"""Researcher-triggered recalculation of a corrected material."""

from pydantic import BaseModel, ConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from bioquorum.application import recompute
from bioquorum.application.controls import ResearchControls
from bioquorum.server.request_body import read_body


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacement_version_id: str


def recompute_routes(controls: ResearchControls) -> list[Route]:
    base = "/api/programs/{pid}/materials/{vid}"

    async def preview(request: Request):
        return JSONResponse(
            recompute.preview(controls, request.path_params["pid"], request.path_params["vid"]),
            headers={"Cache-Control": "no-store"},
        )

    async def start(request: Request):
        body = await read_body(request, Correction)
        operation = await recompute.start(
            controls,
            request.path_params["pid"],
            request.path_params["vid"],
            body.replacement_version_id,
        )
        return JSONResponse(operation, headers={"Cache-Control": "no-store"})

    return [Route(base + "/impact", preview), Route(base + "/recompute", start, methods=["POST"])]
