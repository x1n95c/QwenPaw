# -*- coding: utf-8 -*-
"""Unified resolution of the **effective project directory**.

``workspace_dir`` stores the agent's own state.  ``project_dir`` is where
the agent actually works.  Every subsystem that needs "the directory the
user's task lives in" must go through :func:`resolve_effective_project_dir`
so the precedence rules stay in exactly one place.

Precedence (highest first)::

    validated fork project override    # sandbox boundary for sub-agents
    > active mode runtime override     # e.g. Mission's pinned source project
    > trusted ephemeral request override   # ACP / cron task-level dir
    > persisted Chat Session override  # user picked a dir for this chat
    > Agent project_dir                # agent-level default
    > workspace_dir                    # fallback: behave like before

The fork override is deliberately highest: a forked sub-agent must not be
able to escape the worktree it was assigned, no matter what the session or
agent config says.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path, None]

# Provenance labels, ordered by precedence. Also used in audit events.
SOURCE_FORK = "fork"
SOURCE_MODE = "mode"
SOURCE_REQUEST = "request"
SOURCE_SESSION = "session"
SOURCE_AGENT = "agent"
SOURCE_WORKSPACE_FALLBACK = "workspace_fallback"


class ResolvedProjectDir(NamedTuple):
    """Outcome of project-directory resolution."""

    path: Path
    source: str
    exists: bool

    @property
    def is_workspace_fallback(self) -> bool:
        """True when no project dir was configured anywhere."""
        return self.source == SOURCE_WORKSPACE_FALLBACK


def normalize_project_dir(raw: PathLike) -> Optional[Path]:
    """Normalize a user-supplied project path to an absolute path.

    Returns ``None`` for empty / blank input.  Does **not** require the
    path to exist — a configured-but-missing directory must survive
    round-trips so the UI can flag it as unavailable instead of silently
    resetting the user's config.

    ``os.path.normpath`` is used rather than ``Path.resolve()`` so a
    missing path still normalizes, and symlinks are not collapsed on
    platforms where that would surprise the user.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    expanded = os.path.expanduser(text)
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return Path(os.path.normpath(expanded))


def same_dir(left: PathLike, right: PathLike) -> bool:
    """Platform-aware directory equality.

    Windows and macOS default to case-insensitive filesystems, so a plain
    string comparison would treat ``C:\\Repo`` and ``c:\\repo`` as
    different projects and start two Git watchdogs for one repository.
    """
    a = normalize_project_dir(left)
    b = normalize_project_dir(right)
    if a is None or b is None:
        return a is None and b is None
    if _CASE_INSENSITIVE_FS:
        return str(a).casefold() == str(b).casefold()
    return str(a) == str(b)


# Windows and macOS default to case-insensitive filesystems. Treating
# "C:\Repo" and "c:\repo" as different projects would start two Git
# watchdogs for one repository, so compare case-insensitively there.
_CASE_INSENSITIVE_FS = sys.platform in ("win32", "cygwin", "darwin")


def is_within(child: PathLike, parent: PathLike) -> bool:
    """Return True when *child* is *parent* or lives inside it.

    Uses path-component comparison rather than ``str.startswith`` so that
    ``/repo-backup`` is not treated as being inside ``/repo``.
    """
    c = normalize_project_dir(child)
    p = normalize_project_dir(parent)
    if c is None or p is None:
        return False
    if same_dir(c, p):
        return True
    if _CASE_INSENSITIVE_FS:
        # relative_to() is case-sensitive, which is wrong on these hosts.
        c_parts = [part.casefold() for part in c.parts]
        p_parts = [part.casefold() for part in p.parts]
        return c_parts[: len(p_parts)] == p_parts
    try:
        c.relative_to(p)
        return True
    except ValueError:
        return False


def agent_project_dir_from_config(agent_config: Any) -> Optional[str]:
    """Read the agent-level default project dir from a config object.

    Accepts either a pydantic model or a plain dict, and understands the
    legacy ``coding_mode.project_dir`` location so callers keep working
    against configs that have not been migrated yet.
    """
    if agent_config is None:
        return None

    if isinstance(agent_config, dict):
        top = agent_config.get("project_dir")
        if top:
            return str(top)
        legacy = (agent_config.get("coding_mode") or {}).get("project_dir")
        return str(legacy) if legacy else None

    top = getattr(agent_config, "project_dir", None)
    if top:
        return str(top)
    cm = getattr(agent_config, "coding_mode", None)
    legacy = getattr(cm, "project_dir", None) if cm is not None else None
    return str(legacy) if legacy else None


def session_project_dir_from_meta(meta: Any) -> Optional[str]:
    """Extract a session-level override from ``ChatSpec.meta``.

    The override lives in a controlled namespace
    (``meta["runtime_context"]["project_dir"]``) rather than at the top of
    ``meta`` so a generic meta-patch from the frontend cannot clobber
    unrelated system metadata.
    """
    if not isinstance(meta, dict):
        return None
    runtime_context = meta.get("runtime_context")
    if not isinstance(runtime_context, dict):
        return None
    value = runtime_context.get("project_dir")
    return str(value) if value else None


def resolve_effective_project_dir(
    *,
    workspace_dir: PathLike,
    agent_project_dir: PathLike = None,
    session_project_dir: PathLike = None,
    request_override: PathLike = None,
    mode_override: PathLike = None,
    fork_project_dir: PathLike = None,
) -> ResolvedProjectDir:
    """Resolve the effective project directory for one turn.

    Args:
        workspace_dir: The agent's internal workspace; the final fallback.
        agent_project_dir: Agent-level default (``agent.json`` top level).
        session_project_dir: Persisted per-chat override.
        request_override: Trusted ephemeral override (ACP, cron task).
        mode_override: Override pinned by an active mode (e.g. Mission).
        fork_project_dir: **Already validated** worktree path for a forked
            sub-agent. Callers must validate it before passing it in —
            this function trusts it and gives it top precedence.

    Returns:
        A :class:`ResolvedProjectDir` with the path, its provenance, and
        whether it currently exists on disk.

    Raises:
        ValueError: If *workspace_dir* is empty, since there would be no
            fallback and silently picking the process cwd would let agent
            state escape into whatever directory the server was started in.
    """
    candidates = (
        (fork_project_dir, SOURCE_FORK),
        (mode_override, SOURCE_MODE),
        (request_override, SOURCE_REQUEST),
        (session_project_dir, SOURCE_SESSION),
        (agent_project_dir, SOURCE_AGENT),
    )

    for raw, source in candidates:
        normalized = normalize_project_dir(raw)
        if normalized is not None:
            return ResolvedProjectDir(
                path=normalized,
                source=source,
                exists=normalized.is_dir(),
            )

    fallback = normalize_project_dir(workspace_dir)
    if fallback is None:
        raise ValueError(
            "resolve_effective_project_dir requires a workspace_dir "
            "fallback; got an empty value",
        )
    return ResolvedProjectDir(
        path=fallback,
        source=SOURCE_WORKSPACE_FALLBACK,
        exists=fallback.is_dir(),
    )


def migrate_project_dir_in_place(data: dict[str, Any]) -> bool:
    """Lift a legacy ``coding_mode.project_dir`` to the top level.

    Mutates *data* (a raw ``agent.json`` dict) and returns True when
    something changed, so the caller can persist it once.

    Migration matrix:

    =============  ==============  ==========================================
    top-level      legacy          result
    =============  ==============  ==========================================
    absent         existing path   move legacy value up
    absent         missing path    move it up anyway; UI flags unavailable
    present        present         keep top-level, drop legacy, log it
    present        absent          unchanged
    absent         absent          stays None; runtime falls back to workspace
    =============  ==============  ==========================================

    Idempotent: once the legacy key is gone this is a no-op. Deliberately
    does **not** create, copy or delete any directory — a configured path
    that no longer exists is preserved so the user can see and fix it
    rather than having their config silently reset.
    """
    coding_mode = data.get("coding_mode")
    if not isinstance(coding_mode, dict):
        return False
    if "project_dir" not in coding_mode:
        return False

    legacy_raw = coding_mode.pop("project_dir")
    legacy = normalize_project_dir(legacy_raw)
    top = normalize_project_dir(data.get("project_dir"))

    if top is not None:
        if legacy is not None and not same_dir(top, legacy):
            logger.info(
                "project_dir migration: keeping top-level %s and dropping "
                "legacy coding_mode.project_dir %s",
                top,
                legacy,
            )
        # Re-normalize the surviving top-level value for consistency.
        data["project_dir"] = str(top)
        return True

    if legacy is None:
        # Legacy key existed but was null/blank: just drop it.
        return True

    data["project_dir"] = str(legacy)
    if not legacy.is_dir():
        logger.warning(
            "project_dir migration: migrated %s but it does not exist; "
            "it will be reported as unavailable rather than reset",
            legacy,
        )
    else:
        logger.info("project_dir migration: lifted %s to top level", legacy)
    return True


def describe_for_audit(
    resolved: ResolvedProjectDir,
    workspace_dir: PathLike,
) -> dict[str, Any]:
    """Build the dual-directory context recorded on audit events."""
    return {
        "workspace_dir": str(normalize_project_dir(workspace_dir) or ""),
        "project_dir": str(resolved.path),
        "project_dir_source": resolved.source,
        "project_dir_exists": resolved.exists,
    }


__all__ = [
    "ResolvedProjectDir",
    "SOURCE_AGENT",
    "SOURCE_FORK",
    "SOURCE_MODE",
    "SOURCE_REQUEST",
    "SOURCE_SESSION",
    "SOURCE_WORKSPACE_FALLBACK",
    "agent_project_dir_from_config",
    "describe_for_audit",
    "is_within",
    "migrate_project_dir_in_place",
    "normalize_project_dir",
    "resolve_effective_project_dir",
    "same_dir",
    "session_project_dir_from_meta",
]
