# -*- coding: utf-8 -*-
"""Job-owned script storage: the write gate, cleanup, and orphan reaping."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.app.crons.manager import (
    CronManager,
    INTERNAL_JOB_IDS,
    validate_job_id_for_write,
)
from qwenpaw.app.crons.repo.json_repo import JsonJobRepository
from qwenpaw.app.crons.script_paths import job_scripts_dir
from tests.unit.app.conftest import make_cron_job_spec

JOB_ID = "3f4a1c2e-0000-4000-8000-000000000001"
OTHER_ID = "bbbbbbbb-0000-4000-8000-000000000002"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def manager(workspace: Path) -> CronManager:
    return CronManager(
        repo=JsonJobRepository(workspace / "jobs.json"),
        workspace=SimpleNamespace(workspace_dir=workspace),
        channel_manager=None,
        timezone="UTC",
    )


def seed_scripts(workspace: Path, job_id: str) -> Path:
    directory = job_scripts_dir(workspace, job_id)
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "collect.json").write_text("[]", encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# validate_job_id_for_write
#
# A job id names a directory, and three surfaces reach the same method:
# POST /cron/jobs, the auto-registered POST /crons/jobs, and the
# cron-create slash command. The gate has to sit where all three meet.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job_id", [None, JOB_ID, "j0", "daily-report"])
def test_write_gate_accepts_usable_ids(job_id):
    validate_job_id_for_write(job_id)


@pytest.mark.parametrize(
    "job_id",
    [
        "..",
        "../..",
        "../../../../tmp/pwn",
        "/etc",
        "a/b",
        "a\\b",
        "",
        "\x00",
        "x\x00y",
        "CON",
        "nul",
        ".hidden",
        "-dash",
        "a" * 65,
        "任务",
    ],
)
def test_write_gate_refuses_unsafe_ids(job_id: str):
    with pytest.raises(ValueError):
        validate_job_id_for_write(job_id)


@pytest.mark.parametrize("job_id", sorted(INTERNAL_JOB_IDS))
def test_write_gate_refuses_our_own_ids(job_id: str):
    """A client must not be able to impersonate the heartbeat."""
    with pytest.raises(ValueError, match="reserved"):
        validate_job_id_for_write(job_id)


def test_write_gate_does_not_require_a_uuid():
    """Deliberate: POST /crons/jobs and cron-create have always taken
    readable ids, and traversal safety is what actually matters."""
    validate_job_id_for_write("my-nightly-report")


@pytest.mark.asyncio
async def test_create_or_replace_rejects_a_traversal_id(
    manager: CronManager,
    workspace: Path,
):
    spec = make_cron_job_spec()
    spec.id = "../../../../tmp/pwn"
    with pytest.raises(ValueError):
        await manager.create_or_replace_job(spec)
    # And nothing was written: not the job, not a directory.
    assert await manager.list_jobs() == []
    assert not (workspace / "cron_jobs").exists()


# ---------------------------------------------------------------------------
# delete_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_job_removes_its_scripts(
    manager: CronManager,
    workspace: Path,
):
    spec = make_cron_job_spec()
    spec.id = JOB_ID
    await manager.create_or_replace_job(spec)
    directory = seed_scripts(workspace, JOB_ID)

    await manager.delete_job(JOB_ID)

    assert not directory.exists()
    assert not directory.parent.exists()


@pytest.mark.asyncio
async def test_delete_job_leaves_other_jobs_alone(
    manager: CronManager,
    workspace: Path,
):
    spec = make_cron_job_spec()
    spec.id = JOB_ID
    await manager.create_or_replace_job(spec)
    seed_scripts(workspace, JOB_ID)
    keep = seed_scripts(workspace, OTHER_ID)

    await manager.delete_job(JOB_ID)

    assert keep.is_dir()


@pytest.mark.asyncio
async def test_delete_with_an_unsafe_id_is_a_noop_not_a_crash(
    manager: CronManager,
    workspace: Path,
):
    keep = seed_scripts(workspace, JOB_ID)
    await manager.delete_job("../../../../tmp")
    assert keep.is_dir()


# ---------------------------------------------------------------------------
# orphan reaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_reaps_a_directory_with_no_job(
    manager: CronManager,
    workspace: Path,
):
    """Abandoning the create drawer leaves scripts under an id that was
    never saved."""
    live = seed_scripts(workspace, JOB_ID)
    orphan = seed_scripts(workspace, OTHER_ID)

    await manager._prune_orphan_job_scripts({JOB_ID})

    assert live.is_dir()
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_prune_spares_the_synthesized_jobs(
    manager: CronManager,
    workspace: Path,
):
    """`_heartbeat` / `_dream` are never in jobs.json, so a reaper built on
    that set alone would delete their scripts on every boot."""
    kept = [seed_scripts(workspace, job_id) for job_id in INTERNAL_JOB_IDS]

    await manager._prune_orphan_job_scripts(set())

    for directory in kept:
        assert directory.is_dir()


@pytest.mark.asyncio
async def test_prune_without_a_workspace_is_a_noop(workspace: Path):
    manager = CronManager(
        repo=JsonJobRepository(workspace / "jobs.json"),
        workspace=SimpleNamespace(),
        channel_manager=None,
        timezone="UTC",
    )
    await manager._prune_orphan_job_scripts(set())


# ---------------------------------------------------------------------------
# workspace zip upload — the bypass this change had to close
# ---------------------------------------------------------------------------


def test_uploaded_workspace_batch_scripts_reach_the_scanner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Merging a workspace zip used to be the one write path around the
    scan, and a planted script is executed unattended.

    The payload has to arrive as *shell*: no shipped signature rule lists
    ``json`` among its ``file_types``, so scanning the stored form finds
    nothing whatever it contains.
    """
    from qwenpaw.app.routers import workspace as wsmod
    import qwenpaw.security.skill_scanner as scanner_mod

    payload = "chmod 777 /etc/passwd"
    root = tmp_path / "extracted"
    scripts = root / "cron_jobs" / JOB_ID / "batch"
    scripts.mkdir(parents=True)
    (scripts / "evil.json").write_text(
        '[{"tool_name": "execute_shell_command", '
        f'"arguments": {{"command": "{payload}"}}}}]',
        encoding="utf-8",
    )

    seen: dict[str, dict[str, str]] = {}
    original = scanner_mod.scan_skill_directory

    def spy(dir_path: Path, skill_name: str = "", **kwargs):
        seen[skill_name] = {
            path.name: path.read_text(encoding="utf-8", errors="replace")
            for path in dir_path.rglob("*")
            if path.is_file()
        }
        return original(dir_path, skill_name=skill_name, **kwargs)

    monkeypatch.setattr(scanner_mod, "scan_skill_directory", spy)

    wsmod._scan_executable_workspace_content(root)

    label = f"cron-job:{JOB_ID}"
    assert label in seen, f"job scripts were not scanned: {list(seen)}"
    shell = [
        name
        for name, body in seen[label].items()
        if name.endswith(".sh") and payload in body
    ]
    assert shell, f"payload never reached the scanner as shell: {seen[label]}"
    # The surrogate is scan-only and must not survive into the workspace.
    assert sorted(p.name for p in scripts.iterdir()) == ["evil.json"]


def test_zip_containment_rejects_a_sibling_prefix_directory(tmp_path: Path):
    """`<workspace>-evil` shares a string prefix with `<workspace>`."""
    import io
    import zipfile

    from fastapi import HTTPException

    from qwenpaw.app.routers import workspace as wsmod

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../ws-evil/pwn.txt", "x")

    with pytest.raises(HTTPException) as excinfo:
        wsmod._validate_zip_data(buffer.getvalue(), workspace_dir)
    assert "unsafe path" in str(excinfo.value.detail)


def test_zip_containment_still_accepts_ordinary_entries(tmp_path: Path):
    import io
    import zipfile

    from qwenpaw.app.routers import workspace as wsmod

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("memory/notes.md", "x")
        zf.writestr("jobs.json", "{}")

    wsmod._validate_zip_data(buffer.getvalue(), workspace_dir)


def test_scan_is_a_noop_for_a_workspace_without_scripts(tmp_path: Path):
    from qwenpaw.app.routers import workspace as wsmod

    root = tmp_path / "extracted"
    (root / "memory").mkdir(parents=True)
    shutil.rmtree(root / "memory")
    root.mkdir(exist_ok=True)
    wsmod._scan_executable_workspace_content(root)
