# -*- coding: utf-8 -*-
"""Where a fork's worktree gets checked out.

The checkout belongs to the agent workspace, not the project: one fork is
a full copy of the repository, and they are never cleaned up, so putting
them in the user's working tree accumulated 42 GB of dead checkouts in
practice. What *cannot* move is the branch ref and
``.git/worktrees/<id>`` — a linked worktree is by definition a worktree of
that repository.
"""
# pylint: disable=protected-access
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qwenpaw.app.routers.fork import _create_worktree


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-m", "init")


@pytest.mark.asyncio
async def test_checkout_lands_in_the_workspace(tmp_path: Path) -> None:
    project = tmp_path / "code_proj"
    workspace = tmp_path / "agent_ws"
    _init_repo(project)
    workspace.mkdir()

    wt_path, branch = await _create_worktree(project, workspace, "abc123")

    assert wt_path == workspace / ".qwenpaw" / "worktrees" / "abc123"
    assert wt_path.is_dir()
    assert branch == "fork/abc123"
    # Nothing added under the project's working tree.
    assert not (project / ".qwenpaw").exists()
    # The checkout is real, not an empty directory.
    assert (wt_path / "README").is_file()


@pytest.mark.asyncio
async def test_branch_still_belongs_to_the_project_repo(
    tmp_path: Path,
) -> None:
    """The ref cannot move to the workspace, and must not be expected to."""
    project = tmp_path / "code_proj"
    workspace = tmp_path / "agent_ws"
    _init_repo(project)
    workspace.mkdir()

    wt_path, branch = await _create_worktree(project, workspace, "abc123")

    branches = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=str(project),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branch in branches
    # git also tracks the worktree from the project side.
    listed = subprocess.run(
        ["git", "worktree", "list"],
        cwd=str(project),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(wt_path.resolve()) in listed


@pytest.mark.asyncio
async def test_worktreeinclude_files_come_from_the_project(
    tmp_path: Path,
) -> None:
    """The include list is project configuration, so it is read there."""
    project = tmp_path / "code_proj"
    workspace = tmp_path / "agent_ws"
    _init_repo(project)
    workspace.mkdir()
    (project / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    (project / ".worktreeinclude").write_text(
        ".env.local\n",
        encoding="utf-8",
    )

    wt_path, _branch = await _create_worktree(project, workspace, "abc123")

    assert (wt_path / ".env.local").read_text(encoding="utf-8") == "SECRET=1\n"
