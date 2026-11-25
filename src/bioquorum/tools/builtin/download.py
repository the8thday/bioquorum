"""``download_file``: fetch a public file into the project (ADR-017).

Kernel code has no network, so this tool is how external data (GEO, ENA, supplementary
tables, reference files) gets into an analysis. Unlike a ``requests.get`` in the kernel it
is permission-checked, limited to public projects, and recorded: the query-free locator goes into
``tool_call_requested``, the content hash and size into ``tool_call_finished``, and the
bytes into the content-addressed store, so a later rerun can prove it used the same file.

Guards, all before or while connecting:
  * http(s) only, no credentials in the URL (they would end up in the event log);
  * every hop, redirects included, must resolve to public addresses only (at the proxy when configured), so a prompt
    injection cannot use the tool to reach localhost or the intranet (``private or local``);
  * a size limit, enforced while streaming.
Not covered: DNS rebinding between our resolution and the connection (see SECURITY.md).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import httpx2
from pydantic import BaseModel, Field, field_validator

from bioquorum.core.enums import Classification, DataKind, PermissionClass, Trust
from bioquorum.core.hashing import sha256_file
from bioquorum.core.ids import new_id
from bioquorum.core.messages import DataRefPart, TextPart
from bioquorum.policy.secrets import public_url
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

DOWNLOAD_DIR = "downloads"
MAX_REDIRECTS = 5
_CHUNK = 1 << 20
_USER_AGENT = "BioQuorum (+https://github.com/the8thday/bioquorum)"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

Resolver = Callable[[str], Awaitable[list[str]]]


class DownloadArgs(BaseModel):
    url: str = Field(description="http(s) URL of a public file.")
    filename: str | None = Field(
        default=None, description="Name to save under downloads/ (default: from the URL)."
    )
    max_mb: int = Field(default=2048, ge=1, le=50_000, description="Abort above this size.")

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        try:
            url = httpx2.URL(v.strip())
        except Exception:
            raise ValueError("not a valid URL") from None
        if url.scheme not in ("http", "https") or not url.host:
            raise ValueError("only http:// and https:// URLs with a host are supported")
        if url.userinfo:
            raise ValueError("credentials in the URL are not allowed")
        return str(url)


def _host(args: DownloadArgs, ctx: ToolContext) -> str:
    return httpx2.URL(args.url).host.lower()


def safe_filename(name: str) -> str:
    """A plain file name: no directories, no leading dot, only safe characters."""
    base = PurePosixPath(unquote(name).replace("\\", "/")).name
    base = _UNSAFE.sub("_", base).strip("._")
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 119 - len(ext)] + dot + ext) if dot and len(ext) <= 10 else base[:120]
    return base or "download"


async def _system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class _RefusedError(Exception):
    pass


async def _check_host(host: str, resolve: Resolver) -> list[str]:
    try:
        addresses = [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        try:
            addresses = await resolve(host)
        except OSError as exc:
            raise _RefusedError(f"cannot resolve {host}: {exc}") from None
    if not addresses or not all(_is_public(a) for a in addresses):
        raise _RefusedError(f"{host} resolves to a private or local address; refused")
    return addresses


class _PinnedTransport(httpx2.AsyncHTTPTransport):
    """Connect directly to the pre-checked public IP while preserving Host and TLS SNI."""

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        pinned_ip = request.extensions.get("bq_pinned_ip")
        if not isinstance(pinned_ip, str) or not pinned_ip:
            return await super().handle_async_request(request)
        original_host = request.url.host
        ip_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        headers = httpx2.Headers(request.headers)
        headers["host"] = request.url.netloc.decode("ascii")
        extensions = {**request.extensions, "sni_hostname": original_host}
        pinned_request = httpx2.Request(
            method=request.method,
            url=request.url.copy_with(host=ip_host),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await super().handle_async_request(pinned_request)


def _destination(directory: Path, name: str, digest: str) -> tuple[Path, bool]:
    """Where to put the file, and whether an identical file is already there."""
    dest = directory / name
    if not dest.exists():
        return dest, False
    if dest.is_file() and sha256_file(dest) == digest:
        return dest, True
    stem, dot, ext = name.partition(".")
    alt = directory / f"{stem}-{digest[:8]}{dot}{ext}"
    return alt, alt.is_file() and sha256_file(alt) == digest


def download_tool(
    *, transport: httpx2.AsyncBaseTransport | None = None, resolve: Resolver | None = None
) -> ToolSpec:
    """Build the tool. Tests pass a mock ``transport`` and ``resolve``; nothing else differs."""
    resolver = resolve or _system_resolve

    async def handler(args: DownloadArgs, ctx: ToolContext) -> ToolResult:
        cas = ctx.cas
        if cas is None:
            return ToolResult.text("download_file needs a content store", is_error=True)
        directory = Path(ctx.workdir).resolve() / DOWNLOAD_DIR
        limit = args.max_mb * 1024 * 1024
        partial: Path | None = None
        url = httpx2.URL(args.url)
        try:
            client_transport = (
                transport
                if transport is not None
                else (_PinnedTransport() if ctx.outbound_proxy is None else None)
            )
            async with httpx2.AsyncClient(
                transport=client_transport,
                follow_redirects=False,
                proxy=ctx.outbound_proxy,
                trust_env=ctx.outbound_proxy is None,
                timeout=httpx2.Timeout(60.0, connect=30.0),
                headers={"user-agent": _USER_AGENT},
            ) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    if url.scheme not in ("http", "https"):
                        raise _RefusedError(f"redirect to unsupported scheme: {url.scheme}")
                    if url.userinfo:
                        raise _RefusedError("credentials in the URL are not allowed")
                    extensions: dict[str, str] = {}
                    if ctx.outbound_proxy:
                        if url.scheme != "https":
                            raise _RefusedError("The outbound proxy requires HTTPS")
                    else:
                        addresses = await _check_host(url.host, resolver)
                        extensions["bq_pinned_ip"] = addresses[0]
                    async with client.stream("GET", url, extensions=extensions) as resp:
                        if resp.is_redirect and resp.next_request is not None:
                            url = resp.next_request.url
                            continue
                        if resp.status_code >= 400:
                            raise _RefusedError(
                                f"HTTP {resp.status_code} from {public_url(str(url))}"
                            )
                        declared = resp.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > limit:
                            raise _RefusedError(
                                f"{declared} bytes is over the {args.max_mb} MB limit"
                            )
                        directory.mkdir(parents=True, exist_ok=True)
                        partial = directory / f".partial-{new_id()}"
                        size = 0
                        with partial.open("wb") as fh:
                            async for chunk in resp.aiter_bytes(_CHUNK):
                                size += len(chunk)
                                if size > limit:
                                    raise _RefusedError(
                                        f"download exceeded the {args.max_mb} MB limit"
                                    )
                                fh.write(chunk)
                        content_type = resp.headers.get("content-type", "unknown")
                        break
                else:
                    raise _RefusedError(f"more than {MAX_REDIRECTS} redirects")

            assert partial is not None
            ref = cas.put_file(partial, kind=DataKind.FILE)
            name = safe_filename(args.filename or url.path or "")
            dest, existed = _destination(directory, name, ref.content_hash)
            if existed:
                partial.unlink()
            else:
                os.replace(partial, dest)
            partial = None
        except _RefusedError as exc:
            return ToolResult.text(str(exc), is_error=True)
        except httpx2.HTTPError as exc:
            return ToolResult.text(f"download failed: {type(exc).__name__}", is_error=True)
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)

        rel = dest.relative_to(Path(ctx.workdir).resolve()).as_posix()
        source = public_url(str(url))
        if str(url) != args.url:
            source += f" (redirected from {public_url(args.url)})"
        text = (
            f"saved: {rel}\nsize: {ref.size} bytes\nsha256: {ref.content_hash}\n"
            f"content-type: {content_type}\nsource: {source}\n"
            f"Read it in python_execute from '{rel}' (the kernel cannot download)."
        )
        return ToolResult(
            content=[
                TextPart(text=text),
                DataRefPart(ref=ref, summary=f"{rel}, downloaded from {public_url(str(url))}"),
            ]
        )

    return ToolSpec(
        name="download_file",
        description=(
            "Download a public file over http(s) into the project's downloads/ folder, e.g. "
            "GEO or ENA data, supplementary tables, reference files. Returns the saved path, "
            "size and sha256; load the file with python_execute afterwards. Use this instead "
            "of requests/urllib in python_execute, which has no network."
        ),
        handler=handler,
        args_model=DownloadArgs,
        permission=PermissionClass.NETWORK,
        output_trust=Trust.UNTRUSTED,
        # The request (URL, host) leaves the machine: public projects only.
        classification_floor=Classification.PUBLIC,
        target=_host,
    )


DOWNLOAD_FILE = download_tool()
