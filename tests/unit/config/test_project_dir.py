# -*- coding: utf-8 -*-
"""Effective project-directory LIST resolution, normalization, migration.

The model: an ordered list where index 0 is the PRIMARY directory
(relative paths / shell cwd resolve there) and the rest are extra
directories granted by governance and addressed by absolute path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from qwenpaw.config.project_dir import (
    MAX_PROJECT_DIRS,
    SOURCE_AGENT,
    SOURCE_FORK,
    SOURCE_MODE,
    SOURCE_REQUEST,
    SOURCE_SESSION,
    SOURCE_WORKSPACE_FALLBACK,
    agent_primary_project_dir_from_config,
    agent_project_dirs_from_config,
    describe_for_audit,
    is_within,
    migrate_project_dirs_in_place,
    normalize_project_dir,
    normalize_project_dir_list,
    resolve_effective_project_dirs,
    same_dir,
    session_project_dirs_from_meta,
)

_WS = "/tmp/qwenpaw-test-ws"


def _paths(resolved) -> list[str]:
    return [str(entry.path) for entry in resolved.dirs]


class TestResolverPrecedence:
    """fork > mode > request > session > agent > workspace_fallback."""

    def test_nothing_configured_falls_back_to_workspace(self):
        resolved = resolve_effective_project_dirs(workspace_dir=_WS)
        assert resolved.source == SOURCE_WORKSPACE_FALLBACK
        assert resolved.is_workspace_fallback is True
        assert resolved.dirs == ()
        # The primary is the workspace, but it is NOT listed as a
        # project dir — tools fall back, the UI shows the empty state.
        assert str(resolved.primary_path) == _WS

    def test_agent_default_beats_fallback(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/agent-proj"}],
        )
        assert resolved.source == SOURCE_AGENT
        assert _paths(resolved) == [str(Path("/tmp/agent-proj"))]

    def test_session_beats_agent_wholesale(self):
        """Session override replaces the whole list — no merging."""
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[
                {"path": "/tmp/agent-a"},
                {"path": "/tmp/agent-b"},
            ],
            session_project_dirs=[{"path": "/tmp/session-proj"}],
        )
        assert resolved.source == SOURCE_SESSION
        assert _paths(resolved) == [str(Path("/tmp/session-proj"))]

    def test_empty_session_list_means_explicitly_none(self):
        """[] is not the same as absent: it clears the override target."""
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/agent-proj"}],
            session_project_dirs=[],
        )
        assert resolved.source == SOURCE_SESSION
        assert resolved.dirs == ()

    def test_request_becomes_primary_and_keeps_rest(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/agent-proj"}],
            session_project_dirs=[{"path": "/tmp/session-proj"}],
            request_override="/tmp/acp-proj",
        )
        assert resolved.source == SOURCE_REQUEST
        assert _paths(resolved) == [
            str(Path("/tmp/acp-proj")),
            str(Path("/tmp/session-proj")),
        ]

    def test_mode_pin_replaces_the_whole_list(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/agent-proj"}],
            session_project_dirs=[{"path": "/tmp/session-proj"}],
            request_override="/tmp/acp-proj",
            mode_override=[{"path": "/tmp/mission-proj"}],
        )
        assert resolved.source == SOURCE_MODE
        assert _paths(resolved) == [str(Path("/tmp/mission-proj"))]

    def test_fork_replaces_primary_but_keeps_rest(self):
        """The fork worktree is a sandbox boundary, so it must win the
        primary slot. The remaining entries are user-configured trusted
        paths and stay accessible.
        """
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/agent-proj"}],
            session_project_dirs=[{"path": "/tmp/session-proj"}],
            request_override="/tmp/acp-proj",
            mode_override=[{"path": "/tmp/mission-proj"}],
            fork_project_dir="/tmp/worktree",
        )
        assert resolved.source == SOURCE_FORK
        assert _paths(resolved) == [
            str(Path("/tmp/worktree")),
            str(Path("/tmp/mission-proj")),
        ]

    def test_fork_dedupes_itself_out_of_the_rest(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/proj"}],
            fork_project_dir="/tmp/proj",
        )
        assert _paths(resolved) == [str(Path("/tmp/proj"))]

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_values_are_skipped_not_used_as_paths(self, blank):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": blank}] if blank else [],
            session_project_dirs=None,
        )
        assert resolved.source == SOURCE_WORKSPACE_FALLBACK

    def test_missing_workspace_raises(self):
        """Without a fallback we must fail loudly.

        Silently using the process cwd would let agent state escape into
        whatever directory the server happened to start in.
        """
        with pytest.raises(ValueError, match="workspace_dir"):
            resolve_effective_project_dirs(workspace_dir="")

    def test_missing_dir_is_reported_not_swallowed(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": "/tmp/definitely-not-here-12345"}],
        )
        assert resolved.source == SOURCE_AGENT
        assert resolved.dirs[0].exists is False
        assert len(resolved.dirs) == 1

    def test_existing_dir_reports_exists(self, tmp_path):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[{"path": str(tmp_path)}],
        )
        assert resolved.dirs[0].exists is True

    def test_labels_survive_resolution(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[
                {"path": "/tmp/a", "label": "backend"},
                {"path": "/tmp/b"},
            ],
        )
        assert resolved.dirs[0].label == "backend"
        assert resolved.dirs[1].label is None

    def test_primary_property_matches_first_entry(self):
        resolved = resolve_effective_project_dirs(
            workspace_dir=_WS,
            agent_project_dirs=[
                {"path": "/tmp/first"},
                {"path": "/tmp/second"},
            ],
        )
        assert resolved.primary.path == resolved.dirs[0].path
        assert str(resolved.primary_path) == str(Path("/tmp/first"))


class TestNormalizeList:
    def test_order_is_preserved_index_zero_is_primary(self):
        entries = normalize_project_dir_list(
            ["/tmp/one", "/tmp/two", "/tmp/three"],
        )
        assert [str(p) for p, _ in entries] == [
            str(Path("/tmp/one")),
            str(Path("/tmp/two")),
            str(Path("/tmp/three")),
        ]

    def test_duplicates_are_dropped_case_insensitively(self):
        entries = normalize_project_dir_list(
            [
                {"path": "/Repo/Main", "label": "first"},
                {"path": "/repo/main", "label": "dup"},
                {"path": "/tmp/other"},
            ],
        )
        assert len(entries) == 2
        # The first occurrence keeps its label.
        assert entries[0][1] == "first"

    def test_accepts_strings_dicts_tuples_and_objects(self):
        class Entry:
            path = "/tmp/obj"
            label = "from-object"

        entries = normalize_project_dir_list(
            [
                "/tmp/str",
                {"path": "/tmp/dict", "label": "d"},
                ("/tmp/tuple", "t"),
                Entry(),
            ],
        )
        assert [str(p) for p, _ in entries] == [
            str(Path("/tmp/str")),
            str(Path("/tmp/dict")),
            str(Path("/tmp/tuple")),
            str(Path("/tmp/obj")),
        ]
        assert entries[1][1] == "d"
        assert entries[3][1] == "from-object"

    def test_blank_entries_are_dropped(self):
        entries = normalize_project_dir_list(["", "  ", None, "/tmp/ok"])
        assert len(entries) == 1

    def test_cap_enforced(self):
        raw = [f"/tmp/proj-{i}" for i in range(MAX_PROJECT_DIRS + 5)]
        entries = normalize_project_dir_list(raw)
        assert len(entries) == MAX_PROJECT_DIRS

    def test_labels_are_trimmed_and_capped(self):
        entries = normalize_project_dir_list(
            [{"path": "/tmp/x", "label": "  "}],
        )
        assert entries[0][1] is None
        entries = normalize_project_dir_list(
            [{"path": "/tmp/x", "label": "y" * 80}],
        )
        assert len(entries[0][1]) == 50

    def test_none_is_empty_list(self):
        assert normalize_project_dir_list(None) == []


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


class TestAgentProjectDirsFromConfig:
    def test_none_config(self):
        assert agent_project_dirs_from_config(None) == []
        assert agent_primary_project_dir_from_config(None) is None

    def test_reads_project_dirs_list(self):
        config = {
            "project_dirs": [
                {"path": "/p1", "label": "main"},
                {"path": "/p2"},
            ],
        }
        entries = agent_project_dirs_from_config(config)
        assert entries == [
            {"path": str(Path("/p1")), "label": "main"},
            {"path": str(Path("/p2")), "label": None},
        ]
        assert agent_primary_project_dir_from_config(config) == str(
            Path("/p1"),
        )

    def test_accepts_plain_string_entries(self):
        config = {"project_dirs": ["/only"]}
        assert agent_primary_project_dir_from_config(config) == str(
            Path("/only"),
        )

    def test_empty_and_missing(self):
        assert agent_project_dirs_from_config({"id": "x"}) == []
        assert agent_project_dirs_from_config({"project_dirs": []}) == []

    def test_object_attribute_access(self):
        class Entry:
            def __init__(self, path, label):
                self.path = path
                self.label = label

        class Cfg:
            project_dirs = [Entry("/p", None)]

        assert agent_primary_project_dir_from_config(Cfg()) == str(
            Path("/p"),
        )

    def test_malformed_entries_are_dropped_not_raised(self):
        config = {"project_dirs": [{"no_path": True}, "/ok"]}
        entries = agent_project_dirs_from_config(config)
        assert len(entries) == 1


class TestSessionProjectDirsFromMeta:
    def test_reads_controlled_namespace(self):
        meta = {
            "runtime_context": {
                "project_dirs": [{"path": "/s1", "label": "x"}],
            },
        }
        assert session_project_dirs_from_meta(meta) == [
            {"path": str(Path("/s1")), "label": "x"},
        ]

    def test_legacy_single_value_is_wrapped_into_a_list(self):
        meta = {"runtime_context": {"project_dir": "/legacy"}}
        assert session_project_dirs_from_meta(meta) == [
            {"path": str(Path("/legacy")), "label": None},
        ]

    def test_list_wins_over_legacy_key(self):
        meta = {
            "runtime_context": {
                "project_dirs": ["/new"],
                "project_dir": "/old",
            },
        }
        result = session_project_dirs_from_meta(meta)
        assert result == [{"path": str(Path("/new")), "label": None}]

    def test_empty_list_is_explicitly_none_not_inherit(self):
        """[] was stored as "explicitly no dirs" and must round-trip."""
        meta = {"runtime_context": {"project_dirs": []}}
        assert session_project_dirs_from_meta(meta) == []

    def test_top_level_meta_key_is_ignored(self):
        """Only the controlled namespace counts.

        A generic meta patch from a client must not be able to set the
        project dirs as a side effect.
        """
        assert session_project_dirs_from_meta({"project_dirs": ["/x"]}) is None

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
        assert session_project_dirs_from_meta(meta) is None


class TestMigration:
    def test_lifts_legacy_coding_mode_value(self):
        data = {"coding_mode": {"enabled": True, "project_dir": "/legacy"}}
        assert migrate_project_dirs_in_place(data) is True
        assert data["project_dirs"] == [
            {"path": str(Path("/legacy")), "label": None},
        ]
        assert "project_dir" not in data["coding_mode"]
        assert data["coding_mode"]["enabled"] is True

    def test_lifts_singular_top_level_value(self):
        data = {"project_dir": "/single"}
        assert migrate_project_dirs_in_place(data) is True
        assert data["project_dirs"] == [
            {"path": str(Path("/single")), "label": None},
        ]
        assert "project_dir" not in data

    def test_is_idempotent(self):
        data = {"coding_mode": {"project_dir": "/legacy"}}
        assert migrate_project_dirs_in_place(data) is True
        assert migrate_project_dirs_in_place(data) is False

    def test_existing_list_wins_and_legacy_dropped(self):
        data = {
            "project_dirs": [{"path": "/new", "label": None}],
            "project_dir": "/draft",
            "coding_mode": {"project_dir": "/old"},
        }
        assert migrate_project_dirs_in_place(data) is True
        assert data["project_dirs"] == [
            {"path": str(Path("/new")), "label": None},
        ]
        assert "project_dir" not in data
        assert "project_dir" not in data["coding_mode"]

    def test_canonical_list_is_noop(self):
        data = {
            "project_dirs": [
                {"path": str(Path("/a")), "label": None},
            ],
        }
        assert migrate_project_dirs_in_place(data) is False

    def test_no_legacy_key_is_noop(self):
        data = {"id": "default"}
        assert migrate_project_dirs_in_place(data) is False
        assert "project_dirs" not in data

    def test_null_legacy_value_is_just_dropped(self):
        data = {"coding_mode": {"project_dir": None}}
        assert migrate_project_dirs_in_place(data) is True
        assert "project_dir" not in data["coding_mode"]
        assert "project_dirs" not in data

    def test_missing_path_is_preserved_not_reset(self):
        """A stale path must migrate too, so the UI can flag it."""
        data = {"coding_mode": {"project_dir": "/gone/missing/12345"}}
        assert migrate_project_dirs_in_place(data) is True
        assert data["project_dirs"] == [
            {"path": str(Path("/gone/missing/12345")), "label": None},
        ]

    def test_value_is_normalized_on_migration(self):
        data = {"coding_mode": {"project_dir": "/a/b/../c"}}
        migrate_project_dirs_in_place(data)
        assert data["project_dirs"] == [
            {"path": str(Path("/a/c")), "label": None},
        ]

    def test_non_dict_coding_mode_is_ignored(self):
        data = {"coding_mode": "nonsense"}
        assert migrate_project_dirs_in_place(data) is False


class TestDescribeForAudit:
    def test_records_dirs_and_provenance(self, tmp_path):
        resolved = resolve_effective_project_dirs(
            workspace_dir=str(tmp_path),
            agent_project_dirs=[
                {"path": str(tmp_path)},
                {"path": "/tmp/other"},
            ],
        )
        record = describe_for_audit(resolved, str(tmp_path))
        assert record["workspace_dir"] == str(tmp_path)
        assert record["project_dir"] == str(tmp_path)
        assert record["project_dir_source"] == SOURCE_AGENT
        assert record["project_dir_exists"] is True
        assert record["project_dirs"] == [str(tmp_path), "/tmp/other"]


class TestProjectName:
    """The project's display name: descriptive only, never a path."""

    def test_derived_from_the_primary_label_then_basename(self):
        from qwenpaw.config.project_dir import default_project_name

        assert (
            default_project_name([{"path": "/repos/app", "label": "My App"}])
            == "My App"
        )
        assert (
            default_project_name([{"path": "/repos/app", "label": None}])
            == "app"
        )

    def test_derived_name_ignores_non_primary_entries(self):
        from qwenpaw.config.project_dir import default_project_name

        entries = [
            {"path": "/repos/main", "label": None},
            {"path": "/repos/other", "label": "Other"},
        ]
        assert default_project_name(entries) == "main"

    def test_no_directories_means_no_name(self):
        from qwenpaw.config.project_dir import default_project_name

        assert default_project_name([]) is None

    def test_precedence_session_then_agent_then_derived(self):
        from qwenpaw.config.project_dir import resolve_project_name

        entries = [{"path": "/repos/app", "label": None}]
        assert (
            resolve_project_name(
                entries=entries,
                session_name="Session",
                agent_name="Agent",
            )
            == "Session"
        )
        assert (
            resolve_project_name(entries=entries, agent_name="Agent")
            == "Agent"
        )
        assert resolve_project_name(entries=entries) == "app"

    def test_blank_override_falls_through_rather_than_blanking(self):
        # Otherwise clearing the field would leave the UI with no name.
        from qwenpaw.config.project_dir import resolve_project_name

        entries = [{"path": "/repos/app", "label": None}]
        assert (
            resolve_project_name(entries=entries, session_name="   ") == "app"
        )

    def test_normalize_trims_and_caps_length(self):
        from qwenpaw.config.project_dir import (
            MAX_PROJECT_NAME_LEN,
            normalize_project_name,
        )

        assert normalize_project_name("  spaced  ") == "spaced"
        assert normalize_project_name("") is None
        assert normalize_project_name(None) is None
        assert normalize_project_name(123) is None
        assert (
            len(normalize_project_name("x" * 500)) == MAX_PROJECT_NAME_LEN
        )

    def test_read_from_agent_config_and_chat_meta(self):
        from types import SimpleNamespace

        from qwenpaw.config.project_dir import (
            agent_project_name_from_config,
            session_project_name_from_meta,
        )

        assert (
            agent_project_name_from_config(
                SimpleNamespace(project_name="From Agent"),
            )
            == "From Agent"
        )
        assert agent_project_name_from_config({"project_name": "D"}) == "D"
        assert agent_project_name_from_config(None) is None
        assert (
            session_project_name_from_meta(
                {"runtime_context": {"project_name": "From Chat"}},
            )
            == "From Chat"
        )
        assert session_project_name_from_meta({}) is None
        assert session_project_name_from_meta(None) is None
