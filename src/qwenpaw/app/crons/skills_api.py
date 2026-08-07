# -*- coding: utf-8 -*-
"""HTTP API listing the skills a cron job may attach.

One endpoint for both sources, deliberately. The job form's picker offers
installed workspace skills *and* skills bundled in template packages, and
splitting that across two routes would mean two hooks, two loading states,
and an option tree built over a heterogeneous union — for a distinction the
user does not care about while choosing. So the two are normalised into one
shape here, where the filesystem differences already live.

It also carries each skill's batch scripts, which is what lets the
preprocess picker offer a script a skill ships with. No second endpoint for
that: the list is one field on the same response.

Its own prefix rather than a static sibling under ``/cron``: such a sibling
only works while it is declared before the job-scoped ``/cron/jobs/{id}/…``
routes, which is a trap for whoever reorders them.

Never returns ``SKILL.md`` itself. The picker renders a name and a
description; the body is read from disk by the trigger path, where a 60 KB
string is one prompt block instead of a page of JSON.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from .skill_refs import list_skill_batch_files

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron-skills", tags=["cron"])


class CronSkillInfo(BaseModel):
    """A skill a cron job can attach, from either source.

    Declared here rather than in ``crons/models.py`` because that module is
    read every time ``jobs.json`` is loaded and this is a response shape,
    not part of a job spec.
    """

    #: Skill directory name — the stable identity, and what
    #: ``CronJobSkillRef.name`` holds. Not the frontmatter title.
    name: str
    #: Which root resolves it, matching ``CronJobSkillRef.source``.
    source: Literal["workspace", "template"]
    #: Template package that bundles it; empty for a workspace skill. Pairs
    #: with ``name`` to form the ref the job stores.
    template: str = ""
    #: Title literal and its i18n key, resolved key-first by the client —
    #: rendering it here would emit the untranslated literal for every
    #: builtin that ships ``metadata.title_key``. Mirrors
    #: ``TemplateBatchScriptInfo``.
    template_title: str = ""
    template_title_key: str = ""
    template_source: str = ""
    #: Frontmatter ``name``, for display only. Falls back to ``name``.
    display_name: str = ""
    description: str = ""
    #: Batch JSON the skill carries, skill-relative (``scripts/x.json``).
    #: Copyable into a job as a preprocess script.
    batch_files: list[str] = Field(default_factory=list)


@router.get("", response_model=list[CronSkillInfo])
async def list_cron_skills(
    request: Request,
    include_builtin: bool = Query(
        default=True,
        description="Include skills bundled with builtin templates",
    ),
) -> list[CronSkillInfo]:
    """List every skill a job in this workspace could attach."""
    from ..agent_context import get_agent_for_request

    workspace = await get_agent_for_request(request)
    workspace_dir = Path(workspace.workspace_dir)
    return await asyncio.to_thread(
        collect_cron_skills,
        workspace_dir,
        include_builtin,
    )


def collect_cron_skills(
    workspace_dir: Path,
    include_builtin: bool = True,
) -> list[CronSkillInfo]:
    """Installed workspace skills first, then template-bundled ones.

    Workspace skills lead because they are what a picker should offer
    without being asked; the bundled ones sit behind the expander.

    One unreadable skill logs a warning and is skipped rather than blanking
    the whole list — a picker that renders nothing reads like the feature is
    broken, which is a worse failure than one missing row.
    """
    return [
        *_workspace_skills(workspace_dir),
        *_template_skills(workspace_dir, include_builtin),
    ]


def _workspace_skills(workspace_dir: Path) -> list[CronSkillInfo]:
    from ...agents.skill_system.store import (
        get_workspace_skills_dir,
        is_ignored_skill_entry,
    )

    root = get_workspace_skills_dir(workspace_dir)
    if not root.is_dir():
        return []
    found: list[CronSkillInfo] = []
    for skill_dir in sorted(root.iterdir()):
        if is_ignored_skill_entry(skill_dir.name) or not skill_dir.is_dir():
            continue
        info = _describe(skill_dir, "workspace")
        if info is not None:
            found.append(info)
    return found


def _template_skills(
    workspace_dir: Path,
    include_builtin: bool,
) -> list[CronSkillInfo]:
    from ..cron_templates.models import TEMPLATE_SKILLS_DIR
    from ..cron_templates.service import CronTemplateService

    found: list[CronSkillInfo] = []
    try:
        templates = CronTemplateService(workspace_dir).list_templates(
            include_builtin,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Cannot list templates for skill picker: %s", exc)
        return []

    # Built on `list_templates` rather than walking the two roots again, so
    # the user-shadows-builtin precedence has exactly one definition — the
    # same one `resolve_template_dir` applies at trigger time.
    for template in templates:
        package_dir = Path(template.package_dir)
        for name in template.skills:
            info = _describe(
                package_dir / TEMPLATE_SKILLS_DIR / name,
                "template",
                template_name=template.name,
                template_title=template.title,
                template_title_key=template.title_key,
                template_source=template.source,
            )
            if info is not None:
                found.append(info)
    return found


def _describe(
    skill_dir: Path,
    source: Literal["workspace", "template"],
    *,
    template_name: str = "",
    template_title: str = "",
    template_title_key: str = "",
    template_source: str = "",
) -> Optional[CronSkillInfo]:
    """Read one skill's display metadata, or ``None`` if it is unusable."""
    from ...agents.skill_system.store import read_frontmatter_safe_from_path

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        # Same rule discovery applies everywhere else: no SKILL.md, not a
        # skill. Silent, because a stray directory is not a fault.
        return None

    try:
        # Frontmatter only. `read_skill_from_dir` would also read the whole
        # body and walk two directory trees, and still not give us the
        # frontmatter `name` — it reports the *directory* as `name`, by
        # design. The body belongs to the trigger path, not to a picker.
        post = read_frontmatter_safe_from_path(skill_md, skill_dir.name)
        batch_files = list_skill_batch_files(skill_dir)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Skipping unreadable skill '%s': %s", skill_dir, exc)
        return None

    return CronSkillInfo(
        name=skill_dir.name,
        source=source,
        template=template_name,
        template_title=template_title,
        template_title_key=template_title_key,
        template_source=template_source,
        display_name=str(post.get("name") or skill_dir.name),
        description=str(post.get("description") or ""),
        batch_files=batch_files,
    )


__all__ = ["CronSkillInfo", "collect_cron_skills", "router"]
