# -*- coding: utf-8 -*-
"""Resolve and normalize project directories for an agent.

The agent operates on two distinct locations:

* ``workspace_dir`` — the agent's **internal** storage root (config,
  memory, sessions, skills, media, cache). Internal subsystems must
  keep resolving against it, no matter which project is active.
* ``project_dirs`` — the directories the agent **works in**. An ordered
  list; the first entry is the **primary** project directory (the base
  for relative paths in file tools and the default ``cwd`` for shell
  commands). Additional entries are extra project directories the user
  bound to the agent/chat: fully granted by governance and described in
  the prompt, but addressed by absolute path — relative paths never
  resolve against them, which keeps resolution unambiguous.

Effective-directory precedence, highest first::

    fork worktree (replaces the primary; the rest is inherited)
    mode pin (Mission snapshots the whole list for the run)
    trusted request override (ACP / cron; becomes the primary)
    per-chat session override (whole list, persisted on the chat)
    agent-level default list
    workspace fallback (nothing configured; primary = workspace)

A path that no longer exists is **surfaced, not dropped**: silently
resetting to another directory would scatter the user's files.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Hard cap on how many directories one agent/chat may bind. Keeps the
# prompt block and the governance rule set bounded no matter what a
# client sends.
MAX_PROJECT_DIRS = 10

# Labels are rendered into the system prompt; long ones are truncated.
MAX_PROJECT_DIR_LABEL_LENGTH = 50

# Provenance of the effective list, highest precedence first. UI and
# audit use these verbatim.
SOURCE_FORK = "fork"
SOURCE_MODE = "mode"
SOURCE_REQUEST = "request"
SOURCE_SESSION = "session"
SOURCE_AGENT = "agent"
SOURCE_WORKSPACE_FALLBACK = "workspace_fallback"

ProjectDirSource = str

# One project directory entry as it appears in configs / chat meta /
# API payloads: a path plus an optional user-facing label.
RawProjectDirEntry = Union[str, Path, dict, Sequence[Any], Any]


def normalize_project_dir(raw: Any) -> Optional[Path]:
    """Normalize a user-supplied project path to an absolute path.

    Returns ``None`` for empty / blank input. Does **not** require the
    path to exist — a configured-but-missing directory must survive
    round-trips so the UI can flag it as unavailable instead of silently
    resetting the user's config.

    ``os.path.normpath`` is used rather than ``Path.resolve()`` so a
    missing path still normalizes, and symlinks are not collapsed on
    platforms where that would surprise the user (macOS keeps ``/var``
    -> ``/private/var`` while the shell reports the original path).
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


def normalize_project_dir_label(raw: Any) -> Optional[str]:
    """Trim a user-provided label; None/blank becomes None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return text[:MAX_PROJECT_DIR_LABEL_LENGTH]


def same_dir(a: PathLike, b: PathLike) -> bool:
    """Compare two directories, ignoring case and trailing separators.

    On case-insensitive filesystems (macOS, Windows) ``/Repo`` and
    ``/repo`` are the same directory; naive string comparison would
    treat them as distinct and let stale entries survive a re-save.
    """
    left = normalize_project_dir(a)
    right = normalize_project_dir(b)
    if left is None or right is None:
        return left is right
    return str(left).casefold() == str(right).casefold()


def is_within(path: PathLike, root: PathLike) -> bool:
    """Return True when *path* is *root* itself or lives underneath it."""
    target = normalize_project_dir(path)
    base = normalize_project_dir(root)
    if target is None or base is None:
        return False
    if same_dir(target, base):
        return True
    return str(target).casefold().startswith(str(base).casefold() + "/")


def coerce_project_dir_entry(
    raw: RawProjectDirEntry,
) -> Optional[tuple[Path, Optional[str]]]:
    """Coerce one raw entry into ``(path, label)``.

    Accepts the shapes that reach the resolver:

    * plain path strings / ``Path`` objects (no label)
    * ``{"path": ..., "label": ...}`` dicts (config, meta, API payloads)
    * ``ProjectDirEntry`` pydantic models (attribute access)
    * ``(path, label)`` sequences

    Returns ``None`` for blank/unusable input.
    """
    if raw is None:
        return None

    label: Any = None
    path_raw: Any = raw

    if isinstance(raw, dict):
        path_raw = raw.get("path")
        label = raw.get("label")
    elif isinstance(raw, (list, tuple)):
        if not raw:
            return None
        path_raw = raw[0]
        label = raw[1] if len(raw) > 1 else None
    elif not isinstance(raw, (str, Path)):
        # Pydantic model or similar: attribute access.
        path_raw = getattr(raw, "path", None)
        label = getattr(raw, "label", None)

    normalized = normalize_project_dir(path_raw)
    if normalized is None:
        return None
    return normalized, normalize_project_dir_label(label)


def normalize_project_dir_list(
    raw: Any,
) -> list[tuple[Path, Optional[str]]]:
    """Normalize a raw project-dir list: coerce, dedupe, cap.

    Order is preserved — index 0 is the primary project directory.
    Dedupe keeps the first occurrence (and its label) and is
    case-insensitive via :func:`same_dir`. Entries beyond
    ``MAX_PROJECT_DIRS`` are dropped with a warning.

    ``None`` (as opposed to an empty list) is treated as an empty list
    here; callers that need to distinguish "absent" from "empty" must
    check before calling.
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]

    entries: list[tuple[Path, Optional[str]]] = []
    for item in raw:
        coerced = coerce_project_dir_entry(item)
        if coerced is None:
            continue
        path, label = coerced
        if any(same_dir(path, existing) for existing, _ in entries):
            continue
        entries.append((path, label))
        if len(entries) >= MAX_PROJECT_DIRS:
            logger.warning(
                "project_dirs: more than %d entries supplied; "
                "keeping the first %d",
                MAX_PROJECT_DIRS,
                MAX_PROJECT_DIRS,
            )
            break
    return entries


@dataclass(frozen=True)
class ResolvedProjectDir:
    """One effective project directory after resolution."""

    path: Path
    label: Optional[str] = None
    exists: bool = True


@dataclass(frozen=True)
class ResolvedProjectDirs:
    """The effective project-directory list for one turn.

    ``dirs`` holds only explicitly configured entries; ``[0]`` is the
    primary. When nothing is configured ``dirs`` is empty and the
    primary falls back to ``workspace_dir`` (``source`` says so).
    """

    dirs: tuple[ResolvedProjectDir, ...]
    source: ProjectDirSource
    workspace_dir: Path

    @property
    def is_workspace_fallback(self) -> bool:
        return not self.dirs

    @property
    def primary(self) -> ResolvedProjectDir:
        """The directory tools resolve relative paths against."""
        if self.dirs:
            return self.dirs[0]
        return ResolvedProjectDir(
            path=self.workspace_dir,
            label=None,
            exists=self.workspace_dir.is_dir(),
        )

    @property
    def primary_path(self) -> Path:
        return self.primary.path

    @property
    def paths(self) -> list[Path]:
        return [entry.path for entry in self.dirs]


def resolve_effective_project_dirs(
    workspace_dir: PathLike,
    *,
    agent_project_dirs: Any = None,
    session_project_dirs: Optional[Any] = None,
    request_override: Any = None,
    mode_override: Optional[Any] = None,
    fork_project_dir: Optional[PathLike] = None,
) -> ResolvedProjectDirs:
    """Resolve the effective project-directory list for a request.

    Precedence, highest first:

    1. ``fork_project_dir`` — a forked subagent's worktree replaces the
       primary; the remaining entries are inherited. Subagents must
       resolve paths against their worktree, and the rest of the list
       stays user-configured trusted paths.
    2. ``mode_override`` — a running mode (Mission) snapshots the whole
       list at start so a mid-run session switch cannot move it.
    3. ``request_override`` — a trusted per-run path (ACP / cron) that
       becomes the primary; the rest is inherited.
    4. ``session_project_dirs`` — per-chat override. ``None`` means
       "not set" (inherit); an empty list means "explicitly none".
    5. ``agent_project_dirs`` — the agent-level default list.
    6. Workspace fallback when nothing is configured.

    Raises:
        ValueError: workspace_dir is empty or not absolute.
    """
    normalized_workspace = normalize_project_dir(workspace_dir)
    if normalized_workspace is None or not normalized_workspace.is_absolute():
        raise ValueError(f"Invalid workspace_dir: {workspace_dir!r}")

    if session_project_dirs is not None:
        entries = normalize_project_dir_list(session_project_dirs)
        source: ProjectDirSource = SOURCE_SESSION
    else:
        entries = normalize_project_dir_list(agent_project_dirs)
        source = SOURCE_AGENT if entries else SOURCE_WORKSPACE_FALLBACK

    if request_override is not None:
        override = coerce_project_dir_entry(request_override)
        if override is not None:
            entries = [override] + [
                entry
                for entry in entries
                if not same_dir(entry[0], override[0])
            ]
            source = SOURCE_REQUEST

    if mode_override is not None:
        pinned = normalize_project_dir_list(mode_override)
        entries = pinned
        source = SOURCE_MODE

    if fork_project_dir is not None:
        worktree = normalize_project_dir(fork_project_dir)
        if worktree is not None:
            entries = [(worktree, None)] + [
                entry for entry in entries if not same_dir(entry[0], worktree)
            ]
            source = SOURCE_FORK

    dirs = tuple(
        ResolvedProjectDir(path=path, label=label, exists=path.is_dir())
        for path, label in entries
    )
    return ResolvedProjectDirs(
        dirs=dirs,
        source=source,
        workspace_dir=normalized_workspace,
    )


def agent_project_dirs_from_config(config: Any) -> list[dict]:
    """Return the agent-level project-directory list from a config object.

    Each entry is ``{"path": str, "label": str | None}``, primary first.
    Accepts either a pydantic model or a plain dict, and understands the
    legacy locations (singular ``project_dir``, ``coding_mode.project_dir``)
    so callers keep working against configs that have not been migrated
    yet. Returns an empty list when nothing is configured. Tolerates
    missing or malformed attributes: a broken config degrades to "no
    project", not to a crash.
    """
    if config is None:
        return []

    if isinstance(config, dict):
        raw = config.get("project_dirs")
        if not raw:
            legacy = config.get("project_dir") or (
                config.get("coding_mode") or {}
            ).get("project_dir")
            raw = [legacy] if legacy else []
    else:
        raw = getattr(config, "project_dirs", None)
        if not raw:
            legacy = getattr(config, "project_dir", None)
            if not legacy:
                cm = getattr(config, "coding_mode", None)
                legacy = getattr(cm, "project_dir", None) if cm else None
            raw = [legacy] if legacy else []

    return [
        {"path": str(path), "label": label}
        for path, label in normalize_project_dir_list(raw)
    ]


def agent_primary_project_dir_from_config(config: Any) -> Optional[str]:
    """Return the agent-level **primary** project dir (first entry).

    Convenience for consumers that only care about the primary:
    code/Git tooling roots, LSP detection, fork source.
    """
    entries = agent_project_dirs_from_config(config)
    if not entries:
        return None
    return entries[0]["path"]


# ---------------------------------------------------------------------------
# Project display name
#
# A name for the directory list *as a unit*, separate from the per-directory
# labels. Purely descriptive — it never takes part in resolving a path — so
# it is safe for it to be absent, and it is derived rather than persisted
# when the user has not typed one.
# ---------------------------------------------------------------------------

MAX_PROJECT_NAME_LEN = 60


def normalize_project_name(raw: Any) -> Optional[str]:
    """Coerce a project display name; ``None`` when blank or unusable."""
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name:
        return None
    return name[:MAX_PROJECT_NAME_LEN]


def agent_project_name_from_config(config: Any) -> Optional[str]:
    """Return the agent-level project display name, if one is set."""
    if config is None:
        return None
    if isinstance(config, dict):
        return normalize_project_name(config.get("project_name"))
    return normalize_project_name(getattr(config, "project_name", None))


def session_project_name_from_meta(meta: Optional[dict]) -> Optional[str]:
    """Read the per-chat project display name override, if any."""
    if not isinstance(meta, dict):
        return None
    runtime_context = meta.get("runtime_context")
    if not isinstance(runtime_context, dict):
        return None
    return normalize_project_name(runtime_context.get("project_name"))


def default_project_name(entries: Any) -> Optional[str]:
    """Derive a display name from the directory list.

    The primary entry's label, else its basename, so the UI always has
    something to show without persisting a name nobody typed.
    """
    normalized = normalize_project_dir_list(entries)
    if not normalized:
        return None
    path, label = normalized[0]
    if label:
        return label
    return path.name or str(path)


def resolve_project_name(
    *,
    entries: Any,
    session_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Optional[str]:
    """Pick the display name to show for a project.

    Mirrors the directory precedence: a session override beats the agent
    default, and a derived name is the last resort so the UI is never
    blank.
    """
    for candidate in (session_name, agent_name):
        normalized = normalize_project_name(candidate)
        if normalized:
            return normalized
    return default_project_name(entries)


def session_project_dirs_from_meta(meta: Optional[dict]) -> Optional[list]:
    """Read the per-chat project-directory override from chat metadata.

    Returns the persisted list (possibly empty), or ``None`` when the
    chat has no override and should inherit the agent default. Entries
    are normalized on the way out so callers get clean data even if the
    stored metadata predates the list format.
    """
    if not isinstance(meta, dict):
        return None
    runtime_context = meta.get("runtime_context")
    if not isinstance(runtime_context, dict):
        return None

    stored = runtime_context.get("project_dirs")
    if stored is not None:
        if not isinstance(stored, list):
            stored = [stored]
        return [
            {"path": str(path), "label": label}
            for path, label in normalize_project_dir_list(stored)
        ]

    # Draft-era chats stored a single path under "project_dir".
    legacy = runtime_context.get("project_dir")
    if isinstance(legacy, str) and legacy.strip():
        entries = normalize_project_dir_list([legacy])
        if entries:
            return [
                {"path": str(path), "label": label}
                for path, label in entries
            ]
    return None


def migrate_project_dirs_in_place(data: dict) -> bool:
    """Lift legacy project-dir fields into ``project_dirs`` in raw JSON.

    Handles two legacy shapes:

    * ``coding_mode.project_dir`` — the pre-refactor Coding Mode field
    * top-level ``project_dir`` (singular string) — an earlier draft of
      this feature

    Both become a single-entry ``project_dirs`` list. When ``project_dirs``
    already has entries the legacy values are dropped (top-level wins)::

        project_dirs  legacy present   behaviour
        ============  ==============   =================================
        present       present          keep list, drop legacy, log it
        absent        present          list = [legacy entry]
        absent        absent           stays absent; runtime falls back
                                       to the workspace

    Idempotent: once the legacy keys are gone this is a no-op.
    Deliberately does **not** create, copy or delete any directory — a
    configured path that no longer exists is preserved so the user can
    see and fix it rather than having their config silently reset.
    """
    changed = False

    legacy_raw = None
    coding_mode = data.get("coding_mode")
    if isinstance(coding_mode, dict) and "project_dir" in coding_mode:
        legacy_raw = coding_mode.pop("project_dir")
        changed = True

    singular_raw = None
    if "project_dir" in data and not isinstance(data.get("project_dir"), list):
        singular_raw = data.pop("project_dir")
        changed = True

    existing = normalize_project_dir_list(data.get("project_dirs"))
    canonical = [
        {"path": str(path), "label": label} for path, label in existing
    ]

    if existing or "project_dirs" in data:
        for raw in (singular_raw, legacy_raw):
            candidate = coerce_project_dir_entry(raw)
            if candidate is not None and not any(
                same_dir(candidate[0], path) for path, _ in existing
            ):
                logger.info(
                    "project_dirs migration: keeping %s and dropping "
                    "legacy value %s",
                    [str(path) for path, _ in existing],
                    candidate[0],
                )
        if data.get("project_dirs") != canonical:
            data["project_dirs"] = canonical
            changed = True
        return changed

    raw = singular_raw if singular_raw is not None else legacy_raw
    entry = coerce_project_dir_entry(raw)
    if entry is None:
        # Legacy keys existed but were null/blank: they were popped above.
        return changed

    path, label = entry
    data["project_dirs"] = [{"path": str(path), "label": label}]
    if not path.is_dir():
        logger.warning(
            "project_dirs migration: migrated %s but it does not exist; "
            "it will be reported as unavailable rather than reset",
            path,
        )
    else:
        logger.info(
            "project_dirs migration: lifted %s into project_dirs",
            path,
        )
    return True


def describe_for_audit(
    resolved: ResolvedProjectDirs,
    workspace_dir: PathLike,
) -> dict[str, Any]:
    """Build the directory context recorded on audit events."""
    primary = resolved.primary
    return {
        "workspace_dir": str(normalize_project_dir(workspace_dir) or ""),
        "project_dir": str(primary.path),
        "project_dir_source": resolved.source,
        "project_dir_exists": primary.exists,
        "project_dirs": [str(entry.path) for entry in resolved.dirs],
    }


__all__ = [
    "MAX_PROJECT_DIRS",
    "MAX_PROJECT_DIR_LABEL_LENGTH",
    "MAX_PROJECT_NAME_LEN",
    "ResolvedProjectDir",
    "ResolvedProjectDirs",
    "SOURCE_AGENT",
    "SOURCE_FORK",
    "SOURCE_MODE",
    "SOURCE_REQUEST",
    "SOURCE_SESSION",
    "SOURCE_WORKSPACE_FALLBACK",
    "agent_primary_project_dir_from_config",
    "agent_project_dirs_from_config",
    "agent_project_name_from_config",
    "coerce_project_dir_entry",
    "default_project_name",
    "describe_for_audit",
    "is_within",
    "migrate_project_dirs_in_place",
    "normalize_project_dir",
    "normalize_project_dir_label",
    "normalize_project_dir_list",
    "normalize_project_name",
    "resolve_effective_project_dirs",
    "resolve_project_name",
    "same_dir",
    "session_project_dirs_from_meta",
    "session_project_name_from_meta",
]
