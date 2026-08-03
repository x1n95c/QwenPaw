# -*- coding: utf-8 -*-
"""Effective project-directory resolution, normalization and migration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from qwenpaw.config.project_dir import (
    SOURCE_AGENT,
    SOURCE_FORK,
    SOURCE_MODE,
    SOURCE_REQUEST,
    SOURCE_SESSION,
    SOURCE_WORKSPACE_FALLBACK,
    agent_project_dir_from_config,
    describe_for_audit,
    is_within,
    migrate_project_dir_in_place,
    normalize_project_dir,
    resolve_effective_project_dir,
    same_dir,
    session_project_dir_from_meta,
)

_WS = "/tmp/qwenpaw-test-ws"


class TestResolverPrecedence:
    """fork > mode > request > session > agent > workspace_fallback."""

    def test_nothing_configured_falls_back_to_workspace(self):
        resolved = resolve_effective_project_dir(workspace_dir=_WS)
        assert resolved.source == SOURCE_WORKSPACE_FALLBACK
        assert str(resolved.path) == _WS
        assert resolved.is_workspace_fallback is True

    def test_agent_default_beats_fallback(self):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir="/tmp/agent-proj",
        )
        assert resolved.source == SOURCE_AGENT
        assert str(resolved.path) == "/tmp/agent-proj"

    def test_session_beats_agent(self):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir="/tmp/agent-proj",
            session_project_dir="/tmp/session-proj",
        )
        assert resolved.source == SOURCE_SESSION
        assert str(resolved.path) == "/tmp/session-proj"

    def test_request_beats_session(self):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir="/tmp/agent-proj",
            session_project_dir="/tmp/session-proj",
            request_override="/tmp/acp-proj",
        )
        assert resolved.source == SOURCE_REQUEST
        assert str(resolved.path) == "/tmp/acp-proj"

    def test_mode_beats_request(self):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir="/tmp/agent-proj",
            session_project_dir="/tmp/session-proj",
            request_override="/tmp/acp-proj",
            mode_override="/tmp/mission-proj",
        )
        assert resolved.source == SOURCE_MODE
        assert str(resolved.path) == "/tmp/mission-proj"

    def test_fork_beats_everything(self):
        """The fork worktree is a sandbox boundary, so it must win.

        If a session or agent value could override it, a forked sub-agent
        would be able to write outside the worktree it was assigned.
        """
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir="/tmp/agent-proj",
            session_project_dir="/tmp/session-proj",
            request_override="/tmp/acp-proj",
            mode_override="/tmp/mission-proj",
            fork_project_dir="/tmp/worktree",
        )
        assert resolved.source == SOURCE_FORK
        assert str(resolved.path) == "/tmp/worktree"

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_values_are_skipped_not_used_as_paths(self, blank):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir=blank,
            session_project_dir=blank,
        )
        assert resolved.source == SOURCE_WORKSPACE_FALLBACK

    def test_missing_workspace_raises(self):
        """Without a fallback we must fail loudly.

        Silently using the process cwd would let agent state escape into
        whatever directory the server happened to start in.
        """
        with pytest.raises(ValueError, match="workspace_dir"):
            resolve_effective_project_dir(workspace_dir="")

    def test_nonexistent_dir_is_reported_not_swallowed(self):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir="/tmp/definitely-not-here-12345",
        )
        assert resolved.source == SOURCE_AGENT
        assert resolved.exists is False

    def test_existing_dir_reports_exists(self, tmp_path):
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir=str(tmp_path),
        )
        assert resolved.exists is True

    def test_file_is_not_a_valid_project_dir(self, tmp_path):
        target = tmp_path / "a-file.txt"
        target.write_text("x", encoding="utf-8")
        resolved = resolve_effective_project_dir(
            workspace_dir=_WS,
            agent_project_dir=str(target),
        )
        assert resolved.exists is False


class TestNormalize:
    @pytest.mark.parametrize("blank", [None, "", "   ", "\t"])
    def test_blank_becomes_none(self, blank):
        assert normalize_project_dir(blank) is None

    def test_tilde_is_expanded(self):
        result = normalize_project_dir("~/some-project")
        assert result is not None
        assert "~" not in str(result)
        assert result.is_absolute()

    def test_relative_becomes_absolute(self):
        result = normalize_project_dir("some/relative/path")
        assert result is not None
        assert result.is_absolute()

    def test_dotdot_is_collapsed(self):
        assert str(normalize_project_dir("/a/b/../c")) == str(Path("/a/c"))

    def test_missing_path_still_normalizes(self):
        """A configured-but-missing dir must survive round-trips.

        Returning None here would silently reset the user's config instead
        of letting the UI flag the path as unavailable.
        """
        assert normalize_project_dir("/no/such/dir/anywhere") is not None

    def test_accepts_path_objects(self, tmp_path):
        assert normalize_project_dir(tmp_path) == tmp_path


class TestSameDir:
    def test_identical_paths_match(self):
        assert same_dir("/repo", "/repo") is True

    def test_normalizes_before_comparing(self):
        assert same_dir("/repo/", "/repo/../repo") is True

    def test_different_paths_do_not_match(self):
        assert same_dir("/repo", "/other") is False

    def test_both_none_match(self):
        assert same_dir(None, None) is True

    def test_one_none_does_not_match(self):
        assert same_dir("/repo", None) is False

    @pytest.mark.skipif(
        sys.platform not in ("win32", "darwin"),
        reason="only macOS/Windows default to case-insensitive filesystems",
    )
    def test_case_insensitive_on_mac_and_windows(self):
        """Otherwise one repo would get two Git watchdogs."""
        assert same_dir("/Repo/Project", "/repo/project") is True

    @pytest.mark.skipif(
        sys.platform in ("win32", "darwin"),
        reason="Linux filesystems are case-sensitive",
    )
    def test_case_sensitive_on_linux(self):
        assert same_dir("/Repo/Project", "/repo/project") is False


class TestIsWithin:
    def test_child_is_within_parent(self):
        assert is_within("/repo/src/main.py", "/repo") is True

    def test_same_dir_counts_as_within(self):
        assert is_within("/repo", "/repo") is True

    def test_sibling_prefix_is_not_within(self):
        """``startswith`` would wrongly say yes here."""
        assert is_within("/repo-backup", "/repo") is False
        assert is_within("/repo-backup/file.txt", "/repo") is False

    def test_parent_is_not_within_child(self):
        assert is_within("/repo", "/repo/src") is False

    def test_none_is_never_within(self):
        assert is_within(None, "/repo") is False
        assert is_within("/repo", None) is False


class TestAgentProjectDirFromConfig:
    def test_none_config(self):
        assert agent_project_dir_from_config(None) is None

    def test_dict_top_level(self):
        assert (
            agent_project_dir_from_config({"project_dir": "/p"}) == "/p"
        )

    def test_dict_legacy_fallback(self):
        config = {"coding_mode": {"project_dir": "/legacy"}}
        assert agent_project_dir_from_config(config) == "/legacy"

    def test_dict_top_level_wins(self):
        config = {
            "project_dir": "/new",
            "coding_mode": {"project_dir": "/old"},
        }
        assert agent_project_dir_from_config(config) == "/new"

    def test_dict_without_either(self):
        assert agent_project_dir_from_config({"id": "x"}) is None

    def test_object_top_level(self):
        class Cfg:
            project_dir = "/p"
            coding_mode = None

        assert agent_project_dir_from_config(Cfg()) == "/p"

    def test_object_legacy_fallback(self):
        class Cm:
            project_dir = "/legacy"

        class Cfg:
            project_dir = None
            coding_mode = Cm()

        assert agent_project_dir_from_config(Cfg()) == "/legacy"


class TestSessionProjectDirFromMeta:
    def test_reads_controlled_namespace(self):
        meta = {"runtime_context": {"project_dir": "/session-proj"}}
        assert session_project_dir_from_meta(meta) == "/session-proj"

    def test_top_level_meta_key_is_ignored(self):
        """Only the controlled namespace counts.

        A generic meta patch from a client must not be able to set the
        project dir as a side effect.
        """
        assert session_project_dir_from_meta({"project_dir": "/x"}) is None

    @pytest.mark.parametrize(
        "meta",
        [
            None,
            {},
            "not-a-dict",
            {"runtime_context": None},
            {"runtime_context": "not-a-dict"},
            {"runtime_context": {}},
            {"runtime_context": {"project_dir": ""}},
        ],
    )
    def test_malformed_meta_returns_none(self, meta):
        assert session_project_dir_from_meta(meta) is None


class TestMigration:
    def test_lifts_legacy_value(self):
        data = {"coding_mode": {"enabled": True, "project_dir": "/legacy"}}
        assert migrate_project_dir_in_place(data) is True
        assert data["project_dir"] == "/legacy"
        assert "project_dir" not in data["coding_mode"]
        assert data["coding_mode"]["enabled"] is True

    def test_is_idempotent(self):
        data = {"coding_mode": {"project_dir": "/legacy"}}
        assert migrate_project_dir_in_place(data) is True
        assert migrate_project_dir_in_place(data) is False

    def test_top_level_wins_and_legacy_dropped(self):
        data = {
            "project_dir": "/new",
            "coding_mode": {"project_dir": "/old"},
        }
        assert migrate_project_dir_in_place(data) is True
        assert data["project_dir"] == "/new"
        assert "project_dir" not in data["coding_mode"]

    def test_no_legacy_key_is_noop(self):
        data = {"project_dir": "/new", "coding_mode": {"enabled": True}}
        assert migrate_project_dir_in_place(data) is False
        assert data["project_dir"] == "/new"

    def test_no_coding_mode_is_noop(self):
        data = {"id": "default"}
        assert migrate_project_dir_in_place(data) is False
        assert "project_dir" not in data

    def test_null_legacy_value_is_just_dropped(self):
        data = {"coding_mode": {"project_dir": None}}
        assert migrate_project_dir_in_place(data) is True
        assert "project_dir" not in data["coding_mode"]
        assert data.get("project_dir") is None

    def test_missing_path_is_preserved_not_reset(self):
        """A stale path must migrate too, so the UI can flag it."""
        data = {"coding_mode": {"project_dir": "/gone/missing/12345"}}
        assert migrate_project_dir_in_place(data) is True
        assert data["project_dir"] == str(Path("/gone/missing/12345"))

    def test_value_is_normalized_on_migration(self):
        data = {"coding_mode": {"project_dir": "/a/b/../c"}}
        migrate_project_dir_in_place(data)
        assert data["project_dir"] == str(Path("/a/c"))

    def test_non_dict_coding_mode_is_ignored(self):
        data = {"coding_mode": "nonsense"}
        assert migrate_project_dir_in_place(data) is False


class TestDescribeForAudit:
    def test_records_both_dirs_and_provenance(self, tmp_path):
        resolved = resolve_effective_project_dir(
            workspace_dir=str(tmp_path),
            agent_project_dir=str(tmp_path),
        )
        record = describe_for_audit(resolved, str(tmp_path))
        assert record["workspace_dir"] == str(tmp_path)
        assert record["project_dir"] == str(tmp_path)
        assert record["project_dir_source"] == SOURCE_AGENT
        assert record["project_dir_exists"] is True
