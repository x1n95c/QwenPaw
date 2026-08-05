# -*- coding: utf-8 -*-
"""Models for the shared ``run_tool_batch`` script pool.

Scripts are stored as flat ``<name>.json`` files under
``WORKING_DIR/tool_batches`` — the same directory cron preprocesses
resolve pool scripts from. The filesystem is the source of truth: no
manifest, no frontmatter. A file is either a bare JSON array of actions
or an object with an ``actions`` array, exactly what
``run_tool_batch._load_batch_file`` accepts; an object may additionally
carry a top-level ``description`` key, which the loader ignores.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolBatchInfo(BaseModel):
    """A pool batch script as returned to callers."""

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
    """A pool batch script including its parsed content."""

    #: The batch JSON as stored: an actions array or an object with an
    #: ``actions`` array (plus optional ``description``).
    content: Any = None


class CreateToolBatchRequest(BaseModel):
    """Create a pool batch script from structured fields."""

    name: str
    #: An actions array or an object with an ``actions`` array.
    content: Any
    #: Explicit description. ``None`` derives it from a top-level
    #: ``description`` key in object-form content; an empty string clears.
    description: Optional[str] = None


class UpdateToolBatchRequest(BaseModel):
    """Patch an existing pool batch script.

    Every field is optional and ``None`` means "leave as-is", so a
    partial edit never blanks out the part the client did not render.
    """

    #: New batch content; ``None`` keeps the current content.
    content: Optional[Any] = None
    #: New description; ``None`` keeps the current description, an empty
    #: string clears it.
    description: Optional[str] = None


__all__ = [
    "CreateToolBatchRequest",
    "ToolBatchDetail",
    "ToolBatchInfo",
    "UpdateToolBatchRequest",
]
