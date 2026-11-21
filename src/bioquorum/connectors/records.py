"""Retrieve a fingerprinted JSON response from a fixed public source."""

import re
from html import unescape

import httpx2

from bioquorum.core.hashing import sha256_bytes


def clean(value: str) -> str:
    return " ".join(unescape(re.sub(r"</?[A-Za-z][^>]*>", " ", value)).split())


async def read_record(client: httpx2.AsyncClient, url: str, params: dict[str, str]) -> tuple:
    response = None
    for attempt in range(2):
        try:
            response = await client.get(url, params=params)
            break
        except httpx2.TransportError:
            if attempt == 1:
                raise
    assert response is not None
    response.raise_for_status()
    source = f"Source URL: {response.url}\nResponse SHA-256: {sha256_bytes(response.content)}"
    return response.json(), source
