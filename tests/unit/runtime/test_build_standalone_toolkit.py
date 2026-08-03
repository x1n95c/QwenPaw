# -*- coding: utf-8 -*-
"""Tests for ``AgentBuilder.build_standalone_toolkit``.

Two claims carry the cron preprocess design and are asserted here:

1. A Toolkit can be built with **no model configured** — the whole reason a
   text cron task can run a batch without involving an LLM. ``build()``
   refuses in that situation, so the two are compared side by side.
2. An existing governor is reused rather than replaced. Starting one takes
   a cross-process lock, writes ``policy.yaml`` and probes sandbox support,
   and nothing ever stops it — so rebuilding per cron tick would leak that
   work every interval, and swapping it mid-request would hand a concurrent
   request a different governor than the one it built.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from qwenpaw.runtime.builder import AgentBuilder


class FakeLocalWorkspace:
    """Stands in for QwenPawLocalWorkspace's governor + tool plumbing."""

    def __init__(self) -> None:
        self._governor: Any = None
        self.list_tools_calls: list[dict[str, Any]] = []

    def set_governor(self, governor: Any) -> None:
        self._governor = governor

    @property
    def governor(self) -> Any:
        return self._governor

    async def list_tools(self, **kwargs: Any) -> list[Any]:
        self.list_tools_calls.append(kwargs)
        return []


def make_workspace(tmp_path, local_ws: FakeLocalWorkspace | None = None):
    return SimpleNamespace(
        workspace_dir=tmp_path,
        local_workspace=local_ws or FakeLocalWorkspace(),
        agent_id="default",
    )


REQUEST_CONTEXT = {
    "source": "cron",
    "cron_phase": "preprocess",
    "approval_level": "off",
    "session_id": "s1",
    "agent_id": "default",
}


# ---------------------------------------------------------------------------
# Claim 1: no model required
# ---------------------------------------------------------------------------


def strip_active_model(monkeypatch) -> None:
    """Make both sources of an active model report "nothing configured".

    ``load_agent_config`` is imported *inside* the builder's methods, so it
    has to be patched at its definition site — patching the attribute on
    ``qwenpaw.runtime.builder`` has no effect. Worth stating because a
    silently-ineffective patch here would make the asymmetry test below
    pass for the wrong reason on a machine that has a model configured.
    """
    import qwenpaw.config.config as config_module
    from qwenpaw.providers.provider_manager import ProviderManager

    real = config_module.load_agent_config

    def _no_model(agent_id=None):
        cfg = real(agent_id)
        return SimpleNamespace(
            active_model=None,
            coding_mode=getattr(cfg, "coding_mode", None),
            running=cfg.running,
        )

    monkeypatch.setattr(config_module, "load_agent_config", _no_model)
    monkeypatch.setattr(
        ProviderManager,
        "get_active_model",
        lambda self: None,
        raising=False,
    )


@pytest.mark.asyncio
async def test_succeeds_with_no_active_model(tmp_path, monkeypatch):
    """The load-bearing claim: a batch runs without a configured model."""
    strip_active_model(monkeypatch)
    monkeypatch.setattr(
        AgentBuilder,
        "_init_governor",
        staticmethod(lambda *_a, **_k: object()),
    )

    builder = AgentBuilder(app_services=None)
    toolkit = await builder.build_standalone_toolkit(
        workspace=make_workspace(tmp_path),
        request_context=dict(REQUEST_CONTEXT),
    )
    assert toolkit is not None


@pytest.mark.asyncio
async def test_build_refuses_where_standalone_succeeds(tmp_path, monkeypatch):
    """Pins the asymmetry the whole design rests on."""
    strip_active_model(monkeypatch)
    monkeypatch.setattr(
        AgentBuilder,
        "_init_governor",
        staticmethod(lambda *_a, **_k: object()),
    )
    builder = AgentBuilder(app_services=None)
    workspace = make_workspace(tmp_path)

    # Same config, same workspace: standalone works...
    assert (
        await builder.build_standalone_toolkit(
            workspace=workspace,
            request_context=dict(REQUEST_CONTEXT),
        )
        is not None
    )

    # ...and build() refuses before it does anything else.
    ctx = SimpleNamespace(
        agent_id="default",
        workspace=workspace,
        workspace_dir=tmp_path,
        session_id="s1",
        root_session_id="s1",
        session_state=None,
        agent_config=None,
        request=SimpleNamespace(
            user_id="u1",
            channel="console",
            request_context={},
            model_slot_override=None,
        ),
        extras={},
    )
    with pytest.raises(RuntimeError, match="No active model configured"):
        await builder.build(ctx)


# ---------------------------------------------------------------------------
# Claim 2: governor reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reuses_an_existing_governor(tmp_path, monkeypatch):
    local_ws = FakeLocalWorkspace()
    sentinel = object()
    local_ws.set_governor(sentinel)

    created: list[int] = []

    def _never(*_args, **_kwargs):
        created.append(1)
        return object()

    monkeypatch.setattr(AgentBuilder, "_init_governor", staticmethod(_never))

    builder = AgentBuilder(app_services=None)
    await builder.build_standalone_toolkit(
        workspace=make_workspace(tmp_path, local_ws),
        request_context=dict(REQUEST_CONTEXT),
    )

    assert created == [], "must not start a second governor"
    assert local_ws.governor is sentinel


@pytest.mark.asyncio
async def test_creates_and_installs_a_governor_when_absent(
    tmp_path,
    monkeypatch,
):
    local_ws = FakeLocalWorkspace()
    sentinel = object()
    monkeypatch.setattr(
        AgentBuilder,
        "_init_governor",
        staticmethod(lambda *_a, **_k: sentinel),
    )

    builder = AgentBuilder(app_services=None)
    await builder.build_standalone_toolkit(
        workspace=make_workspace(tmp_path, local_ws),
        request_context=dict(REQUEST_CONTEXT),
    )

    # Installed so the next caller — and list_tools — reuses it.
    assert local_ws.governor is sentinel


@pytest.mark.asyncio
async def test_tolerates_governance_being_unavailable(tmp_path, monkeypatch):
    """_init_governor returns None on an unsupported platform."""
    local_ws = FakeLocalWorkspace()
    monkeypatch.setattr(
        AgentBuilder,
        "_init_governor",
        staticmethod(lambda *_a, **_k: None),
    )

    builder = AgentBuilder(app_services=None)
    toolkit = await builder.build_standalone_toolkit(
        workspace=make_workspace(tmp_path, local_ws),
        request_context=dict(REQUEST_CONTEXT),
    )
    assert toolkit is not None
    assert local_ws.governor is None


# ---------------------------------------------------------------------------
# What gets passed through to list_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_no_skills_and_no_modes(tmp_path, monkeypatch):
    """Skills exist for an LLM to read; there is no LLM here."""
    local_ws = FakeLocalWorkspace()
    monkeypatch.setattr(
        AgentBuilder,
        "_init_governor",
        staticmethod(lambda *_a, **_k: object()),
    )

    builder = AgentBuilder(app_services=None)
    await builder.build_standalone_toolkit(
        workspace=make_workspace(tmp_path, local_ws),
        request_context=dict(REQUEST_CONTEXT),
    )

    assert len(local_ws.list_tools_calls) == 1
    call = local_ws.list_tools_calls[0]
    assert tuple(call["active_skills"]) == ()
    assert tuple(call["active_modes"]) == ()


@pytest.mark.asyncio
async def test_threads_request_context_to_tools(tmp_path, monkeypatch):
    """approval_level lives here; a guarded tool reads it per call."""
    local_ws = FakeLocalWorkspace()
    monkeypatch.setattr(
        AgentBuilder,
        "_init_governor",
        staticmethod(lambda *_a, **_k: object()),
    )

    builder = AgentBuilder(app_services=None)
    await builder.build_standalone_toolkit(
        workspace=make_workspace(tmp_path, local_ws),
        request_context=dict(REQUEST_CONTEXT),
    )

    passed = local_ws.list_tools_calls[0]["request_context"]
    assert passed["approval_level"] == "off"
    assert passed["cron_phase"] == "preprocess"


# ---------------------------------------------------------------------------
# Why the governor wiring above is not optional.
#
# PolicyGuardedTool is fail-closed: with no governor it DENIES everything
# except under approval_level=off. Since cron jobs default to
# tool_safety=False (→ off), a governor-less Toolkit would look fine in
# testing and then deny every tool the moment someone enables tool safety.
# This test pins that matrix so the reason for the wiring stays visible.
# ---------------------------------------------------------------------------


async def permission_behavior(governor: Any, approval_level: str) -> str:
    from qwenpaw.governance.tool_adapter import (
        _policy_tool_check_permissions,
    )

    tool = SimpleNamespace(
        _qp_governor=governor,
        _qp_request_context={"approval_level": approval_level},
        _qp_raw_params={},
        name="execute_shell_command",
        _build_tc_spec=lambda: SimpleNamespace(
            tool_name="execute_shell_command",
        ),
    )
    decision = await _policy_tool_check_permissions(tool, {"command": "date"})
    return decision.behavior.name


@pytest.mark.asyncio
async def test_no_governor_allows_only_under_approval_off():
    assert await permission_behavior(None, "off") == "ALLOW"


@pytest.mark.asyncio
async def test_no_governor_denies_every_tool_under_approval_auto():
    """The silent-failure case build_standalone_toolkit exists to avoid."""
    assert await permission_behavior(None, "auto") == "DENY"
