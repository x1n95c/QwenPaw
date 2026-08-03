# -*- coding: utf-8 -*-
"""ContextVar setup hook.

Injects per-request ContextVars before agent execution so that tools
(shell, file_io, etc.) see correct workspace_dir, session_id, etc.
"""

from __future__ import annotations

import logging

from ..base import LifecycleHook
from ...runtime.hooks import HookContext, HookResult
from ...runtime.phases import Phase

logger = logging.getLogger(__name__)


class ContextVarsSetupHook(LifecycleHook):
    """Inject per-request ContextVars before agent execution."""

    phase = Phase.PRE_DISPATCH
    name = "contextvars_setup"
    priority = 10

    async def run(self, ctx: HookContext) -> HookResult:
        from ...config.context import (
            set_current_workspace_dir,
            set_current_session_id,
            set_current_recent_max_bytes,
            set_current_shell_command_timeout,
            set_current_shell_command_executable,
        )
        from ...app.agent_context import (
            set_current_agent_id,
            set_current_approval_route,
            set_current_channel,
            set_current_root_session_id,
            set_current_session_id as _set_app_session_id,
            set_current_user_id,
        )

        set_current_agent_id(ctx.agent_id or "default")
        _session_id = ctx.session_id or ""
        set_current_session_id(_session_id)
        _set_app_session_id(_session_id)
        set_current_root_session_id(
            ctx.root_session_id or ctx.session_id or "",
        )
        set_current_user_id(ctx.request.user_id)
        set_current_channel(getattr(ctx.request, "channel", None))
        request_context = getattr(ctx.request, "request_context", None)
        if isinstance(request_context, dict) and request_context.get(
            "_spawn_subagent",
        ):
            approval_route = {
                key: request_context.get(key)
                for key in (
                    "root_session_id",
                    "user_id",
                    "channel",
                    "channel_meta",
                )
            }
        else:
            approval_route = {
                "root_session_id": ctx.root_session_id or ctx.session_id or "",
                "user_id": getattr(ctx.request, "user_id", None) or "",
                "channel": getattr(ctx.request, "channel", None) or "",
                "channel_meta": getattr(ctx.request, "channel_meta", None),
            }
        set_current_approval_route(approval_route)

        # Shared with the non-request callers (cron preprocess) so the two
        # paths cannot disagree about shell timeouts or pruning limits.
        # config_derived_tool_values never raises; it logs and returns
        # defaults, which is what this block used to do inline.
        from ...runtime.tool_context import config_derived_tool_values

        config_values = config_derived_tool_values(ctx.agent_id)
        set_current_recent_max_bytes(config_values.recent_max_bytes)
        set_current_shell_command_timeout(
            config_values.shell_command_timeout,
        )
        set_current_shell_command_executable(
            config_values.shell_command_executable,
        )
        coding_project_dir = config_values.coding_project_dir

        # Forked subagents must resolve relative file/shell paths against
        # the worktree, not the parent workspace.
        fork_dir = None
        if isinstance(request_context, dict):
            from ...agents.fork_project import resolve_allowed_fork_project_dir

            fork_dir = resolve_allowed_fork_project_dir(
                request_context.get("fork_project_dir"),
                workspace_dir=ctx.workspace_dir,
                coding_project_dir=coding_project_dir,
            )
        if fork_dir is not None:
            set_current_workspace_dir(fork_dir)
        elif ctx.workspace_dir is not None:
            set_current_workspace_dir(ctx.workspace_dir)
        return HookResult()


__all__ = ["ContextVarsSetupHook"]
