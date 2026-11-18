"""URL validation helpers with basic SSRF protection.

Two layers:
1. ``normalize_url`` - syntax only (scheme, hostname, credentials).
2. ``validate_url`` - syntax + DNS resolution check that refuses to talk to
   loopback, private, link-local or otherwise non-public addresses.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
}


class InvalidURLError(ValueError):
    """Raised when a URL is malformed or points to a non-public host."""


def normalize_url(raw: str) -> str:
    """Clean up a user supplied URL and return its normalized string form."""
    if not raw or not raw.strip():
        raise InvalidURLError("URL must not be empty.")

    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlparse(candidate)
    except ValueError as exc:  # pragma: no cover - urlparse rarely raises
        raise InvalidURLError("URL could not be parsed.") from exc

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURLError("URL must use http or https.")

    hostname = parsed.hostname
    if not hostname:
        raise InvalidURLError("URL must include a valid hostname.")
    if parsed.username or parsed.password:
        raise InvalidURLError("URL must not contain credentials.")

    try:
        _ = parsed.port
    except ValueError as exc:
        raise InvalidURLError("URL contains an invalid port.") from exc

    # Fragments are never sent to the server, drop them for cleaner output.
    cleaned = urlunparse(parsed._replace(fragment="", scheme=parsed.scheme.lower()))
    return cleaned


def ensure_public_hostname(hostname: str) -> None:
    """Reject hostnames that resolve to non-public (internal) IP addresses."""
    host = hostname.strip().strip("[]").lower()

    if host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise InvalidURLError("URL host is not publicly accessible.")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise InvalidURLError(f"URL host could not be resolved: {host}") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise InvalidURLError(f"URL host could not be resolved: {host}")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%")[0])
        except ValueError:
            raise InvalidURLError("URL host resolved to an invalid address.") from None
        if not ip.is_global:
            raise InvalidURLError(
                "URL host resolves to a private, loopback or reserved address."
            )


def validate_url(raw: str) -> str:
    """Normalize a URL and verify its host points to the public internet."""
    normalized = normalize_url(raw)
    hostname = urlparse(normalized).hostname
    if not hostname:  # pragma: no cover - normalize_url already guarantees this
        raise InvalidURLError("URL must include a valid hostname.")
    ensure_public_hostname(hostname)
    return normalized
