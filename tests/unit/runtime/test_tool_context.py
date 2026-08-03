# -*- coding: utf-8 -*-
"""Tests for the shared tool-facing ContextVar seeding.

The point of this module is that a caller which runs tools outside a
request (a cron preprocess batch) seeds the same vars a request would, and
gives every one of them back afterwards. A leak here is silent: the next
reader cannot distinguish a live toolkit from one belonging to finished
work, so these tests assert restoration explicitly rather than trusting
asyncio's implicit per-task context copy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from qwenpaw.app import agent_context as ac
from qwenpaw.config import context as cc
from qwenpaw.runtime.tool_context import (
    ConfigDerivedToolValues,
    config_derived_tool_values,
    scoped_tool_context,
)


ALL_GETTERS = {
    "workspace_dir": cc.get_current_workspace_dir,
    "cc_session_id": cc.get_current_session_id,
    "recent_max_bytes": cc.get_current_recent_max_bytes,
    "shell_timeout": cc.get_current_shell_command_timeout,
    "shell_exe": cc.get_current_shell_command_executable,
    "toolkit": cc.get_current_toolkit,
    "agent_state": cc.get_current_agent_state,
    "agent_id": ac.get_current_agent_id,
    "ac_session_id": ac.get_current_session_id,
    "root_session_id": ac.get_current_root_session_id,
    "user_id": ac.get_current_user_id,
    "channel": ac.get_current_channel,
    "approval_route": ac.get_current_approval_route,
}


def snapshot() -> dict[str, object]:
    return {name: getter() for name, getter in ALL_GETTERS.items()}


CONFIG = ConfigDerivedToolValues(
    recent_max_bytes=1234,
    shell_command_timeout=9.5,
    shell_command_executable="/bin/zsh",
)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeds_every_var_a_tool_reads():
    sentinel_toolkit = object()
    sentinel_state = object()

    with scoped_tool_context(
        toolkit=sentinel_toolkit,
        agent_state=sentinel_state,
        workspace_dir=Path("/ws"),
        session_id="s1",
        agent_id="a1",
        user_id="u1",
        channel="console",
        approval_route={"user_id": "u1"},
        config_values=CONFIG,
    ):
        assert cc.get_current_toolkit() is sentinel_toolkit
        assert cc.get_current_agent_state() is sentinel_state
        assert cc.get_current_workspace_dir() == Path("/ws")
        assert cc.get_current_session_id() == "s1"
        assert cc.get_current_recent_max_bytes() == 1234
        assert cc.get_current_shell_command_timeout() == 9.5
        assert cc.get_current_shell_command_executable() == "/bin/zsh"
        assert ac.get_current_agent_id() == "a1"
        assert ac.get_current_session_id() == "s1"
        assert ac.get_current_user_id() == "u1"
        assert ac.get_current_channel() == "console"
        assert ac.get_current_approval_route() == {"user_id": "u1"}


def test_session_id_seeds_both_modules():
    """Two modules keep their own session var; the request hook sets both."""
    with scoped_tool_context(session_id="dual"):
        assert cc.get_current_session_id() == "dual"
        assert ac.get_current_session_id() == "dual"


def test_root_session_id_defaults_to_session_id():
    with scoped_tool_context(session_id="s1"):
        assert ac.get_current_root_session_id() == "s1"


def test_explicit_root_session_id_wins():
    with scoped_tool_context(session_id="child", root_session_id="root"):
        assert ac.get_current_root_session_id() == "root"


def test_omitted_config_values_seed_none():
    with scoped_tool_context(session_id="s"):
        assert cc.get_current_shell_command_timeout() is None
        assert cc.get_current_recent_max_bytes() is None


# ---------------------------------------------------------------------------
# Restoration — the reason this module exists
# ---------------------------------------------------------------------------


def test_every_var_is_restored_on_exit():
    before = snapshot()
    with scoped_tool_context(
        toolkit=object(),
        agent_state=object(),
        workspace_dir=Path("/ws"),
        session_id="s1",
        agent_id="a1",
        user_id="u1",
        channel="console",
        approval_route={"k": "v"},
        config_values=CONFIG,
    ):
        pass
    assert snapshot() == before


def test_restores_to_a_previous_value_not_just_none():
    """The dangerous case: an outer request already seeded these."""
    outer_toolkit = object()
    with scoped_tool_context(
        toolkit=outer_toolkit,
        session_id="outer",
        agent_id="outer-agent",
    ):
        with scoped_tool_context(
            toolkit=object(),
            session_id="inner",
            agent_id="inner-agent",
        ):
            assert cc.get_current_session_id() == "inner"
            assert ac.get_current_agent_id() == "inner-agent"
        # Back to the outer request's values, not to None.
        assert cc.get_current_toolkit() is outer_toolkit
        assert cc.get_current_session_id() == "outer"
        assert ac.get_current_agent_id() == "outer-agent"


def test_restores_when_the_body_raises():
    before = snapshot()
    with pytest.raises(RuntimeError):
        with scoped_tool_context(toolkit=object(), session_id="s1"):
            raise RuntimeError("batch blew up")
    assert snapshot() == before


def test_none_is_scoped_explicitly_and_restored():
    with scoped_tool_context(session_id="outer"):
        with scoped_tool_context(session_id=None):
            assert cc.get_current_session_id() is None
        assert cc.get_current_session_id() == "outer"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_see_each_others_toolkit():
    """Two cron jobs preprocessing at once must stay isolated."""
    kits = {"a": object(), "b": object()}
    observed: dict[str, object] = {}
    gate = asyncio.Event()

    async def run(name: str) -> None:
        with scoped_tool_context(toolkit=kits[name], session_id=name):
            # Hold the context open across a suspension point so the two
            # tasks genuinely overlap.
            gate.set()
            await asyncio.sleep(0)
            observed[name] = cc.get_current_toolkit()
            observed[f"{name}_session"] = cc.get_current_session_id()

    await asyncio.gather(
        asyncio.create_task(run("a")),
        asyncio.create_task(run("b")),
    )

    assert observed["a"] is kits["a"]
    assert observed["b"] is kits["b"]
    assert observed["a_session"] == "a"
    assert observed["b_session"] == "b"


@pytest.mark.asyncio
async def test_a_task_does_not_leak_into_its_parent():
    before = cc.get_current_toolkit()

    async def run() -> None:
        with scoped_tool_context(toolkit=object()):
            await asyncio.sleep(0)

    await asyncio.create_task(run())
    assert cc.get_current_toolkit() is before


# ---------------------------------------------------------------------------
# Typo protection
# ---------------------------------------------------------------------------


def test_unknown_key_raises_in_runtime_context():
    with pytest.raises(TypeError, match="unexpected keys"):
        with cc.scoped_runtime_context(nope=1):
            pass


def test_unknown_key_raises_in_agent_context():
    with pytest.raises(TypeError, match="unexpected keys"):
        with ac.scoped_agent_context(nope=1):
            pass


# ---------------------------------------------------------------------------
# config_derived_tool_values
# ---------------------------------------------------------------------------


def test_config_values_degrade_to_defaults_on_failure(monkeypatch):
    """A config problem must not fail the caller."""
    import qwenpaw.config.config as config_module

    def _boom(_agent_id):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config_module, "load_agent_config", _boom)
    values = config_derived_tool_values("default")
    assert values == ConfigDerivedToolValues()


def test_config_values_read_the_agent_config():
    values = config_derived_tool_values("default")
    # Whatever the defaults are, the call must succeed and return the
    # dataclass rather than raising or returning None.
    assert isinstance(values, ConfigDerivedToolValues)
