# -*- coding: utf-8 -*-
"""Models for a cron job's ``run_tool_batch`` scripts.

Stored as flat ``<name>.json`` files under
``<workspace_dir>/cron_jobs/<job_id>/batch/`` — the same directory a
preprocess step resolves its script from. The filesystem is the source of
truth: no manifest, no frontmatter. A file is either a bare array of actions
or an object with an ``actions`` array, exactly what
``run_tool_batch._load_batch_file`` accepts; an object may additionally
carry a top-level ``description`` key, which the loader ignores.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolBatchInfo(BaseModel):
    """A batch script as returned to callers."""

    name: str
    description: str = ""
    #: ``${args.*}`` placeholder names referenced anywhere in the script,
    #: sorted and de-duplicated. Derived with the executor's own regex so
    #: the UI and ``run_tool_batch`` can never disagree about what counts.
    arg_names: list[str] = Field(default_factory=list)
    action_count: int = 0
    #: The leading actions verbatim, so a list view can render a step
    #: preview without fetching every script's content. Capped at
    #: ``PREVIEW_ACTION_LIMIT``; ``action_count`` says how many there are
    #: in total. Kept raw rather than pre-summarised because the frontend
    #: already renders an action for the job form.
    preview_actions: list[Any] = Field(default_factory=list)
    updated_at: str = ""


class ToolBatchDetail(ToolBatchInfo):
    """A batch script including its parsed content."""

    #: The batch JSON as stored: an actions array or an object with an
    #: ``actions`` array (plus optional ``description``).
    content: Any = None


class CreateToolBatchRequest(BaseModel):
    """Create a batch script from structured fields."""

    name: str
    #: An actions array or an object with an ``actions`` array.
    content: Any
    #: Explicit description. ``None`` derives it from a top-level
    #: ``description`` key in object-form content; an empty string clears.
    description: Optional[str] = None


class UpdateToolBatchRequest(BaseModel):
    """Patch an existing batch script.

    Every field is optional and ``None`` means "leave as-is", so a
    partial edit never blanks out the part the client did not render.
    """

    #: New batch content; ``None`` keeps the current content.
    content: Optional[Any] = None
    #: New description; ``None`` keeps the current description, an empty
    #: string clears it.
    description: Optional[str] = None


class CopyToolBatchRequest(BaseModel):
    """Copy a script that belongs to something else into this job.

    Scripts are owned by one cron job, so browsing another job's or a
    template's scripts can only ever *copy* — the two end up independent,
    which is the point: editing one must not change what the other runs.

    Exactly one source. The source is named by fields rather than a packed
    ``template/path`` string so nothing has to be parsed, and so no
    identifier exists that could accidentally be stored as a step's script.

    ``from_skill_template`` is the one exception to "one field per source":
    it *qualifies* ``from_skill`` rather than being a source of its own,
    because a skill name alone is ambiguous — the same name can be an
    installed workspace skill and a skill bundled in a package. It mirrors
    ``CronJobSkillRef.{name, template}``, and setting it without
    ``from_skill`` is a 400 rather than something to guess at.
    """

    #: Source: another cron job in the same workspace.
    from_job_id: Optional[str] = None
    #: Source: a template package. Package-relative, e.g.
    #: ``batch/weather.json``.
    from_template: Optional[str] = None
    #: Source: a skill directory, by skill name. Skills carry batch JSON of
    #: their own — the layout ``make-skill`` teaches agents to write — and a
    #: job that references such a skill will usually want to run them.
    from_skill: Optional[str] = None
    #: Qualifies ``from_skill``: the template package bundling it, or
    #: ``None`` for a skill installed in this workspace.
    from_skill_template: Optional[str] = None
    #: File to copy. A bare script name for ``from_job_id``, a
    #: package-relative path for ``from_template``, a skill-relative path
    #: for ``from_skill`` (e.g. ``scripts/collect.json``).
    file: str
    #: Preferred name in this job. Taken when free, otherwise a free
    #: variant is chosen and returned — the response is authoritative.
    name: Optional[str] = None


class JobToolBatches(BaseModel):
    """One cron job's scripts, for the cross-job browser."""

    job_id: str
    job_name: str = ""
    batches: list[ToolBatchInfo] = Field(default_factory=list)


__all__ = [
    "CopyToolBatchRequest",
    "CreateToolBatchRequest",
    "JobToolBatches",
    "ToolBatchDetail",
    "ToolBatchInfo",
    "UpdateToolBatchRequest",
]
