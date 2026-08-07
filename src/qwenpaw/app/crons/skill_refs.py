# -*- coding: utf-8 -*-
"""Resolving a job's skill refs to directories on disk.

Two roots, picked by ``CronJobSkillRef.source``::

    <workspace_dir>/skills/<name>/          # template is None
    <package_dir>/skills/<name>/            # template names the package

Nothing is copied or installed. A referenced skill is read where it lies,
which is why this deliberately does **not** consult the workspace skill
manifest: a cron job's skill need not be *enabled* for any agent, and
requiring that would put back the installation step this whole design
exists to remove.

The cost of skipping installation is that ``import_skill_dir``'s frontmatter
validation no longer runs on the way in, so a bundled skill with malformed
frontmatter now degrades at trigger time (``display_name`` falls back to the
directory name) instead of being rejected up front. Security scanning is
*not* skipped: ``scan_template_dir_or_raise`` covers a user package's whole
tree, ``skills/`` included.

Every function here is **fail-closed and never raises** — same contract as
``script_paths``, and for the same reason: a job spec is not re-validated on
load and a fire is unattended, so an unresolvable ref has to become a
reported failure rather than a read of some other directory.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Optional

from .models import CronJobSkillRef

logger = logging.getLogger(__name__)

#: Subdirectories of a skill that may hold ``run_tool_batch`` JSON.
#: ``scripts/`` is the layout ``make-skill`` teaches agents to write;
#: ``batch/`` mirrors a template package so a skill authored either way
#: works. ``references/`` is excluded on purpose — those are documents, not
#: executables — and so is the skill root, which keeps config sitting next
#: to ``SKILL.md`` from being copyable as a script.
SKILL_BATCH_DIRS = ("scripts", "batch")


def resolve_skill_dir(
    ref: CronJobSkillRef,
    workspace_dir: Path | str,
) -> Optional[Path]:
    """Locate the directory a skill ref names, or ``None``.

    ``None`` covers every way this can fail — unknown template, missing
    directory, absent ``SKILL.md``, a name the skill system would reject —
    because the caller turns all of them into the same "instructions
    unavailable" note in the prompt.
    """
    from ...agents.skill_system.store import (
        is_ignored_skill_entry,
        safe_skill_dir,
    )

    if is_ignored_skill_entry(ref.name):
        return None

    root = _skills_root(ref, workspace_dir)
    if root is None:
        return None

    try:
        skill_dir = safe_skill_dir(root, ref.name)
    except Exception:  # pylint: disable=broad-except
        # `safe_skill_dir` raises SkillsError for empty / NUL / separator /
        # escaping names. Importing that exception here would drag the
        # skill stack onto the module's import path for no gain.
        return None
    if not (skill_dir / "SKILL.md").is_file():
        return None
    return skill_dir


def resolve_skill_batch_script(
    ref: CronJobSkillRef,
    relative: str,
    workspace_dir: Path | str,
) -> Optional[Path]:
    """Resolve ``scripts/<file>.json`` or ``batch/<file>.json`` in a skill.

    Used when copying a script a skill carries into a job that will run it.
    Only those two subdirectories are addressable, and that is checked
    before any filesystem access so a traversal never even resolves a path
    — same shape as ``cron_templates.store.resolve_bundled_batch_script``.
    """
    if not relative or "\\" in relative:
        return None

    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if len(candidate.parts) < 2 or candidate.parts[0] not in SKILL_BATCH_DIRS:
        return None
    # `.lower()` to match `_load_batch_file`, which compares the same way.
    if candidate.suffix.lower() != ".json":
        return None

    skill_dir = resolve_skill_dir(ref, workspace_dir)
    if skill_dir is None:
        return None

    batch_root = (skill_dir / candidate.parts[0]).resolve()
    path = (skill_dir / candidate).resolve()
    if not path.is_relative_to(batch_root):
        return None
    return path if path.is_file() else None


def list_skill_batch_files(skill_dir: Path) -> list[str]:
    """Return the batch JSON a skill carries, as skill-relative paths.

    Recursive under each of ``SKILL_BATCH_DIRS``, sorted, and filtered the
    same way skill discovery filters everything else — the shape
    ``cron_templates.store.list_batch_files`` uses for a package.
    """
    from ...agents.skill_system.store import is_ignored_skill_entry

    found: list[str] = []
    for subdir in SKILL_BATCH_DIRS:
        root = skill_dir / subdir
        if not root.is_dir():
            continue
        try:
            paths = sorted(root.rglob("*.json"))
        except OSError as exc:
            logger.warning("Cannot list %s: %s", root, exc)
            continue
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(skill_dir)
            if any(is_ignored_skill_entry(part) for part in relative.parts):
                continue
            found.append(relative.as_posix())
    return found


def _skills_root(
    ref: CronJobSkillRef,
    workspace_dir: Path | str,
) -> Optional[Path]:
    """The ``skills/`` directory the ref should be looked up under."""
    from ...agents.skill_system.store import get_workspace_skills_dir

    if ref.source == "workspace":
        return get_workspace_skills_dir(Path(workspace_dir))

    from ..cron_templates.models import TEMPLATE_SKILLS_DIR
    from ..cron_templates.store import resolve_template_dir

    try:
        # Goes through `resolve_template_dir` so a user package shadows a
        # builtin of the same name exactly as it does everywhere else —
        # one definition of that precedence, not two.
        found = resolve_template_dir(str(ref.template), workspace_dir)
    except Exception:  # pylint: disable=broad-except
        # `normalize_template_name` raises on empty / NUL / reserved names.
        return None
    if found is None:
        return None
    return found[0] / TEMPLATE_SKILLS_DIR


__all__ = [
    "SKILL_BATCH_DIRS",
    "list_skill_batch_files",
    "resolve_skill_batch_script",
    "resolve_skill_dir",
]
