"""URL validation and SSRF protection for user-supplied URLs."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}


class UnsafeURLError(ValueError):
    pass


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped and not _ip_is_public(str(addr.ipv4_mapped)))
    )


def validate_public_url(url: str, resolve: bool = True) -> str:
    """Raise UnsafeURLError unless `url` is an http(s) URL pointing at a public host.

    Blocks localhost, private ranges, link-local (cloud metadata), credentials in
    the URL and unusual ports. DNS is resolved so names pointing at internal
    addresses are rejected as well.
    """
    if not url or len(url) > 2048:
        raise UnsafeURLError("URL missing or too long")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("Credentials in URLs are not allowed")
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError("Local/internal hosts are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("Invalid port") from exc
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError(f"Port {port} is not allowed")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        if not _ip_is_public(host):
            raise UnsafeURLError("Private or reserved IP addresses are not allowed")
    elif resolve:
        try:
            infos = socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Cannot resolve host {host}") from exc
        for info in infos:
            if not _ip_is_public(info[4][0]):
                raise UnsafeURLError("Host resolves to a private or reserved address")
    return url.strip()


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in (x.lower().lstrip(".") for x in allowed_domains))
