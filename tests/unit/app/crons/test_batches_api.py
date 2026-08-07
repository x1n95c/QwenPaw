# -*- coding: utf-8 -*-
"""HTTP contract tests for a cron job's own batch scripts.

Mounted on a bare app rather than booting the whole application: these are
about status codes, isolation between jobs, and the ``job_id`` gate — which
matters more here than anywhere else in the feature, because ``job_id``
arrives as a path segment and becomes a directory name.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.app.crons.manager import CronManager
from qwenpaw.app.crons.repo.json_repo import JsonJobRepository

JOB = "aaaaaaaa-0000-4000-8000-000000000001"
OTHER = "bbbbbbbb-0000-4000-8000-000000000002"

SCRIPT = {
    "name": "collect",
    "content": {
        "actions": [
            {
                "tool_name": "execute_shell_command",
                "arguments": {"command": "echo ${args.x}"},
            },
        ],
    },
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace under a temp WORKING_DIR.

    ``WORKING_DIR`` has to stay a ``Path``: several modules join onto it at
    import time.
    """
    from qwenpaw import constant

    monkeypatch.setattr(constant, "WORKING_DIR", tmp_path)
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def client(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from qwenpaw.app import agent_context
    from qwenpaw.app.crons.api import get_cron_manager
    from qwenpaw.app.crons.api import router as cron_router
    from qwenpaw.app.crons.batches_api import router as batches_router

    manager = CronManager(
        repo=JsonJobRepository(workspace / "jobs.json"),
        workspace=SimpleNamespace(workspace_dir=workspace),
        channel_manager=None,
        timezone="UTC",
    )
    app = FastAPI()
    app.include_router(cron_router)
    app.include_router(batches_router)
    app.dependency_overrides[get_cron_manager] = lambda: manager

    async def fake_agent(_request):
        return SimpleNamespace(
            workspace_dir=str(workspace),
            agent_id="default",
        )

    monkeypatch.setattr(agent_context, "get_agent_for_request", fake_agent)
    return TestClient(app)


@pytest.fixture
def collector_skill(workspace: Path) -> Path:
    """An installed skill carrying batch JSON, plus decoys.

    The layout `make-skill` teaches agents to write. Without this on disk
    the path-rejection cases below would 404 for the wrong reason — "no
    such skill" rather than "that path is not addressable".
    """
    skill_dir = workspace / "skills" / "collector"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "batch").mkdir()
    (skill_dir / "references").mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Collector\ndescription: d\n---\n\nbody\n",
        encoding="utf-8",
    )
    for relative in ("scripts/collect.json", "batch/other.json"):
        (skill_dir / relative).write_text(
            json.dumps({"actions": [{"tool_name": "read_file"}]}),
            encoding="utf-8",
        )
    # Decoys that must stay unreachable.
    (skill_dir / "references" / "doc.json").write_text("[]", encoding="utf-8")
    (skill_dir / "root.json").write_text("[]", encoding="utf-8")
    (skill_dir / "scripts" / "x.txt").write_text("nope", encoding="utf-8")
    return skill_dir


def names(client: TestClient, job_id: str) -> list[str]:
    response = client.get(f"/cron/jobs/{job_id}/batches")
    assert response.status_code == 200
    return sorted(item["name"] for item in response.json())


# ---------------------------------------------------------------------------
# CRUD, and the isolation that is the point of the whole change
# ---------------------------------------------------------------------------


def test_create_writes_into_the_jobs_own_directory(
    client: TestClient,
    workspace: Path,
):
    assert client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT).status_code
    landed = workspace / "cron_jobs" / JOB / "batch" / "collect.json"
    assert landed.is_file()


def test_a_script_is_invisible_to_every_other_job(client: TestClient):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    assert names(client, JOB) == ["collect"]
    assert names(client, OTHER) == []
    assert client.get(f"/cron/jobs/{OTHER}/batches/collect").status_code == 404


def test_describes_the_script_like_the_pool_did(client: TestClient):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    info = client.get(f"/cron/jobs/{JOB}/batches").json()[0]
    assert info["arg_names"] == ["x"]
    assert info["action_count"] == 1
    assert info["preview_actions"]


def test_duplicate_name_is_a_conflict_with_a_suggestion(client: TestClient):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    response = client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    assert response.status_code == 409
    assert response.json()["detail"]["suggested_name"] == "collect-2"


def test_the_same_name_in_two_jobs_is_not_a_conflict(client: TestClient):
    """Two jobs holding the same recipe is the expected case now."""
    assert client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT).status_code
    assert client.post(f"/cron/jobs/{OTHER}/batches", json=SCRIPT).status_code
    assert names(client, JOB) == names(client, OTHER) == ["collect"]


def test_update_and_delete(client: TestClient):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    updated = client.put(
        f"/cron/jobs/{JOB}/batches/collect",
        json={"content": {"actions": [{"tool_name": "read_file"}]}},
    )
    assert updated.status_code == 200
    assert updated.json()["arg_names"] == []

    assert client.delete(f"/cron/jobs/{JOB}/batches/collect").status_code
    assert names(client, JOB) == []
    gone = client.delete(f"/cron/jobs/{JOB}/batches/collect")
    assert gone.status_code == 404


def test_export_round_trips_through_upload(client: TestClient):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    exported = client.get(f"/cron/jobs/{JOB}/batches/collect/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"

    imported = client.post(
        f"/cron/jobs/{OTHER}/batches/upload",
        files={"file": ("collect.zip", exported.content, "application/zip")},
    )
    assert imported.status_code == 200
    assert names(client, OTHER) == ["collect"]


# ---------------------------------------------------------------------------
# copy — the only way to reach somebody else's script
# ---------------------------------------------------------------------------


def test_copy_from_a_template(client: TestClient):
    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={
            "from_template": "weather-report",
            "file": "batch/weather.json",
        },
    )
    assert response.status_code == 200
    assert response.json()["name"] == "weather"
    assert response.json()["arg_names"] == ["city"]
    assert names(client, JOB) == ["weather"]


def test_copy_from_a_sibling_job_is_independent(client: TestClient):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    assert (
        client.post(
            f"/cron/jobs/{OTHER}/batches/copy",
            json={"from_job_id": JOB, "file": "collect"},
        ).status_code
        == 200
    )

    # Editing the copy must not touch the original.
    client.put(
        f"/cron/jobs/{OTHER}/batches/collect",
        json={"content": {"actions": [{"tool_name": "read_file"}]}},
    )
    assert client.get(f"/cron/jobs/{JOB}/batches").json()[0]["arg_names"] == [
        "x",
    ]


def test_copying_twice_renames_rather_than_failing(client: TestClient):
    """The caller is mid-gesture in a dropdown; the response is
    authoritative about the name that landed."""
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    first = client.post(
        f"/cron/jobs/{OTHER}/batches/copy",
        json={"from_job_id": JOB, "file": "collect"},
    )
    second = client.post(
        f"/cron/jobs/{OTHER}/batches/copy",
        json={"from_job_id": JOB, "file": "collect"},
    )
    assert first.json()["name"] == "collect"
    assert second.json()["name"] == "collect-2"


def test_copy_requires_exactly_one_source(client: TestClient):
    for body in (
        {"file": "x"},
        {"from_job_id": JOB, "from_template": "weather-report", "file": "x"},
        {"from_job_id": JOB, "from_skill": "s", "file": "x"},
        {"from_template": "weather-report", "from_skill": "s", "file": "x"},
        {
            "from_job_id": JOB,
            "from_template": "weather-report",
            "from_skill": "s",
            "file": "x",
        },
    ):
        response = client.post(f"/cron/jobs/{JOB}/batches/copy", json=body)
        assert response.status_code == 400


def test_a_skill_qualifier_without_a_skill_is_400(client: TestClient):
    """`from_skill_template` qualifies `from_skill`; it is not a source.

    Rejected rather than ignored: silently dropping it would resolve
    against the workspace and copy a same-named installed skill's script
    instead of the package's.
    """
    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={
            "from_skill_template": "workspace-usage",
            "from_job_id": OTHER,
            "file": "x",
        },
    )
    assert response.status_code == 400
    assert "requires from_skill" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"from_template": "nope", "file": "batch/x.json"},
        {"from_template": "weather-report", "file": "batch/ghost.json"},
        # Outside the package's batch/ dir.
        {"from_template": "weather-report", "file": "TEMPLATE.md"},
        {"from_template": "weather-report", "file": "batch/../TEMPLATE.md"},
        {"from_job_id": OTHER, "file": "ghost"},
        {"from_job_id": OTHER, "file": "../../../../etc/passwd"},
        # A skill that is not there at all.
        {"from_skill": "ghost-skill", "file": "scripts/x.json"},
        # Only `scripts/` and `batch/` are addressable inside a skill:
        # `references/` holds documents and the root holds SKILL.md plus
        # whatever config sits beside it.
        {"from_skill": "collector", "file": "references/doc.json"},
        {"from_skill": "collector", "file": "SKILL.md"},
        {"from_skill": "collector", "file": "root.json"},
        {"from_skill": "collector", "file": "scripts/x.txt"},
        {"from_skill": "collector", "file": "scripts/../SKILL.md"},
        {"from_skill": "collector", "file": "scripts\\x.json"},
        {"from_skill": "collector", "file": "/etc/passwd"},
        # A real skill, a file it does not carry.
        {"from_skill": "collector", "file": "scripts/ghost.json"},
    ],
)
def test_copy_of_an_unreachable_source_is_404(
    client: TestClient,
    collector_skill: Path,
    body: dict,
):
    response = client.post(f"/cron/jobs/{JOB}/batches/copy", json=body)
    assert response.status_code == 404
    assert names(client, JOB) == []


# ---------------------------------------------------------------------------
# the cross-job browser
# ---------------------------------------------------------------------------


def test_grouped_listing_skips_the_current_job_and_empty_ones(
    client: TestClient,
):
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    client.post(f"/cron/jobs/{OTHER}/batches", json=SCRIPT)

    groups = client.get(
        "/cron/job-batches",
        params={"exclude_job_id": JOB},
    ).json()
    assert [group["job_id"] for group in groups] == [OTHER]
    assert [b["name"] for b in groups[0]["batches"]] == ["collect"]

    # A job with no scripts contributes no group at all.
    assert client.get("/cron/job-batches").json().__len__() == 2


# ---------------------------------------------------------------------------
# the job_id gate — job_id is a directory name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "job_id",
    [
        # `is_safe_job_id` rejects these outright.
        "..%5C..",
        "%00",
        "CON",
        "nul",
        "-dash",
        ".hidden",
        # Ours. `is_safe_job_id` allows them (they own real directories) but
        # a client must never write there — and the orphan reaper spares
        # those ids, so anything planted would survive every restart while
        # staying invisible to every UI.
        "_heartbeat",
        "_dream",
    ],
)
def test_a_bad_job_id_is_rejected_and_writes_nothing(
    client: TestClient,
    workspace: Path,
    job_id: str,
):
    for response in (
        client.get(f"/cron/jobs/{job_id}/batches"),
        client.post(f"/cron/jobs/{job_id}/batches", json=SCRIPT),
        client.post(
            f"/cron/jobs/{job_id}/batches/copy",
            json={"from_template": "weather-report", "file": "batch/x.json"},
        ),
    ):
        assert response.status_code == 400, response.text

    assert list((workspace / "cron_jobs").glob("**/*.json")) == []


def test_percent_encoded_traversal_never_reaches_a_handler(
    client: TestClient,
    workspace: Path,
):
    """`%2F` does not become a path separator, so the route simply misses."""
    response = client.post(
        "/cron/jobs/..%2F..%2F..%2Fetc/batches",
        json=SCRIPT,
    )
    assert response.status_code == 404
    assert list(workspace.rglob("*.json")) == []


def test_a_job_that_does_not_exist_yet_may_hold_scripts(client: TestClient):
    """Deliberate: the console mints a uuid when the create drawer opens and
    writes scripts before the job is ever saved."""
    assert client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT).status_code
    assert names(client, JOB) == ["collect"]


# ---------------------------------------------------------------------------
# save-as-template → create-from-template, the full round trip
# ---------------------------------------------------------------------------


def test_a_job_script_survives_being_saved_and_reused_as_a_template(
    client: TestClient,
    workspace: Path,
):
    """The loop the console drives: a job's script becomes a template's
    bundled file, and applying that template copies it into a *new* job.

    Worth pinning end to end because the script changes owner twice and
    each hop has its own naming rule — the package addresses it as
    ``batch/<name>.json`` while a job addresses it as ``<name>``.
    """
    from qwenpaw.app.cron_templates.api import batch_script_router
    from qwenpaw.app.cron_templates.api import router as template_router

    client.app.include_router(template_router)
    client.app.include_router(batch_script_router)

    # 1. the source job owns a script
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    detail = client.get(f"/cron/jobs/{JOB}/batches/collect").json()

    # 2. saved as a template, carrying that script's content verbatim
    created = client.post(
        "/cron-templates",
        json={
            "name": "my-collector",
            "title": "我的采集",
            "category": "cron",
            "form": {"scheduleType": "cron", "cronCustom": "0 9 * * *"},
            "batch_files": {"collect.json": json.dumps(detail["content"])},
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["batch_files"] == ["batch/collect.json"]

    # 3. the picker can see it
    listed = client.get("/cron-template-batches").json()
    mine = [s for s in listed if s["template"] == "my-collector"]
    assert [s["file_path"] for s in mine] == ["batch/collect.json"]
    assert mine[0]["arg_names"] == ["x"]

    # 4. applying it copies the script into a brand-new job
    landed = client.post(
        f"/cron/jobs/{OTHER}/batches/copy",
        json={"from_template": "my-collector", "file": "batch/collect.json"},
    )
    assert landed.status_code == 200, landed.text
    assert landed.json()["name"] == "collect"
    assert names(client, OTHER) == ["collect"]

    # 5. and it resolves at run time, from the new job's own directory
    from qwenpaw.app.crons.script_paths import resolve_job_script

    resolved = resolve_job_script(workspace, OTHER, "collect")
    assert resolved is not None
    assert resolved.parent.parent.name == OTHER


def test_the_reused_copy_is_independent_of_the_package(client: TestClient):
    """Editing the job's copy must not reach back into the template."""
    from qwenpaw.app.cron_templates.api import batch_script_router
    from qwenpaw.app.cron_templates.api import router as template_router

    client.app.include_router(template_router)
    client.app.include_router(batch_script_router)
    client.post(f"/cron/jobs/{JOB}/batches", json=SCRIPT)
    detail = client.get(f"/cron/jobs/{JOB}/batches/collect").json()
    client.post(
        "/cron-templates",
        json={
            "name": "my-collector",
            "title": "我的采集",
            "category": "cron",
            "form": {"scheduleType": "cron"},
            "batch_files": {"collect.json": json.dumps(detail["content"])},
        },
    )
    client.post(
        f"/cron/jobs/{OTHER}/batches/copy",
        json={"from_template": "my-collector", "file": "batch/collect.json"},
    )

    client.put(
        f"/cron/jobs/{OTHER}/batches/collect",
        json={"content": {"actions": [{"tool_name": "read_file"}]}},
    )

    still = client.get("/cron-template-batches").json()
    mine = [s for s in still if s["template"] == "my-collector"][0]
    assert mine["arg_names"] == ["x"], "the package must be untouched"


def _mount_templates(client: TestClient) -> None:
    from qwenpaw.app.cron_templates.api import batch_script_router
    from qwenpaw.app.cron_templates.api import router as template_router

    client.app.include_router(template_router)
    client.app.include_router(batch_script_router)


def test_applying_a_template_copies_every_bundled_script(
    client: TestClient,
) -> None:
    """A package's scripts all belong to the job, declared or not.

    ``workspace-usage`` bundles a unix and a windows variant for the
    *agent* to choose between and declares no preprocess at all. Copying
    only declared scripts left such a job owning nothing, and the user had
    to go browsing other packages to fetch back their own template's files.
    """
    _mount_templates(client)
    body = json.dumps(SCRIPT["content"])
    client.post(
        "/cron-templates",
        json={
            "name": "two-scripts",
            "title": "两个脚本",
            "category": "cron",
            "form": {"scheduleType": "cron"},
            "batch_files": {"scan-unix.json": body, "scan-windows.json": body},
        },
    )
    for file in ("batch/scan-unix.json", "batch/scan-windows.json"):
        landed = client.post(
            f"/cron/jobs/{JOB}/batches/copy",
            json={"from_template": "two-scripts", "file": file},
        )
        assert landed.status_code == 200, landed.text
    assert names(client, JOB) == ["scan-unix", "scan-windows"]


def test_two_bundled_files_with_the_same_basename_land_distinctly(
    client: TestClient,
) -> None:
    """Nested and top-level files sharing a stem must not collide.

    A job's directory is flat, so both want to be ``a``. The second is
    renamed rather than overwriting the first — the console relies on the
    response naming the copy that actually landed.
    """
    _mount_templates(client)
    body = json.dumps(SCRIPT["content"])
    created = client.post(
        "/cron-templates",
        json={
            "name": "same-stem",
            "title": "同名",
            "category": "cron",
            "form": {"scheduleType": "cron"},
            "batch_files": {"a.json": body, "sub/a.json": body},
        },
    )
    assert created.status_code == 200, created.text

    landed = [
        client.post(
            f"/cron/jobs/{JOB}/batches/copy",
            json={"from_template": "same-stem", "file": file},
        ).json()["name"]
        for file in ("batch/a.json", "batch/sub/a.json")
    ]
    assert landed == ["a", "a-2"]
    assert names(client, JOB) == ["a", "a-2"]


def test_a_bundled_file_with_an_illegal_stem_is_a_400_not_a_500(
    client: TestClient,
    workspace: Path,
) -> None:
    """Copying every bundled file now reaches names the package never
    validated.

    ``validate_template_package`` checks batch *content*, never batch file
    *names*, so an imported package can legally hold ``batch/CON.json`` —
    which ``normalize_batch_name`` refuses as a Windows device name. Before
    copy-everything this was unreachable unless a step declared it.
    """
    _mount_templates(client)
    package = workspace / "cron_templates" / "odd-name" / "batch"
    package.mkdir(parents=True)
    (package / "CON.json").write_text(
        json.dumps(SCRIPT["content"]),
        encoding="utf-8",
    )
    # Laid down by hand, the way a zip import lands one: TEMPLATE.md is
    # what marks a directory as a package at all.
    (package.parent / "TEMPLATE.md").write_text("# odd\n", encoding="utf-8")
    (package.parent / "template.json").write_text(
        json.dumps(
            {
                "schema_version": "cron-template.v1",
                "form": {"scheduleType": "cron"},
                "job": {"name": "odd", "schedule": {"type": "cron"}},
            },
        ),
        encoding="utf-8",
    )

    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={"from_template": "odd-name", "file": "batch/CON.json"},
    )
    assert response.status_code == 400, response.text
    assert names(client, JOB) == []


# ---------------------------------------------------------------------------
# Copying a script a skill carries
#
# A skill may ship its own `run_tool_batch` JSON — the layout `make-skill`
# teaches agents to write — and a job that references that skill usually
# wants to run it first. Copied in like any other foreign script, so the
# job owns what it runs.
# ---------------------------------------------------------------------------


def test_copy_from_an_installed_skill(
    client: TestClient,
    collector_skill: Path,
):
    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={"from_skill": "collector", "file": "scripts/collect.json"},
    )

    assert response.status_code == 200
    # Named after the file's stem, not its directory: the job's scripts are
    # a flat namespace.
    assert response.json()["name"] == "collect"
    assert names(client, JOB) == ["collect"]


def test_copy_from_a_skills_batch_directory_too(
    client: TestClient,
    collector_skill: Path,
):
    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={"from_skill": "collector", "file": "batch/other.json"},
    )

    assert response.status_code == 200
    assert names(client, JOB) == ["other"]


def test_copy_from_a_template_bundled_skill(
    client: TestClient,
    workspace: Path,
):
    """The qualifier picks the package's copy, not a same-named local one."""
    package = workspace / "cron_templates" / "space-check"
    (package / "skills" / "advisor" / "scripts").mkdir(parents=True)
    (package / "TEMPLATE.md").write_text(
        "---\ntitle: Space check\n---\n\n# Space check\n",
        encoding="utf-8",
    )
    (package / "template.json").write_text("{}", encoding="utf-8")
    (package / "skills" / "advisor" / "SKILL.md").write_text(
        "---\nname: Advisor\ndescription: d\n---\n\nbody\n",
        encoding="utf-8",
    )
    (package / "skills" / "advisor" / "scripts" / "scan.json").write_text(
        json.dumps({"actions": [{"tool_name": "read_file"}]}),
        encoding="utf-8",
    )

    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={
            "from_skill": "advisor",
            "from_skill_template": "space-check",
            "file": "scripts/scan.json",
        },
    )

    assert response.status_code == 200
    assert names(client, JOB) == ["scan"]


def test_the_qualifier_decides_which_same_named_skill_is_used(
    client: TestClient,
    workspace: Path,
    collector_skill: Path,
):
    # An installed `collector` and a bundled `collector`, each carrying a
    # different `scripts/collect.json`. Without the qualifier being
    # load-bearing, one would silently shadow the other.
    package = workspace / "cron_templates" / "pkg"
    (package / "skills" / "collector" / "scripts").mkdir(parents=True)
    (package / "TEMPLATE.md").write_text(
        "---\ntitle: P\n---\n\n# P\n", encoding="utf-8"
    )
    (package / "template.json").write_text("{}", encoding="utf-8")
    (package / "skills" / "collector" / "SKILL.md").write_text(
        "---\nname: C\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    (package / "skills" / "collector" / "scripts" / "collect.json").write_text(
        json.dumps({"actions": [{"tool_name": "execute_shell_command"}]}),
        encoding="utf-8",
    )

    for body in (
        {"from_skill": "collector", "file": "scripts/collect.json"},
        {
            "from_skill": "collector",
            "from_skill_template": "pkg",
            "file": "scripts/collect.json",
        },
    ):
        assert (
            client.post(
                f"/cron/jobs/{JOB}/batches/copy", json=body
            ).status_code
            == 200
        )

    assert names(client, JOB) == ["collect", "collect-2"]
    tools = []
    for name in ("collect", "collect-2"):
        detail = client.get(f"/cron/jobs/{JOB}/batches/{name}").json()
        tools.append(detail["content"]["actions"][0]["tool_name"])
    assert tools == ["read_file", "execute_shell_command"]


def test_the_copy_is_independent_of_the_skill(
    client: TestClient,
    collector_skill: Path,
):
    """Editing the job's copy must not change what the skill ships."""
    client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={"from_skill": "collector", "file": "scripts/collect.json"},
    )
    client.put(
        f"/cron/jobs/{JOB}/batches/collect",
        json={"content": {"actions": [{"tool_name": "write_file"}]}},
    )

    original = json.loads(
        (collector_skill / "scripts" / "collect.json").read_text(
            encoding="utf-8",
        ),
    )
    assert original["actions"][0]["tool_name"] == "read_file"


def test_a_skill_script_is_still_validated_on_the_way_in(
    client: TestClient,
    workspace: Path,
):
    """A skill is not a trusted source: the batch rules still apply."""
    skill_dir = workspace / "skills" / "sneaky" / "scripts"
    skill_dir.mkdir(parents=True)
    (workspace / "skills" / "sneaky" / "SKILL.md").write_text(
        "---\nname: S\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    # Nested `run_tool_batch` is banned everywhere else; it must be banned
    # here too, or a skill becomes a way around the check.
    (skill_dir / "nested.json").write_text(
        json.dumps({"actions": [{"tool_name": "run_tool_batch"}]}),
        encoding="utf-8",
    )

    response = client.post(
        f"/cron/jobs/{JOB}/batches/copy",
        json={"from_skill": "sneaky", "file": "scripts/nested.json"},
    )

    assert response.status_code == 400
    assert names(client, JOB) == []
