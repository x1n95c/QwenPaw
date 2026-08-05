# -*- coding: utf-8 -*-
from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote, urlparse

_LOOPBACK_HOSTNAMES = {"localhost"}

#: Characters kept verbatim in the quoted ``filename=`` parameter. Every
#: other byte is replaced, which is what keeps a quote or a semicolon in a
#: user-chosen name from closing the parameter early.
_HEADER_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


def is_loopback_host(host: str) -> bool:
    """Return True when *host* is localhost or a loopback IP address."""
    normalized = host.strip().strip("[]").lower().rstrip(".")
    if normalized in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    """Return True when *url* targets a localhost or loopback address."""
    return is_loopback_host(urlparse(url).hostname or "")


def trust_env_for_url(url: str) -> bool:
    """Return whether httpx should trust proxy/cert env vars for *url*."""
    return not is_loopback_url(url)


def content_disposition_attachment(filename: str) -> str:
    """Build a ``Content-Disposition`` value for a download.

    Interpolating a name straight into ``filename="{name}"`` lets any
    name containing a double quote close the parameter and append its own
    — a name like ``evil"; filename="pwn.exe`` spoofs the saved filename.
    Names here come from user-created skills, templates and batch scripts,
    so they are attacker-influenced.

    Emits both forms per RFC 6266: an ASCII-sanitised ``filename`` that
    every client understands, and ``filename*`` carrying the real UTF-8
    name for clients that support it (which is what preserves a CJK name).
    """
    sanitized = _HEADER_SAFE_FILENAME.sub("_", filename)
    stem, dot, suffix = sanitized.rpartition(".")
    if not dot:
        stem, suffix = sanitized, ""
    # A fully non-ASCII name (a Chinese template, say) sanitises to all
    # underscores; falling back to the extension alone would offer the
    # user a file called ".zip", so name the stem instead. Clients that
    # honour `filename*` never see this.
    if not stem.strip("_"):
        stem = "download"
    safe_ascii = f"{stem}.{suffix}" if suffix else stem
    encoded = quote(filename, safe="")
    quoted = f'attachment; filename="{safe_ascii}"'
    return f"{quoted}; filename*=UTF-8''{encoded}"
