# -*- coding: utf-8 -*-
"""Rendering a job's attached skills into one prompt block.

The output matches what the ``/<skill_name>`` slash command injects, because
that is the shape every shipped skill is written against — see
``agents.skill_system.prompt.render_skill_invocation``. The difference is
that a cron job may attach several skills and that its request body is a
separate block after this one, so no task text is repeated per skill.

A skill that cannot be read becomes a **note in the prompt**, not silence
and not an aborted run. The reasoning is the one ``preprocess`` already
applies to a failed script: a model told nothing will fill the gap with
plausible invention, and that is worse than being told the instructions are
missing. Same reason an oversized body is reported rather than truncated —
instructions cut off mid-sentence produce confident wrong behaviour.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .models import MAX_SKILL_BODY_CHARS, CronJobSkillRef, CronJobSpec
from .skill_refs import resolve_skill_dir

logger = logging.getLogger(__name__)

#: What goes in the slash command's ``user's task:`` slot on the cron path.
#:
#: A cron job has no literal user text to put there — its request body is a
#: separate block *after* this one, and repeating it inside each of N skill
#: preambles would state the task N times. So the slot carries a fixed
#: sentence, which keeps the preamble byte-identical to ``/<skill> <input>``
#: and buys something useful besides: the body is already inlined, so a
#: helpful model opening the skill file with a tool would spend a turn
#: re-reading what it can already see.
CRON_SKILL_TASK = (
    "the scheduled task described below. The skill's full instructions are "
    "already included here, so do not call any tool to read the skill file."
)


def build_skill_prompt_block(
    job: CronJobSpec,
    workspace_dir: Optional[Path | str],
) -> str:
    """Render every attached skill, or ``""`` when there is nothing to add.

    One combined string for N skills, mirroring
    ``preprocess.build_prompt_block``, which also renders N scripts into
    one. That keeps the content-block layout of the final request
    predictable: skill block, preprocess block, then the original request.
    """
    from ...agents.skill_system.prompt import (
        load_skill_body,
        render_skill_invocation,
    )

    if not job.has_skills or not workspace_dir:
        return ""

    total = len(job.skills)
    sections: list[str] = []
    for index, ref in enumerate(job.skills, start=1):
        skill_dir = resolve_skill_dir(ref, workspace_dir)
        if skill_dir is None:
            body = _unavailable(job, ref, "not found")
        else:
            loaded = load_skill_body(skill_dir)
            if loaded is None:
                body = _unavailable(
                    job,
                    ref,
                    "SKILL.md missing or unreadable",
                )
            elif len(loaded[1]) > MAX_SKILL_BODY_CHARS:
                body = _unavailable(
                    job,
                    ref,
                    "instructions too large to attach",
                )
            else:
                body = render_skill_invocation(
                    loaded[0],
                    skill_dir,
                    loaded[1],
                    CRON_SKILL_TASK,
                )
        prefix = f"[{index}/{total}] " if total > 1 else ""
        sections.append(f"{prefix}{body}")

    block = "\n\n".join(sections)
    # Logged because this text is paid for on every single fire, so a job
    # that quietly grew a 40 KB preamble should be diagnosable from the log
    # rather than from a token bill.
    logger.info(
        "cron skills: job_id=%s count=%s chars=%s",
        job.id,
        total,
        len(block),
    )
    return block


def _unavailable(
    job: CronJobSpec,
    ref: CronJobSkillRef,
    reason: str,
) -> str:
    """Tell the model the instructions are missing, and not to invent them."""
    logger.warning(
        "cron skill unresolved: job_id=%s skill=%s template=%s reason=%s",
        job.id,
        ref.name,
        ref.template or "-",
        reason,
    )
    return (
        f"The [{ref.name}] skill was attached to this task but its "
        f"instructions could not be loaded ({reason}). Do not guess what "
        "the skill says and do not invent its rules; if the task cannot be "
        "completed without them, say so plainly in your reply."
    )


__all__ = ["build_skill_prompt_block"]
