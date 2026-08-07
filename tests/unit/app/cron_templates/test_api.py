# -*- coding: utf-8 -*-
"""HTTP contract tests for the cron template router.

Mounts the router on a bare FastAPI app rather than booting the whole
application: these tests are about status codes and payload shapes, and the
router's only app-level dependency (agent resolution) is exercised solely by
the workspace-install path, which is covered in test_service.py.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.app.cron_templates.api import batch_script_router, router

from .conftest import BATCH_JSON, SKILL_DOC, make_zip, valid_zip_entries


@pytest.fixture
def client(workspace: Path) -> TestClient:
    """Templates are per workspace, so the service needs one resolved.

    Overriding the dependency rather than faking the agent lookup: it is
    the seam the endpoints actually use, and it keeps these tests about
    status codes rather than about agent resolution.
    """
    from qwenpaw.app.cron_templates.api import get_template_service
    from qwenpaw.app.cron_templates.service import CronTemplateService

    app = FastAPI()
    app.include_router(router)
    app.include_router(batch_script_router)
    app.dependency_overrides[
        get_template_service
    ] = lambda: CronTemplateService(workspace)
    return TestClient(app)


def create_body(name: str = "api-tpl", **overrides) -> dict:
    body = {
        "name": name,
        "title": "API 模板",
        "description": "说明",
        "category": "cron",
        "frequency": "每天",
        "tags": ["team"],
        "form": {"scheduleType": "cron", "cronCustom": "0 9 * * *"},
    }
    body.update(overrides)
    return body


def upload(client: TestClient, blob: bytes, query: str = ""):
    return client.post(
        f"/cron-templates/upload{query}",
        files={"file": ("t.zip", io.BytesIO(blob), "application/zip")},
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_list_returns_builtins(client: TestClient):
    response = client.get("/cron-templates")
    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert "weather-report" in names


def test_list_can_exclude_builtins(client: TestClient):
    response = client.get("/cron-templates?include_builtin=false")
    assert response.status_code == 200
    assert response.json() == []


def test_list_batch_scripts_returns_refs_and_metadata(client: TestClient):
    response = client.get("/cron-template-batches")
    assert response.status_code == 200
    by_ref = {item["ref"]: item for item in response.json()}
    weather = by_ref["weather-report/batch/weather.json"]
    assert weather["template"] == "weather-report"
    assert weather["file_name"] == "weather.json"
    assert weather["arg_names"] == ["city"]


def test_list_batch_scripts_can_exclude_builtins(client: TestClient):
    response = client.get("/cron-template-batches?include_builtin=false")
    assert response.status_code == 200
    assert response.json() == []


def test_batch_scripts_route_does_not_shadow_a_template(client: TestClient):
    """Its own prefix, so a template cannot be named out of reach."""
    client.post("/cron-templates", json=create_body(name="batches"))
    assert client.get("/cron-templates/batches").status_code == 200
    assert client.get("/cron-template-batches").status_code == 200


def test_get_missing_is_404(client: TestClient):
    response = client.get("/cron-templates/nope")
    assert response.status_code == 404


def test_get_returns_package_contents(client: TestClient):
    client.post("/cron-templates", json=create_body())
    response = client.get("/cron-templates/api-tpl")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "API 模板"
    assert body["source"] == "user"
    assert body["payload"]["form"]["cronCustom"] == "0 9 * * *"


def test_read_file_endpoint(client: TestClient):
    client.post(
        "/cron-templates",
        json=create_body(batch_files={"collect": BATCH_JSON}),
    )
    response = client.get("/cron-templates/api-tpl/files/batch/collect.json")
    assert response.status_code == 200
    assert json.loads(response.json()["content"])["actions"]


def test_read_missing_file_is_404(client: TestClient):
    client.post("/cron-templates", json=create_body())
    response = client.get("/cron-templates/api-tpl/files/batch/ghost.json")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Create / delete
# ---------------------------------------------------------------------------


def test_create_returns_package(client: TestClient):
    response = client.post(
        "/cron-templates",
        json=create_body(
            batch_files={"collect": BATCH_JSON},
            skills={"writer": SKILL_DOC},
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["batch_files"] == ["batch/collect.json"]
    assert body["skills"] == ["writer"]


def test_create_conflict_is_409_with_suggestion(client: TestClient):
    client.post("/cron-templates", json=create_body())
    response = client.post("/cron-templates", json=create_body())
    assert response.status_code == 409
    assert response.json()["detail"]["suggested_name"] == "api-tpl-2"


def test_create_invalid_name_is_400(client: TestClient):
    response = client.post("/cron-templates", json=create_body(name="a/b"))
    assert response.status_code == 400


def test_create_invalid_batch_is_400(client: TestClient):
    response = client.post(
        "/cron-templates",
        json=create_body(batch_files={"bad": "{oops"}),
    )
    assert response.status_code == 400


def test_update_patches_and_preserves(client: TestClient):
    client.post(
        "/cron-templates",
        json=create_body(
            batch_files={"collect": BATCH_JSON},
            skills={"writer": SKILL_DOC},
        ),
    )
    response = client.put(
        "/cron-templates/api-tpl",
        json={"description": "改过了"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "改过了"
    assert body["title"] == "API 模板"
    assert body["batch_files"] == ["batch/collect.json"]
    assert body["skills"] == ["writer"]


def test_update_missing_is_404(client: TestClient):
    response = client.put("/cron-templates/ghost", json={"title": "x"})
    assert response.status_code == 404


def test_update_builtin_is_400(client: TestClient):
    response = client.put(
        "/cron-templates/workspace-usage",
        json={"title": "x"},
    )
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"]


def test_update_invalid_batch_is_400(client: TestClient):
    client.post("/cron-templates", json=create_body())
    response = client.put(
        "/cron-templates/api-tpl",
        json={"batch_files": {"bad": "{oops"}},
    )
    assert response.status_code == 400


def test_delete_then_404(client: TestClient):
    client.post("/cron-templates", json=create_body())
    assert client.delete("/cron-templates/api-tpl").status_code == 200
    assert client.delete("/cron-templates/api-tpl").status_code == 404


def test_delete_builtin_is_400(client: TestClient):
    response = client.delete("/cron-templates/workspace-usage")
    assert response.status_code == 400


def test_fork_builtin(client: TestClient):
    response = client.post("/cron-templates/workspace-usage/fork")
    assert response.status_code == 200
    assert response.json()["source"] == "user"
    assert (
        client.post("/cron-templates/workspace-usage/fork").status_code == 409
    )


def test_fork_unknown_is_404(client: TestClient):
    assert client.post("/cron-templates/ghost/fork").status_code == 404


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


def test_export_sets_attachment_headers(client: TestClient):
    client.post("/cron-templates", json=create_body())
    response = client.get("/cron-templates/api-tpl/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="api-tpl.zip"' in response.headers["content-disposition"]
    assert len(response.content) > 0


def test_export_missing_is_404(client: TestClient):
    assert client.get("/cron-templates/nope/export").status_code == 404


def test_upload_then_export_round_trip(client: TestClient):
    assert upload(client, make_zip(valid_zip_entries())).json()["count"] == 1
    exported = client.get("/cron-templates/sample-template/export")
    assert exported.status_code == 200
    assert client.delete("/cron-templates/sample-template").status_code == 200
    assert upload(client, exported.content).json()["imported"] == [
        "sample-template",
    ]


def test_upload_conflict_is_409(client: TestClient):
    blob = make_zip(valid_zip_entries())
    upload(client, blob)
    response = upload(client, blob)
    assert response.status_code == 409
    conflicts = response.json()["detail"]["conflicts"]
    assert conflicts[0]["suggested_name"] == "sample-template-2"


def test_upload_overwrite(client: TestClient):
    blob = make_zip(valid_zip_entries())
    upload(client, blob)
    response = upload(client, blob, "?overwrite=true")
    assert response.status_code == 200


def test_upload_rename_map(client: TestClient):
    blob = make_zip(valid_zip_entries())
    upload(client, blob)
    query = "?rename_map=" + json.dumps({"sample-template": "renamed"})
    assert upload(client, blob, query).json()["imported"] == ["renamed"]


def test_upload_rejects_bad_rename_map(client: TestClient):
    response = upload(
        client, make_zip(valid_zip_entries()), "?rename_map=notjson"
    )
    assert response.status_code == 400
    assert "rename_map" in response.json()["detail"]


def test_upload_rejects_non_object_rename_map(client: TestClient):
    response = upload(
        client, make_zip(valid_zip_entries()), "?rename_map=[1,2]"
    )
    assert response.status_code == 400


def test_upload_rejects_wrong_content_type(client: TestClient):
    response = client.post(
        "/cron-templates/upload",
        files={"file": ("t.txt", io.BytesIO(b"nope"), "text/plain")},
    )
    assert response.status_code == 400
    assert "zip" in response.json()["detail"]


def test_upload_rejects_traversal(client: TestClient):
    entries = valid_zip_entries()
    entries["../../pwned.txt"] = "x"
    response = upload(client, make_zip(entries))
    assert response.status_code == 400
    assert "Unsafe path" in response.json()["detail"]


def test_upload_rejects_non_zip_bytes(client: TestClient):
    response = upload(client, b"not a zip at all")
    assert response.status_code == 400
