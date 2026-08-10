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

        agent_project_dirs: list[dict] = []
        try:
            from ...config.config import load_agent_config
            from ...config.project_dir import agent_project_dirs_from_config

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
            # Mode-independent: project dirs apply to every mode, so they
            # are read regardless of whether Coding Mode is enabled.
            agent_project_dirs = agent_project_dirs_from_config(cfg)
        except Exception:
            logger.warning(
                "contextvars_setup: config-derived vars failed; "
                "tools may see defaults",
                exc_info=True,
            )

        session_project_dirs = await _session_project_dirs(ctx)

        # Forked subagents must resolve relative file/shell paths against
        # the worktree they were assigned, and must not be able to escape
        # it. Validate before handing it to the resolver, which trusts it.
        # Allowed roots: the workspace plus every configured project dir
        # (agent default and session override alike) — a fork may target
        # any directory the user bound to this agent/chat.
        fork_dir = None
        request_override = None
        if isinstance(request_context, dict):
            from ...agents.fork_project import (
                resolve_allowed_fork_project_dir,
            )

            allowed_dirs = [entry["path"] for entry in agent_project_dirs]
            if session_project_dirs:
                allowed_dirs.extend(
                    entry["path"] for entry in session_project_dirs
                )
            fork_dir = resolve_allowed_fork_project_dir(
                request_context.get("fork_project_dir"),
                workspace_dir=ctx.workspace_dir,
                project_dirs=allowed_dirs,
            )
            request_override = _trusted_request_project_dir(request_context)
            if session_project_dirs is None:
                session_project_dirs = _pending_project_dirs(request_context)

        # The workspace ContextVar always points at the agent's own storage.
        # Never repoint it to a project: memory, skills, cache, approvals
        # and audit records resolve from it and must stay inside the agent.
        if ctx.workspace_dir is not None:
            set_current_workspace_dir(ctx.workspace_dir)

        self._apply_project_dirs(
            ctx,
            agent_project_dirs=agent_project_dirs,
            session_project_dirs=session_project_dirs,
            request_override=request_override,
            fork_dir=fork_dir,
        )
        return HookResult()

    @staticmethod
    def _apply_project_dirs(
        ctx: HookContext,
        *,
        agent_project_dirs: list[dict],
        session_project_dirs: list[dict] | None,
        request_override: str | None,
        fork_dir: object | None,
    ) -> None:
        """Resolve the effective project dirs once and pin them for the
        turn.
        """
        from ...config.context import (
            set_current_project_dir,
            set_current_project_dir_source,
            set_current_project_dirs,
        )
        from ...config.project_dir import resolve_effective_project_dirs

        if ctx.workspace_dir is None:
            # Without a workspace there is no safe fallback; leave the
            # project ContextVars unset so tools use their own defaults.
            return

        # A mode may pin directories for the whole run (Mission snapshots
        # the list at start so a mid-run session switch cannot make the
        # worker jump repositories).
        mode_override = None
        mode_state = getattr(ctx, "mode_state", None)
        if isinstance(mode_state, dict):
            for state in mode_state.values():
                if not isinstance(state, dict):
                    continue
                if state.get("project_dirs_pin"):
                    mode_override = state["project_dirs_pin"]
                    break
                if state.get("project_dir_pin"):
                    # Single-dir pin from older state: treat as a
                    # one-entry list.
                    mode_override = [state["project_dir_pin"]]
                    break

        try:
            resolved = resolve_effective_project_dirs(
                workspace_dir=ctx.workspace_dir,
                agent_project_dirs=agent_project_dirs,
                session_project_dirs=session_project_dirs,
                request_override=request_override,
                mode_override=mode_override,
                fork_project_dir=fork_dir,
            )
        except ValueError:
            logger.warning(
                "contextvars_setup: could not resolve project dirs",
                exc_info=True,
            )
            return

        primary = resolved.primary
        if not primary.exists and not resolved.is_workspace_fallback:
            # Do not silently fall back: writing to the wrong place is far
            # worse than a clear tool error the user can act on.
            logger.warning(
                "Effective primary project dir does not exist: %s "
                "(source=%s)",
                primary.path,
                resolved.source,
            )

        set_current_project_dirs(resolved.dirs)
        set_current_project_dir(primary.path)
        set_current_project_dir_source(resolved.source)


def _trusted_request_project_dir(request_context: dict) -> str | None:
    """Return an ephemeral PRIMARY project override from a trusted source.

    Recognised sources:

    * ACP session metadata (``qwenpaw.coding_project_dir``)
    * cron task config (``cron_project_dir``)

    Per-run only: never written back to the agent's saved default.
    Console "pending" picks for brand-new chats are handled separately by
    :func:`_pending_project_dirs` because they carry the whole list.
    """
    from ...agents.acp.meta import ACP_CODING_PROJECT_META_KEY

    for key in (ACP_CODING_PROJECT_META_KEY, "cron_project_dir"):
        value = request_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pending_project_dirs(request_context: dict) -> list[dict] | None:
    """Read console pending picks for a brand-new chat, if present.

    A chat without a server id cannot persist a session override yet, so
    the console sends the chosen list with the first message. The console
    router also persists it onto the chat as soon as the chat exists;
    reading it here too is what makes the **first** turn already run in
    the chosen directories if the persistence has not landed yet.

    Accepts ``pending_project_dirs`` (the list) and the legacy singular
    ``pending_project_dir``. Client-supplied, so every entry is validated
    here: a non-directory is dropped rather than granted.
    """
    from ...config.project_dir import normalize_project_dir_list

    raw: list | None = None
    pending_list = request_context.get("pending_project_dirs")
    if isinstance(pending_list, list) and pending_list:
        raw = pending_list
    else:
        pending_single = request_context.get("pending_project_dir")
        if isinstance(pending_single, str) and pending_single.strip():
            raw = [pending_single]
    if raw is None:
        return None

    entries = []
    for path, label in normalize_project_dir_list(raw):
        if not path.is_dir():
            logger.warning(
                "Ignoring pending project dir that is not a directory: %s",
                path,
            )
            continue
        entries.append({"path": str(path), "label": label})
    return entries or None


async def _session_project_dirs(ctx: HookContext) -> list[dict] | None:
    """Read the persisted per-chat project-dirs override, if any.

    Runs on **every** turn: the override lives on the chat, so this is
    what keeps session-level directories in effect after the turn that
    set them.
    """
    if not ctx.session_id:
        return None
    try:
        from ...app.channels.schema import DEFAULT_CHANNEL
        from ...config.project_dir import session_project_dirs_from_meta

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
        return session_project_dirs_from_meta(chat.meta)
    except Exception:
        # Warning, not debug: a silent failure here degrades to the agent
        # default, which looks like "the setting reverted on its own" and is
        # very hard to trace from the UI.
        logger.warning(
            "contextvars_setup: session project dirs lookup failed; "
            "falling back to the agent default",
            exc_info=True,
        )
        return None


__all__ = ["ContextVarsSetupHook"]
