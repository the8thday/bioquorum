"""Exact HTTPS allowlist and endpoint validation for the optional local egress proxy."""

from collections.abc import Iterable
from ipaddress import IPv4Address, IPv6Address, ip_address
from re import fullmatch
from urllib.parse import urlsplit


def _host(value: str) -> str:
    host = value.lower()
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or labels[-1].isdigit()
        or not all(fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    ):
        raise ValueError("Use an exact ASCII DNS host name")
    return host


class EgressPolicy:
    def __init__(self, hosts: Iterable[str]):
        self.hosts = frozenset(_host(host) for host in hosts)
        if not self.hosts:
            raise ValueError("Configure at least one allowed egress hostname")

    def authority(self, target: str) -> tuple[str, int]:
        name, sep, port = target.rpartition(":")
        if not sep or port != "443":
            raise ValueError("Only HTTPS CONNECT on port 443 is permitted")
        host = _host(name)
        if host not in self.hosts:
            raise ValueError("The destination host is outside the egress allowlist")
        return host, 443

    @staticmethod
    def addresses(values: Iterable[str]) -> list[IPv4Address | IPv6Address]:
        addresses = list(dict.fromkeys(ip_address(raw) for raw in values))
        for address in addresses:
            checked = (
                (address.ipv4_mapped or address) if isinstance(address, IPv6Address) else address
            )
            if not checked.is_global or checked.is_multicast:
                raise ValueError("The destination returned a non-public address")
        if not addresses:
            raise ValueError("The destination did not resolve")
        return addresses


def loopback_proxy_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("The outbound proxy must be an explicit loopback HTTP URL")
    candidate = urlsplit(value)
    if (
        candidate.scheme != "http"
        or candidate.hostname not in ("127.0.0.1", "::1")
        or not candidate.port
        or candidate.username is not None
        or candidate.password is not None
        or candidate.path not in ("", "/")
        or candidate.query
        or candidate.fragment
    ):
        raise ValueError("The outbound proxy must be an explicit loopback HTTP URL")
    return value
