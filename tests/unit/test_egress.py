"""An explicit local proxy checks the complete DNS result before dialing."""

import asyncio
from ipaddress import ip_address

import pytest

from bioquorum.policy.egress import EgressPolicy, loopback_proxy_url
from bioquorum.server import egress


def test_exact_authority_and_public_addresses():
    policy = EgressPolicy(["api.example.com"])
    assert policy.authority("api.example.com:443") == ("api.example.com", 443)
    assert policy.addresses(["1.1.1.1"]) == [ip_address("1.1.1.1")]
    for target in (
        "other.api.example.com:443",
        "api.example.com:80",
        "127.0.0.1:443",
        "api.example.com@localhost:443",
    ):
        with pytest.raises(ValueError):
            policy.authority(target)
    for addresses in (["1.1.1.1", "127.0.0.1"], ["::ffff:127.0.0.1"], ["224.1.1.1"], []):
        with pytest.raises(ValueError):
            policy.addresses(addresses)


def test_only_explicit_loopback_proxy_config():
    assert loopback_proxy_url("http://127.0.0.1:8443") == "http://127.0.0.1:8443"
    for candidate in (
        "http://other.test:443",
        "http://user@127.0.0.1:8433",
        "http://127.0.0.1:8433/a",
    ):
        with pytest.raises(ValueError):
            loopback_proxy_url(candidate)


@pytest.mark.integration
@pytest.mark.anyio
async def test_proxy_checks_name_before_resolving_or_dialling(monkeypatch):
    calls = []

    async def dial(_policy, hostname, port):
        calls.append((hostname, port))
        raise ValueError("The destination returned a non-public address")

    monkeypatch.setattr(egress, "_dial", dial)
    policy = EgressPolicy(["api.example.com"])
    server = await asyncio.start_server(lambda r, w: egress._request(r, w, policy), "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        for hostname in ("denied.example.org", "api.example.com"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT {hostname}:443 HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            assert b"403" in await asyncio.wait_for(reader.read(), 2)
            writer.close()
            await writer.wait_closed()
    assert calls == [("api.example.com", 443)]


@pytest.mark.integration
@pytest.mark.anyio
async def test_proxy_relays_data_after_approved_dial(monkeypatch):
    async def echo(reader, writer):
        writer.write(await reader.readexactly(3))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)

    async def dial(_policy, host, port):
        assert (host, port) == ("api.example.com", 443)
        return await asyncio.open_connection("127.0.0.1", upstream.sockets[0].getsockname()[1])

    monkeypatch.setattr(egress, "_dial", dial)
    policy = EgressPolicy(["api.example.com"])
    server = await asyncio.start_server(lambda r, w: egress._request(r, w, policy), "127.0.0.1", 0)
    async with upstream, server:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        writer.write(b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        try:
            assert b"200" in await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            writer.write(b"abc")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(3), 2) == b"abc"
        finally:
            writer.close()
            await writer.wait_closed()
