"""Workbench routes for the program evidence graph, decisions and handoffs."""

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from bioquorum.application import program_lineage, program_record
from bioquorum.application.controls import ResearchControls
from bioquorum.server.request_body import Empty, read_body


def record_routes(controls: ResearchControls) -> list[Route]:
    headers = {"Cache-Control": "no-store"}
    base = "/api/programs/{pid}"

    async def dependencies(req: Request):
        p = req.path_params
        return JSONResponse(
            program_lineage.downstream(controls, p["pid"], p["vid"]), headers=headers
        )

    async def upstream(req: Request):
        p = req.path_params
        rows = (
            program_lineage.upstream(controls, p["pid"], p["cid"])
            if "pid" in p
            else program_lineage.session_materials(controls, p["sid"], p["cid"])
        )
        return JSONResponse({"materials": rows}, headers=headers)

    async def handoff(req: Request):
        reply_headers = headers
        if req.url.path.endswith("/file"):
            reply_headers = {
                **headers,
                "Content-Disposition": 'attachment; filename="handoff.json"',
            }
        return JSONResponse(
            program_record.handoff(controls, req.path_params["pid"]), headers=reply_headers
        )

    async def decisions(req: Request):
        p = req.path_params
        if req.method == "GET":
            return JSONResponse(
                {"decisions": program_record.handoff(controls, p["pid"])["decisions"]},
                headers=headers,
            )
        args = await read_body(req, program_record.DecisionEdit)
        row = await program_record.save_decision(
            controls,
            p["pid"],
            args.text,
            args.claim_ids,
            p.get("did"),
            args.prior_revision,
            args.claim_revisions,
        )
        return JSONResponse(row, headers=headers, status_code=201)

    async def source(req: Request):
        p = req.path_params
        return JSONResponse(program_record.record(controls, p["pid"], p["eid"]), headers=headers)

    async def export(req: Request):
        await read_body(req, Empty)
        path = await program_record.export(controls, req.path_params["pid"])
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
            headers={**headers, "X-Content-Type-Options": "nosniff"},
        )

    return [
        Route(base + "/materials/{vid}/dependencies", dependencies),
        Route(base + "/claims/{cid}/materials", upstream),
        Route("/api/sessions/{sid}/claims/{cid}/materials", upstream),
        Route(base + "/handoff", handoff),
        Route(base + "/handoff/file", handoff),
        Route(base + "/decisions", decisions, methods=["GET", "POST"]),
        Route(base + "/decisions/{did}/revise", decisions, methods=["POST"]),
        Route(base + "/records/{eid}", source),
        Route(base + "/bundle", export, methods=["POST"]),
    ]
