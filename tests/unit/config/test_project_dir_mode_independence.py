# -*- coding: utf-8 -*-
"""Project directories are mode-independent.

These tests lock in the core invariant of the workspace/project split:
``project_dirs`` belongs to the agent, not to Coding Mode. Toggling Coding
Mode changes which *tools and UI* are available, never which directories
the agent works in.
"""

from __future__ import annotations

import json

import pytest

from qwenpaw.config.config import AgentProfileConfig, ProjectDirEntry
from qwenpaw.config.project_dir import (
    agent_primary_project_dir_from_config,
    agent_project_dirs_from_config,
)


class TestSchema:
    def test_project_dirs_is_a_top_level_field(self):
        config = AgentProfileConfig(id="a", name="A")
        assert "project_dirs" in type(config).model_fields

    def test_defaults_to_empty_list(self):
        config = AgentProfileConfig(id="a", name="A")
        assert config.project_dirs == []

    def test_entry_carries_path_and_optional_label(self):
        entry = ProjectDirEntry(path="/p", label="backend")
        assert entry.path == "/p"
        assert entry.label == "backend"
        assert ProjectDirEntry(path="/p").label is None

    def test_label_length_is_capped(self):
        with pytest.raises(ValueError):
            ProjectDirEntry(path="/p", label="x" * 51)

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
            project_dirs=[
                ProjectDirEntry(path=str(tmp_path), label="main"),
                ProjectDirEntry(path="/tmp/other"),
            ],
        )
        payload = json.loads(config.model_dump_json())
        assert payload["project_dirs"][0]["path"] == str(tmp_path)
        assert payload["project_dirs"][0]["label"] == "main"

        restored = AgentProfileConfig(**payload)
        assert restored.project_dirs[0].path == str(tmp_path)
        assert restored.project_dirs[1].label is None


class TestIndependenceFromCodingMode:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_resolved_the_same_whether_coding_mode_is_on(self, enabled):
        config = AgentProfileConfig(id="a", name="A")
        config.project_dirs = [ProjectDirEntry(path="/repos/demo")]
        config.coding_mode.enabled = enabled

        assert agent_primary_project_dir_from_config(config) == "/repos/demo"
        assert len(agent_project_dirs_from_config(config)) == 1

    def test_toggling_coding_mode_does_not_change_the_dirs(self):
        """The toggle must only flip ``enabled``."""
        config = AgentProfileConfig(id="a", name="A")
        config.project_dirs = [ProjectDirEntry(path="/repos/demo")]

        config.coding_mode.enabled = True
        after_on = agent_project_dirs_from_config(config)
        config.coding_mode.enabled = False
        after_off = agent_project_dirs_from_config(config)

        assert after_on == after_off
        assert after_on[0]["path"] == "/repos/demo"

    def test_project_dirs_usable_with_coding_mode_off(self):
        """Normal mode gets project dirs too — that is the whole point."""
        config = AgentProfileConfig(id="a", name="A")
        config.project_dirs = [ProjectDirEntry(path="/repos/demo")]
        config.coding_mode.enabled = False

        assert config.coding_mode.enabled is False
        assert agent_primary_project_dir_from_config(config) == "/repos/demo"


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

    def test_dirs_list_var_tracks_all_bound_directories(self):
        """Governance and prompts need the whole granted set, not just
        the primary."""
        from pathlib import Path

        from qwenpaw.config.context import (
            get_all_project_dir_paths,
            set_current_project_dirs,
        )
        from qwenpaw.config.project_dir import ResolvedProjectDir

        set_current_project_dirs(
            (
                ResolvedProjectDir(path=Path("/p1")),
                ResolvedProjectDir(path=Path("/p2"), label="extra"),
            ),
        )
        try:
            assert get_all_project_dir_paths() == [
                Path("/p1"),
                Path("/p2"),
            ]
        finally:
            set_current_project_dirs(None)
