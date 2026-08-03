# -*- coding: utf-8 -*-
"""Seed every ContextVar a bare tool call needs, in one place.

Tools reach for request-scoped state through ContextVars rather than
parameters — ``execute_shell_command`` resolves relative paths against
``current_workspace_dir``, ``run_tool_batch`` finds its Toolkit through
``current_toolkit``, approval prompts route through
``current_approval_route``. During a normal request
:class:`~qwenpaw.hooks.request_setup.contextvars_hook.ContextVarsSetupHook`
seeds them all.

A caller that runs tools *without* a request — a cron preprocess batch —
has to seed the same set, and getting it wrong fails quietly: a missing
``current_workspace_dir`` sends relative paths to the server's cwd instead
of the agent workspace. So the list of vars and the config-derived values
live here, shared by both callers, rather than being written out twice.

This module owns composition only; each of the two context modules still
owns its own vars.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigDerivedToolValues:
    """Tool-affecting values read from an agent's config.

    Shared so the preprocess path and the request path cannot disagree
    about, say, the shell timeout.
    """

    recent_max_bytes: int | None = None
    shell_command_timeout: float | None = None
    shell_command_executable: str | None = None
    #: Not a ContextVar itself — callers need it to resolve a fork dir.
    coding_project_dir: str | None = None


def config_derived_tool_values(
    agent_id: str | None,
) -> ConfigDerivedToolValues:
    """Read the config-derived tool values for one agent.

    Never raises: a config problem must degrade to defaults rather than
    fail the caller, matching the request hook's behaviour.
    """
    try:
        from ..config.config import load_agent_config

        cfg = load_agent_config(agent_id)
        running = cfg.running
        pruning = running.light_context_config.tool_result_pruning_config
        coding_mode = getattr(cfg, "coding_mode", None)
        project_dir = None
        if (
            coding_mode
            and getattr(coding_mode, "enabled", False)
            and getattr(coding_mode, "project_dir", None)
        ):
            project_dir = coding_mode.project_dir
        return ConfigDerivedToolValues(
            recent_max_bytes=pruning.pruning_recent_msg_max_bytes,
            shell_command_timeout=running.shell_command_timeout,
            shell_command_executable=running.shell_command_executable or None,
            coding_project_dir=project_dir,
        )
    except Exception:
        logger.warning(
            "config_derived_tool_values: falling back to defaults for "
            "agent_id=%s; tools may see different limits than a request "
            "would",
            agent_id,
            exc_info=True,
        )
        return ConfigDerivedToolValues()


@contextmanager
def scoped_tool_context(
    *,
    toolkit: Any = None,
    agent_state: Any = None,
    workspace_dir: Path | None = None,
    session_id: str | None = None,
    root_session_id: str | None = None,
    agent_id: str | None = None,
    user_id: str | None = None,
    channel: str | None = None,
    approval_route: dict | None = None,
    config_values: ConfigDerivedToolValues | None = None,
) -> Iterator[None]:
    """Seed every tool-facing ContextVar, then restore all of them.

    Restores on exit rather than setting and walking away. A left-behind
    ``current_toolkit`` is worse than one that was never set: readers
    cannot tell a live toolkit from one belonging to work that finished,
    and the request pipeline only overwrites it at ``POST_AGENT_BUILD`` —
    so anything running before that would observe the stale one.

    ``session_id`` seeds the var in *both* context modules, matching what
    the request hook does.

    Args:
        config_values: Result of :func:`config_derived_tool_values`. Pass
            it explicitly so a caller cannot forget the shell timeout and
            silently get a different limit than a request would.
    """
    from ..app.agent_context import scoped_agent_context
    from ..config.context import scoped_runtime_context

    values = config_values or ConfigDerivedToolValues()

    runtime_values: dict[str, Any] = {
        "toolkit": toolkit,
        "agent_state": agent_state,
        "workspace_dir": workspace_dir,
        "session_id": session_id,
        "recent_max_bytes": values.recent_max_bytes,
        "shell_command_timeout": values.shell_command_timeout,
        "shell_command_executable": values.shell_command_executable,
    }
    agent_values: dict[str, Any] = {
        "agent_id": agent_id,
        "session_id": session_id,
        "root_session_id": root_session_id or session_id,
        "user_id": user_id,
        "channel": channel,
        "approval_route": approval_route,
    }

    with ExitStack() as stack:
        stack.enter_context(scoped_runtime_context(**runtime_values))
        stack.enter_context(scoped_agent_context(**agent_values))
        yield


__all__ = [
    "ConfigDerivedToolValues",
    "config_derived_tool_values",
    "scoped_tool_context",
]
