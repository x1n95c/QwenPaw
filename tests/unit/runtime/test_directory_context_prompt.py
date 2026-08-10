# -*- coding: utf-8 -*-
"""The Directories prompt block renders the whole project-dir list.

The model must know about every bound directory (primary marked, labels
included, absolute-path guidance for the extras), otherwise it cannot use
the directories that governance already grants.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.config.context import (
    set_current_project_dir,
    set_current_project_dir_source,
    set_current_project_dirs,
)
from qwenpaw.config.project_dir import ResolvedProjectDir
from qwenpaw.runtime.prompt_contributors import DirectoryContextContributor


@pytest.fixture(autouse=True)
def _reset_contextvars():
    yield
    set_current_project_dir(None)
    set_current_project_dir_source(None)
    set_current_project_dirs(None)


def _ctx(workspace_dir: str) -> SimpleNamespace:
    return SimpleNamespace(workspace_dir=workspace_dir)


def _contribute(workspace_dir: str) -> str | None:
    return DirectoryContextContributor().contribute_sync(_ctx(workspace_dir))


def test_single_dir_renders_primary_marker(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    set_current_project_dir(project)
    set_current_project_dir_source("agent")
    set_current_project_dirs((ResolvedProjectDir(path=project),))

    block = _contribute(str(tmp_path))

    assert block is not None
    assert f"Project directory (primary): {project}" in block
    # No multi-dir guidance for a single directory.
    assert "ABSOLUTE path" not in block


def test_multiple_dirs_render_numbered_list_with_labels(tmp_path):
    main = tmp_path / "main-app"
    backend = tmp_path / "backend"
    main.mkdir()
    backend.mkdir()
    set_current_project_dir(main)
    set_current_project_dir_source("session")
    set_current_project_dirs(
        (
            ResolvedProjectDir(path=main),
            ResolvedProjectDir(path=backend, label="backend API"),
        ),
    )

    block = _contribute(str(tmp_path))

    assert block is not None
    assert "Project directories:" in block
    assert f"1. {main} (primary)" in block
    assert f"2. {backend} — backend API" in block
    # The model must be told relative paths do NOT resolve in extras.
    assert "ABSOLUTE path" in block


def test_missing_dir_is_flagged_in_the_list(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    gone = tmp_path / "gone"
    set_current_project_dir(main)
    set_current_project_dir_source("session")
    set_current_project_dirs(
        (
            ResolvedProjectDir(path=main),
            ResolvedProjectDir(path=gone, exists=False),
        ),
    )

    block = _contribute(str(tmp_path))

    assert block is not None
    assert f"2. {gone} [MISSING]" in block


def test_workspace_fallback_keeps_single_working_dir_block(tmp_path):
    """When nothing is configured the workspace must not be presented as
    a project directory under a second label."""
    set_current_project_dir(tmp_path)
    set_current_project_dir_source("workspace_fallback")
    set_current_project_dirs(())

    block = _contribute(str(tmp_path))

    assert block is not None
    assert f"Working directory: {tmp_path}" in block
    assert "Project director" not in block


def test_missing_primary_keeps_the_warning(tmp_path):
    gone = tmp_path / "gone"
    set_current_project_dir(gone)
    set_current_project_dir_source("agent")
    set_current_project_dirs((ResolvedProjectDir(path=gone, exists=False),))

    block = _contribute(str(tmp_path))

    assert block is not None
    assert "does not currently" in block
