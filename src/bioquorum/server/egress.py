"""Loopback-only HTTPS CONNECT proxy for managed BioQuorum egress."""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress

from bioquorum.policy.egress import EgressPolicy

_REPLY = {
    200: b"Connection established",
    400: b"Bad request",
    403: b"Forbidden",
    502: b"Bad gateway",
    503: b"Unavailable",
}


async def _status(writer: asyncio.StreamWriter, code: int) -> None:
    writer.write(b"HTTP/1.1 " + str(code).encode("ascii") + b" " + _REPLY[code] + b"\r\n\r\n")
    await writer.drain()


async def _dial(policy: EgressPolicy, host: str, port: int):
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = policy.addresses(str(entry[4][0]) for entry in records)
    for address in addresses:
        try:
            async with asyncio.timeout(3):
                return await asyncio.open_connection(
                    str(address),
                    port,
                    family=socket.AF_INET if address.version == 4 else socket.AF_INET6,
                )
        except (OSError, TimeoutError):
            continue
    raise OSError("No permitted address could be reached")


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        async with asyncio.timeout(90):
            payload = await reader.read(65536)
            if not payload:
                break
            writer.write(payload)
            await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


async def _request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, policy: EgressPolicy
) -> None:
    upstream = None
    try:
        try:
            async with asyncio.timeout(5):
                header = await reader.readuntil(b"\r\n\r\n")
            method, authority, version = header.split(b"\r\n", 1)[0].decode("ascii").split()
            if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("Expected a CONNECT request")
        except (ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await _status(writer, 400)
            return
        try:
            host, port = policy.authority(authority)
            async with asyncio.timeout(12):
                source, upstream = await _dial(policy, host, port)
        except ValueError:
            await _status(writer, 403)
            return
        except (OSError, TimeoutError):
            await _status(writer, 502)
            return
        await _status(writer, 200)
        async with asyncio.TaskGroup() as relay:
            relay.create_task(_relay(reader, upstream))
            relay.create_task(_relay(source, writer))
    except* (OSError, TimeoutError):
        pass
    finally:
        if upstream is not None:
            upstream.close()
            with suppress(OSError):
                await upstream.wait_closed()
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


async def serve_egress(hosts: list[str], port: int) -> None:
    policy = EgressPolicy(hosts)
    capacity = asyncio.Semaphore(32)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if capacity.locked():
            with suppress(OSError):
                await _status(writer, 503)
            writer.close()
            return
        async with capacity:
            await _request(reader, writer, policy)

    server = await asyncio.start_server(handle, "127.0.0.1", port, limit=8192)
    async with server:
        print(
            f"BioQuorum egress · http://127.0.0.1:{server.sockets[0].getsockname()[1]}", flush=True
        )
        await server.serve_forever()
