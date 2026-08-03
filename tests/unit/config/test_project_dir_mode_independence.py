# -*- coding: utf-8 -*-
"""The project dir is mode-independent.

These tests lock in the core invariant of the workspace/project split:
``project_dir`` belongs to the agent, not to Coding Mode. Toggling Coding
Mode changes which *tools and UI* are available, never which directory the
agent works in.
"""

from __future__ import annotations

import json

import pytest

from qwenpaw.config.config import AgentProfileConfig
from qwenpaw.config.project_dir import agent_project_dir_from_config


class TestSchema:
    def test_project_dir_is_a_top_level_field(self):
        config = AgentProfileConfig(id="a", name="A")
        assert "project_dir" in type(config).model_fields

    def test_defaults_to_none(self):
        config = AgentProfileConfig(id="a", name="A")
        assert config.project_dir is None

    def test_legacy_field_is_marked_deprecated(self):
        """Kept only so existing agent.json still parses."""
        field = type(
            AgentProfileConfig(id="a", name="A").coding_mode,
        ).model_fields["project_dir"]
        assert field.deprecated

    def test_survives_a_json_round_trip(self, tmp_path):
        config = AgentProfileConfig(
            id="a",
            name="A",
            project_dir=str(tmp_path),
        )
        payload = json.loads(config.model_dump_json())
        assert payload["project_dir"] == str(tmp_path)

        restored = AgentProfileConfig(**payload)
        assert restored.project_dir == str(tmp_path)


class TestIndependenceFromCodingMode:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_resolved_the_same_whether_coding_mode_is_on(self, enabled):
        config = AgentProfileConfig(id="a", name="A")
        config.project_dir = "/repos/demo"
        config.coding_mode.enabled = enabled

        assert agent_project_dir_from_config(config) == "/repos/demo"

    def test_toggling_coding_mode_does_not_change_the_dir(self):
        """The toggle must only flip ``enabled``."""
        config = AgentProfileConfig(id="a", name="A")
        config.project_dir = "/repos/demo"

        config.coding_mode.enabled = True
        after_on = config.project_dir
        config.coding_mode.enabled = False
        after_off = config.project_dir

        assert after_on == "/repos/demo"
        assert after_off == "/repos/demo"

    def test_project_dir_usable_with_coding_mode_off(self):
        """Normal mode gets a project dir too — that is the whole point."""
        config = AgentProfileConfig(id="a", name="A")
        config.project_dir = "/repos/demo"
        config.coding_mode.enabled = False

        assert config.coding_mode.enabled is False
        assert agent_project_dir_from_config(config) == "/repos/demo"


class TestToolBaseDirFallback:
    def test_falls_back_to_workspace_then_working_dir(self):
        from pathlib import Path

        from qwenpaw.config.context import (
            get_tool_base_dir,
            set_current_project_dir,
            set_current_workspace_dir,
        )
        from qwenpaw.constant import WORKING_DIR

        set_current_project_dir(None)
        set_current_workspace_dir(None)
        try:
            assert get_tool_base_dir() == WORKING_DIR

            set_current_workspace_dir(Path("/ws"))
            assert get_tool_base_dir() == Path("/ws")

            set_current_project_dir(Path("/proj"))
            assert get_tool_base_dir() == Path("/proj")
        finally:
            set_current_project_dir(None)
            set_current_workspace_dir(None)

    def test_workspace_var_is_independent_of_project_var(self):
        """Repointing the workspace var would leak agent state into repos.

        Memory, skills, sessions, cache, approvals and audit all resolve
        from ``current_workspace_dir``; the old code repointed it to
        simulate a project switch, which is exactly what this prevents.
        """
        from pathlib import Path

        from qwenpaw.config.context import (
            get_current_workspace_dir,
            get_tool_base_dir,
            set_current_project_dir,
            set_current_workspace_dir,
        )

        set_current_workspace_dir(Path("/ws"))
        set_current_project_dir(Path("/proj"))
        try:
            assert get_current_workspace_dir() == Path("/ws")
            assert get_tool_base_dir() == Path("/proj")
            assert get_current_workspace_dir() != get_tool_base_dir()
        finally:
            set_current_project_dir(None)
            set_current_workspace_dir(None)
