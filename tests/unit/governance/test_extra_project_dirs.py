# -*- coding: utf-8 -*-
"""Governance registration for ALL bound project directories.

The primary uses the PROJECT_DIR placeholder; every additional bound
directory gets a system-managed ALLOW rule (``*(<path>/**)``, reason
"Extra project dir") that is synced against the bound list on each load.
"""
# pylint: disable=protected-access

from __future__ import annotations

import pytest
import yaml

from qwenpaw.governance.policy import (
    EXTRA_PROJECT_DIR_RULE_REASON,
    GovernanceAction,
    load_governance_policy,
    save_governance_policy,
)
from qwenpaw.governance.resource_governor import ResourceGovernor


def _extra_rules(policy):
    return [
        rule
        for rule in policy.user_rules
        if rule.reason == EXTRA_PROJECT_DIR_RULE_REASON
    ]


class TestExtraProjectDirRules:
    def test_each_extra_dir_gets_an_allow_rule(self, tmp_path):
        policy = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
            extra_project_dirs=[
                str(tmp_path / "backend"),
                str(tmp_path / "protos"),
            ],
        )

        rules = _extra_rules(policy)
        assert len(rules) == 2
        matches = {rule.match for rule in rules}
        assert matches == {
            f"*({tmp_path / 'backend'}/**)",
            f"*({tmp_path / 'protos'}/**)",
        }
        assert all(rule.action == GovernanceAction.ALLOW for rule in rules)

    def test_extra_duplicating_primary_is_not_registered(self, tmp_path):
        """The primary placeholder rule already covers it."""
        policy = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
            extra_project_dirs=[str(tmp_path / "primary")],
        )

        assert _extra_rules(policy) == []

    def test_extra_duplicating_workspace_is_not_registered(self, tmp_path):
        policy = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir="",
            extra_project_dirs=[str(tmp_path / "ws")],
        )

        assert _extra_rules(policy) == []

    def test_sync_drops_rules_for_unbound_dirs(self, tmp_path):
        """Unbinding a directory revokes its grant on the next load."""
        backend = str(tmp_path / "backend")
        protos = str(tmp_path / "protos")

        first = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
            extra_project_dirs=[backend, protos],
        )
        save_governance_policy(
            first,
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
        )

        reloaded = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
            extra_project_dirs=[backend],
        )

        rules = _extra_rules(reloaded)
        assert len(rules) == 1
        assert rules[0].match == f"*({backend}/**)"

    def test_sync_is_idempotent(self, tmp_path):
        backend = str(tmp_path / "backend")
        kwargs = {
            "workspace_dir": str(tmp_path / "ws"),
            "coding_project_dir": str(tmp_path / "primary"),
            "extra_project_dirs": [backend],
        }

        first = load_governance_policy(str(tmp_path), **kwargs)
        save_governance_policy(
            first,
            str(tmp_path),
            workspace_dir=kwargs["workspace_dir"],
            coding_project_dir=kwargs["coding_project_dir"],
        )
        second = load_governance_policy(str(tmp_path), **kwargs)

        assert len(_extra_rules(second)) == 1

    def test_extra_rules_persist_as_literal_paths(self, tmp_path):
        """They are config-derived; portability placeholders would need a
        reverse mapping per directory, which is not worth it."""
        backend = str(tmp_path / "backend")
        policy = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
            extra_project_dirs=[backend],
        )
        save_governance_policy(
            policy,
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(tmp_path / "primary"),
        )

        with open(tmp_path / "policy.yaml", encoding="utf-8") as f:
            text = f.read()
        assert backend in text

    def test_extra_sharing_a_prefix_with_the_primary_stays_literal(
        self,
        tmp_path,
    ):
        """``/repos/app`` must not rewrite ``/repos/app-docs``.

        Placeholder substitution is a plain substring replace, so a
        primary that is a string prefix of an extra would turn the extra
        into ``PROJECT_DIR-docs`` — a path that silently rebinds to
        somewhere else once the primary changes.
        """
        primary = tmp_path / "app"
        extra = tmp_path / "app-docs"
        policy = load_governance_policy(
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(primary),
            extra_project_dirs=[str(extra)],
        )
        save_governance_policy(
            policy,
            str(tmp_path),
            workspace_dir=str(tmp_path / "ws"),
            coding_project_dir=str(primary),
        )

        text = (tmp_path / "policy.yaml").read_text(encoding="utf-8")
        assert f"*({extra}/**)" in text
        assert "PROJECT_DIR-docs" not in text

    def test_user_deletion_of_extra_rule_does_not_stick(self, tmp_path):
        """These rules are system-managed: revocation happens by
        unbinding the directory, not by deleting the rule."""
        backend = str(tmp_path / "backend")
        kwargs = {
            "workspace_dir": str(tmp_path / "ws"),
            "coding_project_dir": str(tmp_path / "primary"),
            "extra_project_dirs": [backend],
        }

        first = load_governance_policy(str(tmp_path), **kwargs)
        # Simulate the user deleting the rule from policy.yaml.
        first.user_rules = [
            rule
            for rule in first.user_rules
            if rule.reason != EXTRA_PROJECT_DIR_RULE_REASON
        ]
        save_governance_policy(
            first,
            str(tmp_path),
            workspace_dir=kwargs["workspace_dir"],
            coding_project_dir=kwargs["coding_project_dir"],
        )

        reloaded = load_governance_policy(str(tmp_path), **kwargs)
        assert len(_extra_rules(reloaded)) == 1


class TestGovernorExtraDirs:
    def test_governor_registers_extra_dirs_on_start(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        backend = tmp_path / "backend"
        backend.mkdir()

        governor = ResourceGovernor(
            str(ws),
            governance_dir=str(tmp_path / "governance"),
            coding_project_dir=None,
            extra_project_dirs=[str(backend)],
        )
        governor.start()
        try:
            rules = _extra_rules(governor._policy)
            assert len(rules) == 1
        finally:
            governor.stop()

    def test_governor_drops_extras_duplicating_primary(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        primary = tmp_path / "proj"
        primary.mkdir()

        governor = ResourceGovernor(
            str(ws),
            governance_dir=str(tmp_path / "governance"),
            coding_project_dir=str(primary),
            extra_project_dirs=[str(primary), str(ws)],
        )

        assert governor.extra_project_dirs == []

    def test_sandbox_mounts_include_extra_dirs(self, tmp_path):
        """The policy ALLOW rule alone is not enough for sandboxed shell
        tools; extra dirs must be mounted too."""
        from qwenpaw.governance.policy import ToolCallSpec

        ws = tmp_path / "ws"
        ws.mkdir()
        backend = tmp_path / "backend"
        backend.mkdir()

        governor = ResourceGovernor(
            str(ws),
            governance_dir=str(tmp_path / "governance"),
            coding_project_dir=None,
            extra_project_dirs=[str(backend)],
        )
        governor.start()
        try:
            spec = ToolCallSpec(
                tool_name="execute_shell_command",
                target="ls",
                agent_id="test-agent",
                session_id="test-session",
            )
            config = governor.compile_sandbox_config(spec)
            mounted = {m.path: m.writable for m in config.mounts}
            assert str(backend) in mounted
            assert mounted[str(backend)] is True
        finally:
            governor.stop()
