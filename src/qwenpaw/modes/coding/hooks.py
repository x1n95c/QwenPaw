# -*- coding: utf-8 -*-
"""Coding mode hooks."""

from __future__ import annotations

from ..base import ModeGatedHook
from ...runtime.hooks import HookContext, HookResult
from ...runtime.phases import Phase


class ProjectDirInjectionHook(ModeGatedHook):
    """Expose the effective project dir under ``ctx.mode_state["coding"]``.

    The value is *read* here, not decided here: resolution happens once in
    ``PRE_DISPATCH`` (session → agent → fork → …) and is published on the
    ``current_project_dir`` ContextVar. This hook only mirrors it into
    mode state for coding-specific consumers, so Coding Mode cannot drift
    to a different directory than the file and shell tools use.
    """

    phase = Phase.PRE_AGENT_BUILD
    name = "coding_mode_project_dir"
    priority = 30

    async def _run(self, ctx: HookContext) -> HookResult:
        from ...config.context import get_current_project_dir
        from ...config.project_dir import agent_project_dir_from_config

        project_dir = get_current_project_dir()
        if project_dir is not None:
            ctx.mode_state.setdefault("coding", {})["project_dir"] = str(
                project_dir,
            )
            return HookResult()

        # No resolved value (e.g. a non-request code path): fall back to
        # the agent-level default so behaviour degrades gracefully.
        cfg = ctx.agent_config
        if cfg is None:
            try:
                from ...config.config import load_agent_config

                cfg = load_agent_config(ctx.agent_id)
            except Exception:
                return HookResult()
        configured = agent_project_dir_from_config(cfg)
        if configured:
            ctx.mode_state.setdefault("coding", {})[
                "project_dir"
            ] = configured
        return HookResult()


__all__ = ["ProjectDirInjectionHook"]
