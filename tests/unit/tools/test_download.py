"""download_file: the only way kernel code gets external data (ADR-017). No real network."""

import httpx2
import pytest

from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.messages import DataRefPart
from bioquorum.policy import PermissionPolicy
from bioquorum.tools import invoke
from bioquorum.tools.builtin.download import download_tool, safe_filename

pytestmark = pytest.mark.anyio

BODY = b"gene\tcount\nTP53\t12\n"
PUBLIC_IP = "93.184.215.14"


async def _chunks():
    for _ in range(5):
        yield b"x" * 600_000


def server(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    if path == "/counts.tsv":
        return httpx2.Response(
            200, content=BODY, headers={"content-type": "text/tab-separated-values"}
        )
    if path == "/moved":
        return httpx2.Response(302, headers={"location": "https://cdn.example.org/counts.tsv"})
    if path == "/loop":
        return httpx2.Response(302, headers={"location": "/loop"})
    if path == "/to-internal":
        return httpx2.Response(302, headers={"location": "http://intranet.local/secret"})
    if path == "/big":  # declares its size: refused before anything is written
        return httpx2.Response(200, content=b"x" * 3_000_000)
    if path == "/big-stream":  # no Content-Length: must be cut off while streaming
        return httpx2.Response(200, content=_chunks())
    return httpx2.Response(404, content=b"not found")


async def resolver(host: str) -> list[str]:
    return {"intranet.local": ["10.0.0.5"], "localhost": ["127.0.0.1"]}.get(host, [PUBLIC_IP])


def leftovers(tmp_path) -> list[str]:
    d = tmp_path / "downloads"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


@pytest.fixture
def tool():
    return download_tool(transport=httpx2.MockTransport(server), resolve=resolver)


@pytest.fixture
def public_ctx(ctx):
    ctx.classification = Classification.PUBLIC
    return ctx


async def test_downloads_into_project_and_cas(tool, public_ctx, tmp_path):
    part = await invoke(tool, {"url": "https://data.example.org/counts.tsv"}, public_ctx)
    assert not part.is_error, part.text()
    saved = tmp_path / "downloads" / "counts.tsv"
    assert saved.read_bytes() == BODY
    digest = sha256_bytes(BODY)
    assert f"sha256: {digest}" in part.text() and "downloads/counts.tsv" in part.text()
    (ref,) = [p for p in part.content if isinstance(p, DataRefPart)]
    assert ref.ref.content_hash == digest and ref.ref.size == len(BODY)
    assert public_ctx.cas.get(ref.ref) == BODY
    assert "https://data.example.org/counts.tsv" in ref.summary
    assert not list((tmp_path / "downloads").glob(".partial-*"))


async def test_recorded_in_events_for_provenance(tool, public_ctx, store):
    await invoke(tool, {"url": "https://data.example.org/counts.tsv"}, public_ctx)
    finished = [e for e in store.read("S") if e.type == "tool_call_finished"][-1]
    refs = [c for c in finished.payload["result"]["content"] if c["type"] == "data_ref"]
    assert refs[0]["ref"]["content_hash"] == sha256_bytes(BODY)
    requested = [e for e in store.read("S") if e.type == "tool_call_requested"][-1]
    assert requested.payload["args"]["url"] == "https://data.example.org/counts.tsv"


async def test_permission_is_network_on_the_host(tool, public_ctx):
    public_ctx.permissions = PermissionPolicy()
    public_ctx.permissions.grant(
        PermissionClass.NETWORK, "data.example.org", PermissionScope.GLOBAL
    )
    ok = await invoke(tool, {"url": "https://data.example.org/counts.tsv"}, public_ctx)
    assert not ok.is_error
    other = await invoke(tool, {"url": "https://other.example.org/counts.tsv"}, public_ctx)
    assert other.is_error and "denied" in other.text()


async def test_only_public_projects(tool, ctx):
    for level in (Classification.INTERNAL, Classification.CONFIDENTIAL):
        ctx.classification = level
        part = await invoke(tool, {"url": "https://data.example.org/counts.tsv"}, ctx)
        assert part.is_error and "classification" in part.text()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://ftp.ncbi.nlm.nih.gov/x",
        "https://user:pw@data.example.org/counts.tsv",
        "not a url",
    ],
)
async def test_rejects_bad_urls(tool, public_ctx, url, tmp_path):
    part = await invoke(tool, {"url": url}, public_ctx)
    assert part.is_error
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8888/api", "http://127.0.0.1/x", "http://10.1.2.3/x", "http://[::1]/x"],
)
async def test_refuses_local_and_private_addresses(tool, public_ctx, url):
    part = await invoke(tool, {"url": url}, public_ctx)
    assert part.is_error and "private or local" in part.text()


async def test_follows_redirects_and_reports_final_url(tool, public_ctx):
    part = await invoke(tool, {"url": "https://data.example.org/moved"}, public_ctx)
    assert not part.is_error, part.text()
    assert "https://cdn.example.org/counts.tsv" in part.text()


async def test_redirect_into_private_network_is_refused(tool, public_ctx, tmp_path):
    part = await invoke(tool, {"url": "https://data.example.org/to-internal"}, public_ctx)
    assert part.is_error and "private or local" in part.text()


async def test_redirect_loop_stops(tool, public_ctx):
    part = await invoke(tool, {"url": "https://data.example.org/loop"}, public_ctx)
    assert part.is_error and "redirect" in part.text()


async def test_http_error_writes_nothing(tool, public_ctx, tmp_path):
    part = await invoke(tool, {"url": "https://data.example.org/missing.csv"}, public_ctx)
    assert part.is_error and "404" in part.text()
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("path", ["/big", "/big-stream"])
async def test_size_limit(tool, public_ctx, tmp_path, path):
    part = await invoke(tool, {"url": f"https://data.example.org{path}", "max_mb": 1}, public_ctx)
    assert part.is_error and "limit" in part.text()
    assert leftovers(tmp_path) == []


async def test_same_name_different_content_gets_its_own_file(tool, public_ctx, tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    (d / "counts.tsv").write_bytes(b"older version")
    part = await invoke(tool, {"url": "https://data.example.org/counts.tsv"}, public_ctx)
    assert not part.is_error
    assert (d / "counts.tsv").read_bytes() == b"older version"
    (new,) = [p for p in d.iterdir() if p.name != "counts.tsv"]
    assert new.name.startswith("counts-") and new.suffix == ".tsv" and new.read_bytes() == BODY


async def test_same_content_is_reused(tool, public_ctx, tmp_path):
    for _ in range(2):
        part = await invoke(tool, {"url": "https://data.example.org/counts.tsv"}, public_ctx)
        assert not part.is_error
    assert [p.name for p in (tmp_path / "downloads").iterdir()] == ["counts.tsv"]


async def test_explicit_filename_cannot_escape(tool, public_ctx, tmp_path):
    part = await invoke(
        tool,
        {"url": "https://data.example.org/counts.tsv", "filename": "../../evil.sh"},
        public_ctx,
    )
    assert not part.is_error
    assert (tmp_path / "downloads" / "evil.sh").exists()
    assert not (tmp_path.parent / "evil.sh").exists()


def test_safe_filename():
    assert safe_filename("GSE123_counts.txt.gz") == "GSE123_counts.txt.gz"
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename(".bashrc") == "bashrc"
    assert safe_filename("a b;rm -rf.csv") == "a_b_rm_-rf.csv"
    assert safe_filename("") == "download"
    assert safe_filename("/") == "download"
    assert len(safe_filename("x" * 500 + ".csv")) <= 120
