"""Account routes owned by the loopback workbench, independently of project navigation."""

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from bioquorum.models import chatgpt_plan
from bioquorum.server.request_body import Empty, read_body

PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def account_routes(host: str) -> list[Route]:
    login = chatgpt_plan.SignIn(host)

    async def account(request: Request):
        return JSONResponse(chatgpt_plan.status(), headers=PRIVATE)

    async def start(request: Request):
        await read_body(request, Empty)
        return JSONResponse(await login.start(), headers=PRIVATE)

    async def signout(request: Request):
        await read_body(request, Empty)
        login.flow = None
        return JSONResponse(await chatgpt_plan.disconnect(), headers=PRIVATE)

    async def callback(request: Request):
        try:
            granted = await login.finish(dict(request.query_params))
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400, headers=PRIVATE)
        return RedirectResponse(
            "/auth/" + ("connected" if granted else "consent"), status_code=303, headers=PRIVATE
        )

    async def result(request: Request):
        text = (
            "ChatGPT plan connected. Return to BioQuorum to select your model."
            if request.url.path.endswith("connected")
            else "Signed in. Authorize plan usage in Models & accounts to use your plan."
        )
        return PlainTextResponse(text, headers=PRIVATE)

    return [
        Route("/api/account/chatgpt", account),
        Route("/api/account/chatgpt/start", start, methods=["POST"]),
        Route("/api/account/chatgpt/disconnect", signout, methods=["POST"]),
        Route("/auth/callback", callback),
        Route("/auth/connected", result),
        Route("/auth/consent", result),
    ]
