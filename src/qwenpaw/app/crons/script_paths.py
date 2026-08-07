# -*- coding: utf-8 -*-
"""Where a cron job's own batch scripts live on disk.

Layout, per workspace::

    <workspace_dir>/cron_jobs/<job_id>/batch/<name>.json

A script belongs to exactly one job. There is no shared pool: two jobs
that want the same recipe hold two independent copies, so editing one
cannot change what the other runs at 3 a.m., and deleting a job cannot
leave another one dangling.

This is a **leaf module** on purpose — it imports nothing but ``pathlib``
and ``re``. ``crons/preprocess.py`` is on the ``jobs.json`` load path and
must not pull in ``agents.tools.run_tool_batch``'s module-scope imports
(psutil, html2text, browser control), which is why the batch store used
to reach *backwards* into ``preprocess`` for the pool path. Both sides now
import from here instead and that inverted dependency is gone.

Every function here is **fail-closed and never raises**: the job spec
holding these names is not re-validated on load, and a preprocess runs
unattended, so an unresolvable name has to become a reported failure
rather than a read of some other file on disk.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

#: Per-workspace root holding one directory per cron job.
CRON_JOBS_DIRNAME = "cron_jobs"
#: Subdirectory of a job's directory holding its batch scripts. Matches
#: ``TEMPLATE_BATCH_DIR`` so a template package's ``batch/`` maps across
#: without renaming anything.
JOB_SCRIPTS_DIRNAME = "batch"

#: DOS device names, which Windows resolves before the filesystem does —
#: ``CON.json`` opens the console rather than creating a file, so a write
#: appears to succeed and the script is simply not there afterwards.
#: Defined here rather than in ``tool_batches.store`` so this module stays
#: a leaf; that module imports the name from here.
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{index}" for index in range(1, 10)]
    + [f"LPT{index}" for index in range(1, 10)],
)

#: A job id is a path segment now, so it needs its own grammar. Server
#: generated ids are uuid4, but ``_heartbeat`` / ``_dream`` are synthesized
#: and legacy files may hold anything, so this is deliberately wider than
#: uuid — it is a filesystem-safety gate, not an identity check. Strict
#: uuid enforcement belongs at the write boundary
#: (``CronManager.create_or_replace_job``), not here.
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")


def is_safe_job_id(job_id: object) -> bool:
    """Whether ``job_id`` may be used as a directory name.

    Rejects anything that could escape the workspace or resolve to
    something other than a plain directory. Never raises, so it is usable
    from the resolution path that promises a message rather than an
    exception.
    """
    if not isinstance(job_id, str):
        return False
    if not _JOB_ID_PATTERN.match(job_id):
        # Covers empty, NUL bytes, separators, leading dot/dash, spaces,
        # unicode, and anything over 64 characters.
        return False
    if job_id in {".", ".."}:
        # Unreachable through the pattern (a leading dot is refused), kept
        # as a belt-and-braces assertion of the thing that matters most.
        return False
    if job_id.upper() in WINDOWS_RESERVED_NAMES:
        return False
    if job_id.upper().split(".", 1)[0] in WINDOWS_RESERVED_NAMES:
        # `CON.1` resolves to the console device on Windows too.
        return False
    return True


def cron_jobs_root(workspace_dir: Path | str) -> Path:
    """Return ``<workspace_dir>/cron_jobs``."""
    return Path(workspace_dir) / CRON_JOBS_DIRNAME


def job_scripts_dir(
    workspace_dir: Path | str,
    job_id: object,
) -> Optional[Path]:
    """Return a job's scripts directory, or ``None`` if the id is unsafe.

    Does not create anything — callers that write go through
    ``ToolBatchService``, which stages and scans first.
    """
    if not is_safe_job_id(job_id):
        return None
    root = cron_jobs_root(workspace_dir)
    candidate = (root / str(job_id) / JOB_SCRIPTS_DIRNAME).resolve()
    # Independent re-check: the id grammar already forbids separators, but
    # a symlinked workspace or a future grammar change must not silently
    # widen this.
    if not candidate.is_relative_to(root.resolve()):
        return None
    return candidate


def resolve_job_script(
    workspace_dir: Path | str,
    job_id: object,
    name: str,
) -> Optional[Path]:
    """Resolve one of a job's scripts to a file, or ``None`` if absent.

    Refuses any name with a path separator: the job spec must not be able
    to point the runner at an arbitrary file. ``.json`` is appended when
    missing, and the suffix is compared case-insensitively to match
    ``run_tool_batch._load_batch_file`` — a ``.JSON`` name that resolved
    here would be rejected by the loader anyway.
    """
    directory = job_scripts_dir(workspace_dir, job_id)
    if directory is None:
        return None
    candidate = (name or "").strip()
    if not candidate or "/" in candidate or "\\" in candidate:
        return None
    if candidate in {".", ".."}:
        return None
    if "\x00" in candidate:
        return None
    if not candidate.lower().endswith(".json"):
        candidate = f"{candidate}.json"

    path = (directory / candidate).resolve()
    if not path.is_relative_to(directory.resolve()):
        return None
    return path if path.is_file() else None


def iter_job_script_dirs(
    workspace_dir: Path | str,
) -> Iterator[tuple[str, Path]]:
    """Yield ``(job_id, scripts_dir)`` for every job directory present.

    Feeds the workspace-wide listing the picker uses to browse other
    jobs' scripts, and the orphan reaper. Unsafe directory names are
    skipped rather than reported: they cannot have been created by this
    code, so there is nothing actionable to say about them.
    """
    root = cron_jobs_root(workspace_dir)
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        # Delegate rather than joining here, so the safety gate and the
        # resolved-path shape have exactly one definition. A caller that
        # compares a yielded path against `job_scripts_dir` must get the
        # same string back.
        scripts = job_scripts_dir(workspace_dir, entry.name)
        if scripts is not None and scripts.is_dir():
            yield entry.name, scripts


__all__ = [
    "CRON_JOBS_DIRNAME",
    "JOB_SCRIPTS_DIRNAME",
    "WINDOWS_RESERVED_NAMES",
    "cron_jobs_root",
    "is_safe_job_id",
    "iter_job_script_dirs",
    "job_scripts_dir",
    "resolve_job_script",
]
