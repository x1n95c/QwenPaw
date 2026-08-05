# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name,unused-argument
"""HTTP contract tests for the tool batch router.

Mounts the router on a bare FastAPI app rather than booting the whole
application: these tests are about status codes and payload shapes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.app.tool_batches import store
from qwenpaw.app.tool_batches.api import router

from .conftest import SAMPLE_BATCH, batch_json, make_zip
from .test_service import make_scan_error


@pytest.fixture
def client(working_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def create_body(name: str = "api-batch", **overrides) -> dict:
    body = {"name": name, "content": dict(SAMPLE_BATCH)}
    body.update(overrides)
    return body


def upload(client: TestClient, blob: bytes, query: str = ""):
    return client.post(
        f"/tool-batches/upload{query}",
        files={"file": ("b.zip", io.BytesIO(blob), "application/zip")},
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_list_empty(client: TestClient):
    response = client.get("/tool-batches")
    assert response.status_code == 200
    assert response.json() == []


def test_get_missing_is_404(client: TestClient):
    response = client.get("/tool-batches/nope")
    assert response.status_code == 404


def test_list_carries_a_step_preview(client: TestClient):
    """The console renders the pool list without a request per script."""
    client.post("/tool-batches", json=create_body())
    body = client.get("/tool-batches").json()
    assert body[0]["preview_actions"] == SAMPLE_BATCH["actions"]
    assert body[0]["action_count"] == 1


def test_export_header_cannot_be_injected_via_the_name(client: TestClient):
    """A pool name is user-chosen, and it lands in a quoted header."""
    client.post("/tool-batches", json=create_body())
    response = client.get("/tool-batches/api-batch/export")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.count('filename="') == 1
    assert "filename*=UTF-8''" in disposition


def test_get_returns_content(client: TestClient):
    client.post("/tool-batches", json=create_body())
    response = client.get("/tool-batches/api-batch")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == SAMPLE_BATCH
    assert body["arg_names"] == ["greeting"]
    assert body["action_count"] == 1
    assert body["description"] == "示例脚本"


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------


def test_create_returns_info(client: TestClient):
    response = client.post("/tool-batches", json=create_body())
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "api-batch"
    assert body["arg_names"] == ["greeting"]
    assert body["action_count"] == 1


def test_create_conflict_is_409_with_suggestion(client: TestClient):
    client.post("/tool-batches", json=create_body())
    response = client.post("/tool-batches", json=create_body())
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["name"] == "api-batch"
    assert detail["suggested_name"] == "api-batch-2"


def test_create_invalid_name_is_400(client: TestClient):
    response = client.post("/tool-batches", json=create_body(name="a/b"))
    assert response.status_code == 400


def test_create_invalid_content_is_400(client: TestClient):
    response = client.post(
        "/tool-batches",
        json=create_body(content={"steps": []}),
    )
    assert response.status_code == 400


def test_update_patches_content(client: TestClient):
    client.post("/tool-batches", json=create_body())
    new_actions = [{"tool_name": "read_file", "arguments": {}}]
    response = client.put(
        "/tool-batches/api-batch",
        json={"content": new_actions},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action_count"] == 1
    detail = client.get("/tool-batches/api-batch").json()
    assert detail["content"]["actions"] == new_actions
    assert detail["description"] == "示例脚本"


def test_update_missing_is_404(client: TestClient):
    response = client.put("/tool-batches/ghost", json={"description": "x"})
    assert response.status_code == 404


def test_delete_then_404(client: TestClient):
    client.post("/tool-batches", json=create_body())
    response = client.delete("/tool-batches/api-batch")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "name": "api-batch"}
    assert client.delete("/tool-batches/api-batch").status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_sets_attachment_headers(client: TestClient):
    client.post("/tool-batches", json=create_body())
    response = client.get("/tool-batches/api-batch/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (
        'filename="api-batch.zip"' in response.headers["content-disposition"]
    )
    assert len(response.content) > 0


def test_export_missing_is_404(client: TestClient):
    assert client.get("/tool-batches/nope/export").status_code == 404


def test_export_reimport_round_trip(client: TestClient):
    client.post("/tool-batches", json=create_body())
    exported = client.get("/tool-batches/api-batch/export")
    assert client.delete("/tool-batches/api-batch").status_code == 200
    response = upload(client, exported.content)
    assert response.status_code == 200
    assert response.json()["imported"] == ["api-batch"]
    assert client.get("/tool-batches/api-batch").json()["content"] == (
        SAMPLE_BATCH
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_single_json_imports_directly(client: TestClient):
    blob = make_zip({"collect.json": batch_json()})
    response = upload(client, blob)
    assert response.status_code == 200
    assert response.json() == {"imported": ["collect"], "conflicts": []}


def test_upload_without_json_is_400(client: TestClient):
    response = upload(client, make_zip({"readme.md": "x"}))
    assert response.status_code == 400
    assert "No .json" in response.json()["detail"]


def test_upload_multi_without_select_is_two_phase(client: TestClient):
    blob = make_zip(
        {"a.json": batch_json(), "b.json": batch_json()},
    )
    response = upload(client, blob)
    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == []
    assert [c["file_name"] for c in body["candidates"]] == [
        "a.json",
        "b.json",
    ]
    # Nothing written until the client picks.
    assert client.get("/tool-batches").json() == []


def test_upload_multi_with_select_imports_subset(client: TestClient):
    blob = make_zip(
        {
            "a.json": batch_json(),
            "b.json": batch_json(),
            "c.json": batch_json(),
        },
    )
    response = upload(client, blob, "?select=a.json,c.json")
    assert response.status_code == 200
    assert response.json()["imported"] == ["a", "c"]


def test_upload_conflict_is_409_with_all_conflicts(client: TestClient):
    client.post("/tool-batches", json=create_body(name="a"))
    client.post("/tool-batches", json=create_body(name="b"))
    blob = make_zip(
        {
            "a.json": batch_json(),
            "b.json": batch_json(),
            "c.json": batch_json(),
        },
    )
    response = upload(client, blob, "?select=a.json,b.json,c.json")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"]
    assert {c["name"] for c in detail["conflicts"]} == {"a", "b"}
    for conflict in detail["conflicts"]:
        assert conflict["file_name"]
        assert conflict["suggested_name"]
    # "c" was clean but batch semantics mean nothing landed.
    assert {b["name"] for b in client.get("/tool-batches").json()} == {
        "a",
        "b",
    }


def test_upload_overwrite(client: TestClient):
    client.post("/tool-batches", json=create_body(name="collect"))
    blob = make_zip({"collect.json": batch_json()})
    response = upload(client, blob, "?overwrite=true")
    assert response.status_code == 200
    assert response.json()["imported"] == ["collect"]


def test_upload_rename_map(client: TestClient):
    client.post("/tool-batches", json=create_body(name="collect"))
    blob = make_zip({"collect.json": batch_json()})
    query = "?rename_map=" + json.dumps({"collect": "renamed"})
    response = upload(client, blob, query)
    assert response.status_code == 200
    assert response.json()["imported"] == ["renamed"]


def test_upload_rejects_bad_rename_map(client: TestClient):
    blob = make_zip({"a.json": batch_json()})
    response = upload(client, blob, "?rename_map=notjson")
    assert response.status_code == 400
    assert "rename_map" in response.json()["detail"]


def test_upload_rejects_non_object_rename_map(client: TestClient):
    blob = make_zip({"a.json": batch_json()})
    response = upload(client, blob, "?rename_map=[1,2]")
    assert response.status_code == 400


def test_upload_rejects_wrong_content_type(client: TestClient):
    response = client.post(
        "/tool-batches/upload",
        files={"file": ("b.txt", io.BytesIO(b"nope"), "text/plain")},
    )
    assert response.status_code == 400
    assert "zip" in response.json()["detail"]


def test_upload_rejects_non_zip_bytes(client: TestClient):
    response = upload(client, b"not a zip at all")
    assert response.status_code == 400


def test_upload_rejects_traversal(client: TestClient):
    blob = make_zip({"a.json": batch_json(), "../../pwned.txt": "x"})
    response = upload(client, blob)
    assert response.status_code == 400
    assert "Unsafe path" in response.json()["detail"]


def test_upload_scan_rejection_is_structured_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Same shape as cron_templates' _scan_error_response."""

    def _reject(*_args, **_kwargs):
        raise make_scan_error("collect")

    monkeypatch.setattr(store, "scan_skill_directory", _reject)
    response = upload(client, make_zip({"collect.json": batch_json()}))
    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "security_scan_failed"
    assert body["template_name"] == "collect"
    assert body["max_severity"] == "HIGH"
    assert body["findings"][0]["title"] == "Dangerous command"
    assert body["findings"][0]["severity"] == "HIGH"
    assert body["findings"][0]["file_path"] == "collect.json"
    assert body["findings"][0]["line_number"] == 1
    assert body["findings"][0]["rule_id"] == "R1"
    # Nothing landed on disk.
    assert client.get("/tool-batches").json() == []


def test_create_scan_rejection_is_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def _reject(*_args, **_kwargs):
        raise make_scan_error("api-batch")

    monkeypatch.setattr(store, "scan_skill_directory", _reject)
    response = client.post("/tool-batches", json=create_body())
    assert response.status_code == 422
    assert response.json()["type"] == "security_scan_failed"


def test_scanner_does_not_skip_json_files(tmp_path: Path):
    """Batch JSON is not in the scanner's skip set: shell inside
    ``arguments.command`` is exactly what the signatures should see."""
    from qwenpaw.security.skill_scanner.scanner import SkillScanner

    scan_dir = tmp_path / "scanme"
    scan_dir.mkdir()
    content = {
        "actions": [
            {
                "tool_name": "execute_shell_command",
                "arguments": {"command": "echo hello"},
            },
        ],
    }
    (scan_dir / "collect.json").write_text(
        json.dumps(content),
        encoding="utf-8",
    )
    scanner = SkillScanner()
    files = scanner._discover_files(  # pylint: disable=protected-access
        scan_dir,
    )
    assert [f.relative_path for f in files] == ["collect.json"]
