"""Durable upper-bound reservations for each paid HTTP attempt, including SDK retries."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

import httpx2

from bioquorum.core.ids import new_id

MODEL = "gpt-6.1-sol"
DEVELOPMENT_MODEL = "gpt-6.2-sol"
MAX_OUTPUT = 4096
CONTEXT = 1_050_000
# Microdollars per token, conservatively including cache writes, long context and region.
INPUT_RATE = 6
OUTPUT_RATE = 17


class SpendingLimitError(RuntimeError):
    pass


class SpendingLog:
    def __init__(self, path: Path, limit: int):
        self.path = path
        self.limit = limit
        self._changed = asyncio.Condition()
        self._inflight = 0
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL)"
            )

    def reserve(self, amount: int) -> str:
        if amount <= 0:
            raise ValueError("Expected a positive reservation")
        token = new_id()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges").fetchone()[0]
            if total + amount > self.limit:
                raise SpendingLimitError(
                    "Insufficient shared evaluation budget for the next request"
                )
            db.execute("INSERT INTO charges VALUES (?,?)", (token, amount))
        return token

    @asynccontextmanager
    async def reservation(self, maximum: int):
        """Wait when another active request may still release enough of its hold."""
        async with self._changed:
            while True:
                try:
                    token = self.reserve(maximum)
                    break
                except SpendingLimitError:
                    if not self._inflight:
                        raise
                    await self._changed.wait()
            self._inflight += 1
        try:
            yield token
        finally:
            async with self._changed:
                self._inflight -= 1
                self._changed.notify_all()

    def settle(self, token: str, actual: int) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            if (
                db.execute(
                    "UPDATE charges SET amount=? WHERE id=? AND amount>=? AND ?>=0",
                    (actual, token, actual, actual),
                ).rowcount
                != 1
            ):
                raise ValueError("Invalid settlement")


class MeteredOpenAI(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        ledger: SpendingLog,
        transport: httpx2.AsyncBaseTransport | None = None,
        *,
        model: str = MODEL,
    ):
        if model not in (MODEL, DEVELOPMENT_MODEL):
            raise ValueError("Model has no approved evaluation price")
        self.model = model
        self.ledger = ledger
        self.transport = transport or httpx2.AsyncHTTPTransport(retries=0)
        self.exhausted = False
        self.reserved_usd = 0.0
        self.unsettled = 0
        self.preflight_error: str | None = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.preflight_error:
            raise ValueError("Input token counting is unavailable: " + self.preflight_error)
        body = json.loads(request.content)
        if (
            str(request.url) != "https://api.openai.com/v1/responses"
            or request.method != "POST"
            or body.get("model") != self.model
            or body.get("stream")
            or body.get("max_output_tokens") != MAX_OUTPUT
            or any(t.get("type") != "function" for t in body.get("tools", []))
        ):
            raise ValueError("Request is outside the priced text-only evaluation protocol")
        body["service_tier"] = "default"
        headers = request.headers.copy()
        headers.pop("content-length", None)
        wire = httpx2.Request(
            "POST", request.url, headers=headers, json=body, extensions=request.extensions
        )
        count_fields = {
            "model",
            "input",
            "instructions",
            "tools",
            "tool_choice",
            "text",
            "reasoning",
            "truncation",
            "parallel_tool_calls",
        }
        output_fields = {
            "max_output_tokens",
            "service_tier",
            "stream",
            "store",
            "include",
            "temperature",
            "top_p",
            "metadata",
        }
        if body.keys() - count_fields - output_fields:
            self.preflight_error = "unrecognized request fields"
            raise ValueError("Unrecognized token-counting inputs")
        # The free endpoint counts the same input and schemas before inference. Never
        # submit the paid request when counting fails. Previous ambiguous holds stay intact.
        count_request = httpx2.Request(
            "POST",
            "https://api.openai.com/v1/responses/input_tokens",
            headers=headers,
            json={k: v for k, v in body.items() if k in count_fields},
            extensions=request.extensions,
        )
        try:
            count_response = await self._count_response(count_request)
        except httpx2.TransportError as exc:
            self.preflight_error = type(exc).__name__
            raise
        if not count_response.is_success:
            self.preflight_error = f"HTTP {count_response.status_code}"
            raise ValueError("Token counting returned " + self.preflight_error)
        count = count_response.json().get("input_tokens")
        if type(count) is not int or not 0 < count <= CONTEXT:
            self.preflight_error = "invalid returned count"
            raise ValueError("Invalid preflight token count")
        maximum = count * INPUT_RATE + MAX_OUTPUT * OUTPUT_RATE
        try:
            async with self.ledger.reservation(maximum) as token:
                return await self._generate(wire, token, maximum, count)
        except SpendingLimitError:
            self.exhausted = True
            raise

    async def _count_response(self, request: httpx2.Request) -> httpx2.Response:
        # Only the free preflight is replayed. Buffer the body inside the retry boundary.
        for attempt in range(3):
            delay = 2**attempt
            try:
                response = await self.transport.handle_async_request(request)
                try:
                    await response.aread()
                finally:
                    await response.aclose()
            except (httpx2.TimeoutException, httpx2.ConnectError) as exc:
                if attempt == 2:
                    raise
                if isinstance(exc, httpx2.ConnectError):
                    delay = (5, 15)[attempt]
            else:
                if response.status_code != 429 or attempt == 2:
                    return response
                try:
                    delay = float(response.headers.get("retry-after", str(delay)))
                except ValueError:
                    return response
                if not 0 <= delay <= 60:
                    return response
            await asyncio.sleep(delay)
        raise AssertionError("Counting attempts exhausted without a final outcome")

    async def _generate(
        self, wire: httpx2.Request, token: str, maximum: int, count: int
    ) -> httpx2.Response:
        self.unsettled += 1
        self.reserved_usd += maximum / 1_000_000
        response = await self.transport.handle_async_request(wire)
        # Ambiguous failures and interrupted requests retain their full reservation.
        await response.aread()
        if response.is_success:
            usage = response.json().get("usage") or {}
            i, o = usage.get("input_tokens"), usage.get("output_tokens")
            if type(i) is int and type(o) is int and 0 < i <= count and 0 <= o <= MAX_OUTPUT:
                # Upper price also covers cache writes and geographic uplift.
                actual = (i * 3 + o * 11) if i <= 272_000 else (i * INPUT_RATE + o * OUTPUT_RATE)
                self.ledger.settle(token, actual)
                self.unsettled -= 1
                self.reserved_usd -= (maximum - actual) / 1_000_000
        return response

    async def aclose(self) -> None:
        await self.transport.aclose()
