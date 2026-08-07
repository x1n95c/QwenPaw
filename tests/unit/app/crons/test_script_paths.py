# -*- coding: utf-8 -*-
"""Where a cron job's scripts live, and what may not become a path.

A job id is a directory name now, and it arrives from an HTTP body, so
these are the tests that matter most in the whole per-job change: every
function here must fail closed and must never raise, because the
resolution path that calls them promises its caller a message rather than
an exception.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qwenpaw.app.crons import script_paths as sp

JOB_ID = "3f4a1c2e-0000-4000-8000-000000000001"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def make_script(workspace: Path, job_id: str, name: str) -> Path:
    directory = sp.job_scripts_dir(workspace, job_id)
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("[]", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# is_safe_job_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "job_id",
    [
        JOB_ID,
        "j0",
        "daily-report",
        "a_b.c-d",
        # Synthesized internally; they are legitimate directory names even
        # though `validate_job_id_for_write` refuses to accept them from a
        # client.
        "_heartbeat",
        "_dream",
        "a" * 64,
    ],
)
def test_accepts_usable_ids(job_id: str):
    assert sp.is_safe_job_id(job_id) is True


@pytest.mark.parametrize(
    "job_id",
    [
        # Traversal, in every spelling.
        "..",
        ".",
        "../..",
        "../../../../etc",
        "/etc/passwd",
        "a/b",
        "a\\b",
        # Empty / whitespace.
        "",
        " ",
        "   ",
        # NUL truncates the path at the OS layer.
        "\x00",
        "x\x00y",
        # Windows resolves these before the filesystem does.
        "CON",
        "nul",
        "CoM1",
        "lpt9",
        "AUX",
        "con.1",
        # A leading dot hides the directory; a leading dash reads as a flag.
        ".hidden",
        "-dash",
        # Over the length cap.
        "a" * 65,
        # Non-ASCII: allowed by many filesystems, but normalization differs
        # between them, so two ids could collide after a round-trip.
        "任务",
        # Wrong type entirely — this arrives from a JSON body.
        None,
        42,
        ["x"],
        {"x": 1},
    ],
)
def test_refuses_unusable_ids(job_id: object):
    assert sp.is_safe_job_id(job_id) is False


def test_never_raises_for_any_input():
    """The resolution path returns a message; it must not have to catch."""
    for value in [None, 42, object(), b"bytes", ("a",), "\x00", ".."]:
        assert sp.is_safe_job_id(value) in (True, False)


# ---------------------------------------------------------------------------
# job_scripts_dir / resolve_job_script
# ---------------------------------------------------------------------------


def test_job_scripts_dir_is_under_the_workspace(workspace: Path):
    directory = sp.job_scripts_dir(workspace, JOB_ID)
    assert directory is not None
    assert directory == (workspace / "cron_jobs" / JOB_ID / "batch").resolve()


@pytest.mark.parametrize("job_id", ["..", "a/b", "", None, "CON"])
def test_job_scripts_dir_is_none_for_an_unsafe_id(
    workspace: Path,
    job_id: object,
):
    assert sp.job_scripts_dir(workspace, job_id) is None


def test_resolves_with_and_without_the_suffix(workspace: Path):
    path = make_script(workspace, JOB_ID, "collect.json")
    assert sp.resolve_job_script(workspace, JOB_ID, "collect") == path
    assert sp.resolve_job_script(workspace, JOB_ID, "collect.json") == path


def test_absent_script_resolves_to_none(workspace: Path):
    make_script(workspace, JOB_ID, "collect.json")
    assert sp.resolve_job_script(workspace, JOB_ID, "ghost") is None


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "../../secret",
        "/etc/passwd",
        "a/b.json",
        "a\\b.json",
        "",
        "   ",
        ".",
        "..",
        "x\x00.json",
    ],
)
def test_script_names_cannot_leave_the_job_directory(
    workspace: Path,
    name: str,
):
    """A real file just outside must stay unreachable."""
    make_script(workspace, JOB_ID, "collect.json")
    outside = workspace / "cron_jobs" / JOB_ID / "secret.json"
    outside.write_text("[]", encoding="utf-8")
    (workspace / "secret.json").write_text("[]", encoding="utf-8")

    assert sp.resolve_job_script(workspace, JOB_ID, name) is None


def test_one_job_cannot_read_another_jobs_script(workspace: Path):
    """The whole point of per-job ownership."""
    other = "bbbbbbbb-0000-4000-8000-000000000002"
    make_script(workspace, other, "collect.json")
    assert sp.resolve_job_script(workspace, JOB_ID, "collect") is None
    assert sp.resolve_job_script(workspace, other, "collect") is not None


# ---------------------------------------------------------------------------
# iter_job_script_dirs
# ---------------------------------------------------------------------------


def test_iter_lists_jobs_and_skips_junk(workspace: Path):
    make_script(workspace, JOB_ID, "a.json")
    other = "bbbbbbbb-0000-4000-8000-000000000002"
    make_script(workspace, other, "b.json")
    # Planted out of band: these must not be reported as jobs.
    root = sp.cron_jobs_root(workspace)
    (root / ".hidden" / "batch").mkdir(parents=True)
    (root / "CON" / "batch").mkdir(parents=True)
    (root / "loose.txt").write_text("x", encoding="utf-8")
    # A job directory with no batch/ yet is not yet interesting.
    (root / "cccccccc-0000-4000-8000-000000000003").mkdir()

    assert [job_id for job_id, _ in sp.iter_job_script_dirs(workspace)] == [
        JOB_ID,
        other,
    ]


def test_iter_paths_match_job_scripts_dir(workspace: Path):
    """Callers compare the two; they must not differ in resolved-ness."""
    make_script(workspace, JOB_ID, "a.json")
    listed = dict(sp.iter_job_script_dirs(workspace))
    assert listed[JOB_ID] == sp.job_scripts_dir(workspace, JOB_ID)


def test_iter_is_empty_when_nothing_exists(workspace: Path):
    assert list(sp.iter_job_script_dirs(workspace)) == []
