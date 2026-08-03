# -*- coding: utf-8 -*-
"""Session-scoped Coding Mode project overrides."""

from __future__ import annotations

# Tests target request-scope helpers directly.
# pylint: disable=protected-access

import pytest

from qwenpaw.agents.acp.meta import ACP_CODING_PROJECT_META_KEY
from qwenpaw.config.config import AgentProfileConfig
from qwenpaw.runtime.builder import AgentBuilder


def test_request_coding_project_enables_clone(tmp_path):
    """An ACP project override lands on the top-level project_dir of a copy."""
    config = AgentProfileConfig(id="default", name="Default")

    updated = AgentBuilder._apply_request_coding_project(
        config,
        {ACP_CODING_PROJECT_META_KEY: str(tmp_path)},
    )

    assert updated is not config
    assert updated.coding_mode.enabled is True
    assert updated.project_dir == str(tmp_path.resolve())
    # The caller's config must be untouched: a per-request override must
    # never be persisted as the agent's saved default.
    assert config.coding_mode.enabled is False
    assert config.project_dir is None


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


def test_agent_project_dir_read_from_top_level(tmp_path):
    """The resolver helper reads the mode-independent top-level field."""
    from qwenpaw.config.project_dir import agent_project_dir_from_config

    config = AgentProfileConfig(id="default", name="Default")
    config.project_dir = str(tmp_path)

    assert agent_project_dir_from_config(config) == str(tmp_path)


def test_agent_project_dir_falls_back_to_legacy_coding_mode(tmp_path):
    """Un-migrated configs still resolve via the legacy nested field."""
    from qwenpaw.config.project_dir import agent_project_dir_from_config

    config = AgentProfileConfig(id="default", name="Default")
    config.coding_mode.project_dir = str(tmp_path)

    assert agent_project_dir_from_config(config) == str(tmp_path)


def test_top_level_project_dir_wins_over_legacy(tmp_path):
    """When both are present the migrated top-level value is authoritative."""
    from qwenpaw.config.project_dir import agent_project_dir_from_config

    config = AgentProfileConfig(id="default", name="Default")
    config.project_dir = str(tmp_path / "new")
    config.coding_mode.project_dir = str(tmp_path / "old")

    assert agent_project_dir_from_config(config) == str(tmp_path / "new")
