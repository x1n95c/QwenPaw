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

        agent_project_dir = None
        try:
            from ...config.config import load_agent_config
            from ...config.project_dir import agent_project_dir_from_config

            cfg = load_agent_config(ctx.agent_id)
            running = cfg.running
            pruning_cfg = (
                running.light_context_config.tool_result_pruning_config
            )
            set_current_recent_max_bytes(
                pruning_cfg.pruning_recent_msg_max_bytes,
            )
            set_current_shell_command_timeout(running.shell_command_timeout)
            set_current_shell_command_executable(
                running.shell_command_executable or None,
            )
            # Mode-independent: the project dir applies to every mode, so
            # it is read regardless of whether Coding Mode is enabled.
            agent_project_dir = agent_project_dir_from_config(cfg)
        except Exception:
            logger.warning(
                "contextvars_setup: config-derived vars failed; "
                "tools may see defaults",
                exc_info=True,
            )

        # Forked subagents must resolve relative file/shell paths against
        # the worktree they were assigned, and must not be able to escape
        # it. Validate before handing it to the resolver, which trusts it.
        fork_dir = None
        request_override = None
        if isinstance(request_context, dict):
            from ...agents.fork_project import resolve_allowed_fork_project_dir

            fork_dir = resolve_allowed_fork_project_dir(
                request_context.get("fork_project_dir"),
                workspace_dir=ctx.workspace_dir,
                coding_project_dir=agent_project_dir,
            )
            request_override = _trusted_request_project_dir(request_context)

        # The workspace ContextVar always points at the agent's own storage.
        # Never repoint it to a project: memory, skills, cache, approvals
        # and audit records resolve from it and must stay inside the agent.
        if ctx.workspace_dir is not None:
            set_current_workspace_dir(ctx.workspace_dir)

        self._apply_project_dir(
            ctx,
            agent_project_dir=agent_project_dir,
            session_project_dir=await _session_project_dir(ctx),
            request_override=request_override,
            fork_dir=fork_dir,
        )
        return HookResult()

    @staticmethod
    def _apply_project_dir(
        ctx: HookContext,
        *,
        agent_project_dir: str | None,
        session_project_dir: str | None,
        request_override: str | None,
        fork_dir: object | None,
    ) -> None:
        """Resolve the effective project dir once and pin it for this turn."""
        from ...config.context import (
            set_current_project_dir,
            set_current_project_dir_source,
        )
        from ...config.project_dir import resolve_effective_project_dir

        if ctx.workspace_dir is None:
            # Without a workspace there is no safe fallback; leave the
            # project ContextVar unset so tools use their own defaults.
            return

        # A mode may pin a directory for the whole run (Mission fixes its
        # source project at start so a mid-run session switch cannot make
        # the worker jump repositories).
        mode_override = None
        mode_state = getattr(ctx, "mode_state", None)
        if isinstance(mode_state, dict):
            for state in mode_state.values():
                if isinstance(state, dict) and state.get("project_dir_pin"):
                    mode_override = state["project_dir_pin"]
                    break

        try:
            resolved = resolve_effective_project_dir(
                workspace_dir=ctx.workspace_dir,
                agent_project_dir=agent_project_dir,
                session_project_dir=session_project_dir,
                request_override=request_override,
                mode_override=mode_override,
                fork_project_dir=fork_dir,
            )
        except ValueError:
            logger.warning(
                "contextvars_setup: could not resolve project dir",
                exc_info=True,
            )
            return

        if not resolved.exists and not resolved.is_workspace_fallback:
            # Do not silently fall back: writing to the wrong place is far
            # worse than a clear tool error the user can act on.
            logger.warning(
                "Effective project dir does not exist: %s (source=%s)",
                resolved.path,
                resolved.source,
            )

        set_current_project_dir(resolved.path)
        set_current_project_dir_source(resolved.source)


def _trusted_request_project_dir(request_context: dict) -> str | None:
    """Return an ephemeral project override from a trusted request source.

    Recognised sources:

    * ACP session metadata (``qwenpaw.coding_project_dir``)
    * cron task config (``cron_project_dir``)
    * ``pending_project_dir`` — a directory the console user picked for a
      brand-new chat, before that chat had an id to persist it against.
      The console router validates the path and writes it onto the chat as
      soon as the chat exists; reading it here as well is what makes the
      **first** turn already run in the chosen directory rather than in the
      agent default.

    Per-run only: never written back to the agent's saved default.
    """
    from ...agents.acp.meta import ACP_CODING_PROJECT_META_KEY

    for key in (ACP_CODING_PROJECT_META_KEY, "cron_project_dir"):
        value = request_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Client-supplied, so validate here too. The console router refuses to
    # persist a non-directory; if this accepted one anyway the turn would
    # run somewhere the chat was never bound to.
    pending = request_context.get("pending_project_dir")
    if isinstance(pending, str) and pending.strip():
        from ...config.project_dir import normalize_project_dir

        normalized = normalize_project_dir(pending)
        if normalized is not None and normalized.is_dir():
            return str(normalized)
        logger.warning(
            "Ignoring pending_project_dir that is not a directory",
        )
    return None


async def _session_project_dir(ctx: HookContext) -> str | None:
    """Read the persisted per-chat project override, if any.

    Runs on **every** turn: the override lives on the chat, so this is what
    keeps a session-level directory in effect after the turn that set it.
    """
    if not ctx.session_id:
        return None
    try:
        from ...app.channels.schema import DEFAULT_CHANNEL
        from ...config.project_dir import session_project_dir_from_meta

        workspace = getattr(ctx, "workspace", None)
        chat_manager = getattr(workspace, "chat_manager", None)
        if chat_manager is None:
            return None

        request = getattr(ctx, "request", None)
        # `channel` is required by the lookup: chats are indexed per channel,
        # so omitting it finds nothing. Cron/heartbeat turns may not carry
        # one, hence the default.
        channel = getattr(request, "channel", None) or DEFAULT_CHANNEL
        user_id = getattr(request, "user_id", None) or None

        chat_id = await chat_manager.get_chat_id_by_session(
            ctx.session_id,
            channel,
            user_id,
        )
        if not chat_id:
            return None
        chat = await chat_manager.get_chat(chat_id)
        if chat is None:
            return None
        return session_project_dir_from_meta(chat.meta)
    except Exception:
        # Warning, not debug: a silent failure here degrades to the agent
        # default, which looks like "the setting reverted on its own" and is
        # very hard to trace from the UI.
        logger.warning(
            "contextvars_setup: session project dir lookup failed; "
            "falling back to the agent default",
            exc_info=True,
        )
        return None


__all__ = ["ContextVarsSetupHook"]
