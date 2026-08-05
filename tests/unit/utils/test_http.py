# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from qwenpaw.utils.http import (
    content_disposition_attachment,
    is_loopback_host,
    is_loopback_url,
    trust_env_for_url,
)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost.",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "[::1]",
    ],
)
def test_is_loopback_host_recognizes_loopback_targets(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        "0.0.0.0",
        "::",
        "192.168.1.10",
        "10.0.0.1",
        "example.com",
    ],
)
def test_is_loopback_host_keeps_non_loopback_targets(host: str) -> None:
    assert is_loopback_host(host) is False


# Local API calls should bypass env proxies for localhost/loopback targets.
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8088/api",
        "http://localhost.:8088/api",
        "http://127.0.0.1:8088/api",
        "http://127.1.2.3:8088/api",
        "http://[::1]:8088/api",
    ],
)
def test_is_loopback_url_recognizes_loopback_targets(url: str) -> None:
    assert is_loopback_url(url) is True
    assert trust_env_for_url(url) is False


# Non-loopback URLs keep httpx's normal env proxy behavior.
@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:8088/api",
        "https://example.com/api",
        "http://10.0.0.1:8088/api",
    ],
)
def test_is_loopback_url_keeps_non_loopback_targets(url: str) -> None:
    assert is_loopback_url(url) is False
    assert trust_env_for_url(url) is True


# ---------------------------------------------------------------------------
# content_disposition_attachment
#
# Download names here come from user-created skills, cron templates and
# batch scripts, so they are attacker-influenced: a name may contain the
# very quote that ends the header parameter.
# ---------------------------------------------------------------------------


def test_content_disposition_passes_through_a_plain_name() -> None:
    header = content_disposition_attachment("collect.zip")
    assert header.startswith('attachment; filename="collect.zip"')
    assert "filename*=UTF-8''collect.zip" in header


def test_content_disposition_neutralizes_a_quote_injection() -> None:
    """The classic payload: close `filename="` and start a second one."""
    header = content_disposition_attachment('evil"; filename="pwn.exe.zip')
    # Exactly one quoted filename parameter, and no stray quote or
    # semicolon survived inside it to start another.
    assert header.count('filename="') == 1
    quoted = header.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in quoted
    assert ";" not in quoted
    # One parameter for the quoted form plus one for filename*: an
    # injected third would mean the payload got through.
    assert header.count(";") == 2


def test_content_disposition_carries_a_non_ascii_name_in_filename_star() -> (
    None
):
    header = content_disposition_attachment("周报汇总.zip")
    # The real name survives percent-encoded for clients that read it...
    assert "filename*=UTF-8''%E5%91%A8" in header
    # ...while the ASCII fallback stays a usable file name rather than
    # collapsing to a bare ".zip".
    assert 'filename="download.zip"' in header


def test_content_disposition_falls_back_when_nothing_is_usable() -> None:
    assert 'filename="download"' in content_disposition_attachment('"')
    assert 'filename="download"' in content_disposition_attachment("")


def test_content_disposition_keeps_a_name_without_an_extension() -> None:
    assert 'filename="noext"' in content_disposition_attachment("noext")
