# -*- coding: utf-8 -*-
"""CLI tests for ``qwenpaw cron template``.

The CLI is a thin HTTP client, so these tests stub the transport and assert
on the request it builds plus how it renders the server's answer — the
server behaviour itself is covered by tests/unit/app/cron_templates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from qwenpaw.cli import cron_cmd
from qwenpaw.cli.cron_cmd import _template_error, cron_group


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = json.dumps(self._payload, ensure_ascii=False)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


class FakeClient:
    """Records calls and replays queued responses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def _record(self, method: str, path: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.responses.pop(0)

    def get(self, path: str, **kwargs: Any) -> FakeResponse:
        return self._record("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> FakeResponse:
        return self._record("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> FakeResponse:
        return self._record("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> FakeResponse:
        return self._record("DELETE", path, **kwargs)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    holder: dict[str, FakeClient] = {}

    def install(*responses: FakeResponse) -> FakeClient:
        client = FakeClient(list(responses))
        holder["client"] = client
        monkeypatch.setattr(cron_cmd, "client", lambda _base_url: client)
        return client

    return install


BASE = ["--base-url", "http://127.0.0.1:9999"]


# ---------------------------------------------------------------------------
# Help / wiring
# ---------------------------------------------------------------------------


def test_template_group_is_registered():
    result = CliRunner().invoke(cron_group, ["template", "--help"])
    assert result.exit_code == 0
    for command in (
        "list",
        "get",
        "export",
        "import",
        "update",
        "delete",
        "fork",
    ):
        assert command in result.output


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def test_list_sends_agent_header_and_builtin_flag(fake_client):
    client = fake_client(FakeResponse(payload=[]))
    result = CliRunner().invoke(
        cron_group,
        ["template", "list", "--agent-id", "a1", *BASE],
    )
    assert result.exit_code == 0
    call = client.calls[0]
    assert call["path"] == "/cron-templates"
    assert call["headers"] == {"X-Agent-Id": "a1"}
    assert call["params"] == {"include_builtin": "true"}


def test_list_no_builtin_flag(fake_client):
    client = fake_client(FakeResponse(payload=[]))
    CliRunner().invoke(cron_group, ["template", "list", "--no-builtin", *BASE])
    assert client.calls[0]["params"] == {"include_builtin": "false"}


def test_get_missing_reports_not_found(fake_client):
    fake_client(FakeResponse(status_code=404, payload={"detail": "nope"}))
    result = CliRunner().invoke(cron_group, ["template", "get", "x", *BASE])
    assert result.exit_code != 0
    assert "Template not found" in result.output


def test_export_writes_file(fake_client, tmp_path: Path):
    fake_client(FakeResponse(content=b"PK\x03\x04zipbytes"))
    target = tmp_path / "out" / "tpl.zip"
    result = CliRunner().invoke(
        cron_group,
        ["template", "export", "tpl", "-o", str(target), *BASE],
    )
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == b"PK\x03\x04zipbytes"
    assert "Exported tpl" in result.output


def test_export_refuses_to_clobber_without_force(fake_client, tmp_path: Path):
    fake_client(FakeResponse(content=b"zip"))
    target = tmp_path / "tpl.zip"
    target.write_bytes(b"existing")
    result = CliRunner().invoke(
        cron_group,
        ["template", "export", "tpl", "-o", str(target), *BASE],
    )
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert target.read_bytes() == b"existing"


def test_export_force_overwrites(fake_client, tmp_path: Path):
    fake_client(FakeResponse(content=b"new"))
    target = tmp_path / "tpl.zip"
    target.write_bytes(b"existing")
    result = CliRunner().invoke(
        cron_group,
        ["template", "export", "tpl", "-o", str(target), "--force", *BASE],
    )
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == b"new"


def test_import_posts_multipart_with_params(fake_client, tmp_path: Path):
    client = fake_client(
        FakeResponse(payload={"imported": ["a"], "count": 1, "conflicts": []}),
    )
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(b"zip")
    result = CliRunner().invoke(
        cron_group,
        [
            "template",
            "import",
            str(zip_path),
            "--name",
            "renamed",
            "--overwrite",
            *BASE,
        ],
    )
    assert result.exit_code == 0, result.output
    call = client.calls[0]
    assert call["path"] == "/cron-templates/upload"
    assert call["params"]["target_name"] == "renamed"
    assert call["params"]["overwrite"] == "true"
    assert "file" in call["files"]


def test_import_builds_rename_map(fake_client, tmp_path: Path):
    client = fake_client(
        FakeResponse(payload={"imported": [], "count": 0, "conflicts": []}),
    )
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(b"zip")
    CliRunner().invoke(
        cron_group,
        [
            "template",
            "import",
            str(zip_path),
            "--rename-to",
            "old=new",
            "--rename-to",
            "x=y",
            *BASE,
        ],
    )
    assert json.loads(client.calls[0]["params"]["rename_map"]) == {
        "old": "new",
        "x": "y",
    }


@pytest.mark.parametrize("pair", ["oldnew", "=new", "old="])
def test_import_rejects_malformed_rename_pair(
    fake_client,
    tmp_path: Path,
    pair: str,
):
    fake_client(FakeResponse(payload={}))
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(b"zip")
    result = CliRunner().invoke(
        cron_group,
        ["template", "import", str(zip_path), "--rename-to", pair, *BASE],
    )
    assert result.exit_code != 0
    assert "OLD=NEW" in result.output


def test_import_conflict_suggests_rename_flag(fake_client, tmp_path: Path):
    fake_client(
        FakeResponse(
            status_code=409,
            payload={
                "detail": {
                    "conflicts": [
                        {"name": "tpl", "suggested_name": "tpl-2"},
                    ],
                },
            },
        ),
    )
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(b"zip")
    result = CliRunner().invoke(
        cron_group,
        ["template", "import", str(zip_path), *BASE],
    )
    assert result.exit_code != 0
    assert "--rename-to tpl-2" in result.output


def test_update_sends_only_given_fields(fake_client):
    client = fake_client(FakeResponse(payload={"name": "tpl"}))
    result = CliRunner().invoke(
        cron_group,
        [
            "template",
            "update",
            "tpl",
            "--title",
            "新标题",
            "--tag",
            "team",
            "--tag",
            "reminder",
            *BASE,
        ],
    )
    assert result.exit_code == 0, result.output
    call = client.calls[0]
    assert call["method"] == "PUT"
    assert call["path"] == "/cron-templates/tpl"
    # Untouched fields must be absent, not null — the server treats
    # missing as "keep" and null would also be "keep", but sending only
    # what changed keeps the intent obvious in logs.
    assert call["json"] == {"title": "新标题", "tags": ["team", "reminder"]}


def test_update_clear_tags_sends_empty_list(fake_client):
    client = fake_client(FakeResponse(payload={}))
    CliRunner().invoke(
        cron_group,
        ["template", "update", "tpl", "--clear-tags", *BASE],
    )
    assert client.calls[0]["json"] == {"tags": []}


def test_update_clear_batch_entry_sends_empty_string(fake_client):
    client = fake_client(FakeResponse(payload={}))
    CliRunner().invoke(
        cron_group,
        ["template", "update", "tpl", "--batch-entry", "", *BASE],
    )
    assert client.calls[0]["json"] == {"batch_entry": ""}


def test_update_reads_batch_file_from_disk(fake_client, tmp_path: Path):
    client = fake_client(FakeResponse(payload={}))
    batch = tmp_path / "collect.json"
    batch.write_text('{"actions": []}', encoding="utf-8")
    result = CliRunner().invoke(
        cron_group,
        [
            "template",
            "update",
            "tpl",
            "--add-batch",
            f"collect={batch}",
            "--remove-batch",
            "old",
            *BASE,
        ],
    )
    assert result.exit_code == 0, result.output
    assert client.calls[0]["json"] == {
        "batch_files": {"collect": '{"actions": []}'},
        "remove_batch_files": ["old"],
    }


@pytest.mark.parametrize(
    "pair", ["collect", "=./x.json", "collect=/nope.json"]
)
def test_update_rejects_malformed_add_batch(
    fake_client,
    pair: str,
):
    fake_client(FakeResponse(payload={}))
    result = CliRunner().invoke(
        cron_group,
        ["template", "update", "tpl", "--add-batch", pair, *BASE],
    )
    assert result.exit_code != 0
    assert "NAME=PATH" in result.output


def test_update_with_no_fields_is_rejected(fake_client):
    fake_client(FakeResponse(payload={}))
    result = CliRunner().invoke(
        cron_group,
        ["template", "update", "tpl", *BASE],
    )
    assert result.exit_code != 0
    assert "Nothing to update" in result.output


def test_update_builtin_surfaces_read_only_message(fake_client):
    fake_client(
        FakeResponse(
            status_code=400,
            payload={"detail": "Builtin template 'x' is read-only"},
        ),
    )
    result = CliRunner().invoke(
        cron_group,
        ["template", "update", "x", "--title", "t", *BASE],
    )
    assert result.exit_code != 0
    assert "read-only" in result.output


def test_delete_builtin_surfaces_server_message(fake_client):
    fake_client(
        FakeResponse(
            status_code=400,
            payload={"detail": "Builtin template 'x' cannot be deleted"},
        ),
    )
    result = CliRunner().invoke(cron_group, ["template", "delete", "x", *BASE])
    assert result.exit_code != 0
    assert "cannot be deleted" in result.output


# ---------------------------------------------------------------------------
# Error rendering
# ---------------------------------------------------------------------------


def test_template_error_renders_conflicts():
    response = FakeResponse(
        payload={
            "detail": {
                "conflicts": [
                    {"name": "a", "suggested_name": "a-2"},
                    {"name": "b", "suggested_name": "b-2"},
                ],
            },
        },
    )
    rendered = _template_error(response)
    assert "--rename-to a-2" in rendered
    assert "--rename-to b-2" in rendered


def test_template_error_renders_single_suggestion():
    response = FakeResponse(
        payload={"detail": {"message": "taken", "suggested_name": "a-2"}},
    )
    assert "--name a-2" in _template_error(response)


def test_template_error_renders_plain_string():
    response = FakeResponse(payload={"detail": "boom"})
    assert _template_error(response) == "boom"
