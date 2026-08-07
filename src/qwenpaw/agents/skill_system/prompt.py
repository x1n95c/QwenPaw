# -*- coding: utf-8 -*-
"""Turning a skill directory into model-facing prompt text.

Extracted from the ``/<skill_name>`` slash-command handler so the cron path
can produce the *same* text without going through slash dispatch. It cannot
reuse the handler itself: that one resolves only workspace-effective skills
and rewrites a single message, whereas a cron job references skills that
were deliberately never installed, and may reference several at once.

Lives here rather than next to either caller because SKILL.md semantics —
what counts as the body, which name is the display name — belong to the
skill system. ``app/crons`` importing ``runtime/`` would be backwards.
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from ..utils.file_handling import read_text_file_with_encoding_fallback

logger = logging.getLogger(__name__)


def load_skill_body(skill_dir: Path) -> tuple[str, str] | None:
    """Read ``SKILL.md`` as ``(display_name, body)``, or ``None``.

    ``body`` is the frontmatter-**stripped** content. That distinction
    matters: ``read_skill_from_dir`` stores the raw file in
    ``SkillInfo.content``, frontmatter included, because it describes the
    skill for a listing. Putting that in a prompt would prepend a block of
    YAML to the instructions every single time.

    The display name comes from frontmatter, the identity from the
    directory — the same split the rest of the skill system applies
    (``store.py`` keys everything by directory name and treats frontmatter
    ``name`` as a label).

    Never raises: every caller is on a path where a missing or corrupt
    skill has to degrade into a message, not an exception.
    """
    skill_md = skill_dir / "SKILL.md"
    try:
        raw = read_text_file_with_encoding_fallback(skill_md)
        post = frontmatter.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("Cannot read skill body from %s: %s", skill_md, exc)
        return None
    except Exception as exc:  # pylint: disable=broad-except
        # frontmatter/yaml raise their own hierarchies for malformed
        # documents; a bad skill must not take the caller down with it.
        logger.warning("Cannot parse skill body from %s: %s", skill_md, exc)
        return None
    display_name = str(post.get("name") or skill_dir.name)
    return display_name, post.content


def render_skill_invocation(
    display_name: str,
    skill_dir: Path,
    body: str,
    user_input: str,
) -> str:
    """Render the "use this skill" preamble followed by the skill body.

    One format, byte-identical to what ``/<skill> <input>`` has always
    injected. The wording is load-bearing for every existing skill, so it
    is reproduced rather than improved, and callers that have no literal
    user text to put in the slot pass a fixed sentence instead of getting a
    second phrasing (see ``crons.skill_prompt.CRON_SKILL_TASK``) — two
    phrasings would be two things to keep in sync for no gain.
    """
    return (
        f"Use the [{display_name}] skill in "
        f"`{skill_dir}` to fulfill "
        f"user's task: {user_input}\n\n"
        f"{body}"
    )


__all__ = ["load_skill_body", "render_skill_invocation"]
