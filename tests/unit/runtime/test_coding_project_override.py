# -*- coding: utf-8 -*-
"""Session-scoped Coding Mode project overrides."""

from __future__ import annotations

# Tests target request-scope helpers directly.
# pylint: disable=protected-access

import pytest

from qwenpaw.agents.acp.meta import ACP_CODING_PROJECT_META_KEY
from qwenpaw.config.config import AgentProfileConfig, ProjectDirEntry
from qwenpaw.runtime.builder import AgentBuilder


def test_request_coding_project_enables_clone(tmp_path):
    """An ACP project override becomes the PRIMARY entry of a copy."""
    config = AgentProfileConfig(id="default", name="Default")

    updated = AgentBuilder._apply_request_coding_project(
        config,
        {ACP_CODING_PROJECT_META_KEY: str(tmp_path)},
    )

    assert updated is not config
    assert updated.coding_mode.enabled is True
    assert [entry.path for entry in updated.project_dirs] == [
        str(tmp_path.resolve()),
    ]
    # The caller's config must be untouched: a per-request override must
    # never be persisted as the agent's saved default.
    assert config.coding_mode.enabled is False
    assert config.project_dirs == []


def test_request_override_becomes_primary_and_keeps_others(tmp_path):
    """The override moves to the front; other bound dirs stay usable."""
    other = tmp_path / "other"
    other.mkdir()
    config = AgentProfileConfig(
        id="default",
        name="Default",
        project_dirs=[
            ProjectDirEntry(path=str(other), label="extra"),
        ],
    )

    updated = AgentBuilder._apply_request_coding_project(
        config,
        {ACP_CODING_PROJECT_META_KEY: str(tmp_path)},
    )

    assert [entry.path for entry in updated.project_dirs] == [
        str(tmp_path.resolve()),
        str(other.resolve()),
    ]
    # The surviving entry keeps its label.
    assert updated.project_dirs[1].label == "extra"


def test_request_override_dedupes_an_existing_entry(tmp_path):
    config = AgentProfileConfig(
        id="default",
        name="Default",
        project_dirs=[ProjectDirEntry(path=str(tmp_path.resolve()))],
    )

    updated = AgentBuilder._apply_request_coding_project(
        config,
        {ACP_CODING_PROJECT_META_KEY: str(tmp_path)},
    )

    assert len(updated.project_dirs) == 1


def test_request_coding_project_ignores_non_directory(tmp_path):
    config = AgentProfileConfig(id="default", name="Default")

    updated = AgentBuilder._apply_request_coding_project(
        config,
        {ACP_CODING_PROJECT_META_KEY: str(tmp_path / "missing")},
    )

    assert updated is config
    assert config.coding_mode.enabled is False


@pytest.mark.usefixtures("capture_qwenpaw_logs")
def test_request_coding_project_warns_for_unsupported_config(
    caplog,
    tmp_path,
):
    config = {}

    updated = AgentBuilder._apply_request_coding_project(
        config,
        {ACP_CODING_PROJECT_META_KEY: str(tmp_path)},
    )

    assert updated is config
    assert "unsupported config type: dict" in caplog.text


def test_agent_project_dirs_read_from_top_level(tmp_path):
    """The resolver helper reads the mode-independent top-level list."""
    from qwenpaw.config.project_dir import (
        agent_primary_project_dir_from_config,
        agent_project_dirs_from_config,
    )

    config = AgentProfileConfig(id="default", name="Default")
    config.project_dirs = [
        ProjectDirEntry(path=str(tmp_path), label="main"),
    ]

    assert agent_primary_project_dir_from_config(config) == str(tmp_path)
    assert agent_project_dirs_from_config(config) == [
        {"path": str(tmp_path), "label": "main"},
    ]
