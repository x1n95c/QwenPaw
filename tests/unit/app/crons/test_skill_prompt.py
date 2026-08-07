# -*- coding: utf-8 -*-
"""Rendering a job's attached skills into one prompt block.

The wording is load-bearing in two directions: it has to match what the
``/<skill>`` slash command injects (every shipped skill is written against
that sentence), and an unreadable skill has to *say so* rather than vanish —
a model told nothing invents the missing rules.
"""

from __future__ import annotations

from pathlib import Path

from qwenpaw.app.crons.models import MAX_SKILL_BODY_CHARS
from qwenpaw.app.crons.skill_prompt import (
    CRON_SKILL_TASK,
    build_skill_prompt_block,
)
from tests.unit.app.conftest import make_cron_job_spec

BODY = "# How to advise\n\nLead with the biggest directory."

SKILL_MD = f"""---
name: Disk Usage Advisor
description: Reads a scan and advises.
---

{BODY}
"""


def write_workspace_skill(
    workspace: Path,
    name: str,
    text: str = SKILL_MD,
) -> Path:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


class TestRendering:
    def test_injects_the_body_under_the_slash_command_preamble(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = write_workspace_skill(tmp_path, "advisor")
        job = make_cron_job_spec(skills=[{"name": "advisor"}])

        block = build_skill_prompt_block(job, tmp_path)

        # No trailing newline: `frontmatter.loads` strips it off `.content`.
        # The preamble is byte-identical to `/<skill> <input>`; the fixed
        # sentence is what fills the `user's task:` slot.
        assert block == (
            f"Use the [Disk Usage Advisor] skill in `{skill_dir}` "
            f"to fulfill user's task: {CRON_SKILL_TASK}\n\n{BODY}"
        )

    def test_tells_the_model_not_to_re_read_the_skill_file(
        self,
        tmp_path: Path,
    ) -> None:
        # The body is already inlined, so a helpful model opening the file
        # with a tool would spend a turn re-reading what it can see.
        write_workspace_skill(tmp_path, "advisor")
        job = make_cron_job_spec(skills=[{"name": "advisor"}])

        block = build_skill_prompt_block(job, tmp_path)

        assert "do not call any tool to read the skill file" in block

    def test_the_body_carries_no_frontmatter(self, tmp_path: Path) -> None:
        # The trap this guards: `read_skill_from_dir().content` keeps the
        # frontmatter, so reaching for the obvious helper would prepend a
        # block of YAML to the prompt on every single fire.
        write_workspace_skill(tmp_path, "advisor")
        job = make_cron_job_spec(skills=[{"name": "advisor"}])

        block = build_skill_prompt_block(job, tmp_path)

        assert "---" not in block
        assert "description:" not in block
        assert "Reads a scan and advises." not in block

    def test_several_skills_keep_declared_order_and_are_numbered(
        self,
        tmp_path: Path,
    ) -> None:
        for name in ("first", "second", "third"):
            write_workspace_skill(
                tmp_path,
                name,
                f"---\nname: {name}\n---\n\nbody of {name}\n",
            )
        job = make_cron_job_spec(
            skills=[{"name": "third"}, {"name": "first"}],
        )

        block = build_skill_prompt_block(job, tmp_path)

        assert block.index("[1/2] Use the [third]") < block.index(
            "[2/2] Use the [first]",
        )

    def test_a_single_skill_is_not_numbered(self, tmp_path: Path) -> None:
        write_workspace_skill(tmp_path, "advisor")
        job = make_cron_job_spec(skills=[{"name": "advisor"}])

        assert "[1/1]" not in build_skill_prompt_block(job, tmp_path)


class TestNothingToRender:
    def test_a_job_with_no_skills_is_empty(self, tmp_path: Path) -> None:
        assert build_skill_prompt_block(make_cron_job_spec(), tmp_path) == ""

    def test_a_text_job_is_empty_even_with_skills(
        self,
        tmp_path: Path,
    ) -> None:
        # No model runs on that path, so there is no prompt to prepend to.
        write_workspace_skill(tmp_path, "advisor")
        job = make_cron_job_spec(
            task_type="text",
            text="Daily check",
            skills=[{"name": "advisor"}],
        )

        assert build_skill_prompt_block(job, tmp_path) == ""

    def test_no_workspace_dir_is_empty(self) -> None:
        # The executor passes `getattr(workspace, "workspace_dir", None)`,
        # which is legitimately absent in some harnesses.
        job = make_cron_job_spec(skills=[{"name": "advisor"}])

        assert build_skill_prompt_block(job, None) == ""


class TestUnavailable:
    def test_an_unresolvable_skill_says_so_and_forbids_invention(
        self,
        tmp_path: Path,
    ) -> None:
        job = make_cron_job_spec(skills=[{"name": "deleted-skill"}])

        block = build_skill_prompt_block(job, tmp_path)

        assert "[deleted-skill]" in block
        assert "could not be loaded (not found)" in block
        assert "do not invent its rules" in block

    def test_an_unreadable_skill_md_says_so(self, tmp_path: Path) -> None:
        write_workspace_skill(
            tmp_path,
            "broken",
            "---\nname: [unclosed\n  bad: : yaml\n---\nbody\n",
        )
        job = make_cron_job_spec(skills=[{"name": "broken"}])

        block = build_skill_prompt_block(job, tmp_path)

        assert "SKILL.md missing or unreadable" in block

    def test_an_oversized_body_is_reported_not_truncated(
        self,
        tmp_path: Path,
    ) -> None:
        # Truncating instructions mid-sentence produces confidently wrong
        # behaviour, which is exactly what the "do not invent" wording
        # exists to prevent.
        huge = "x" * (MAX_SKILL_BODY_CHARS + 1)
        write_workspace_skill(tmp_path, "huge", f"---\nname: h\n---\n\n{huge}")
        job = make_cron_job_spec(skills=[{"name": "huge"}])

        block = build_skill_prompt_block(job, tmp_path)

        assert "instructions too large to attach" in block
        assert "xxxx" not in block

    def test_one_broken_skill_does_not_lose_the_others(
        self,
        tmp_path: Path,
    ) -> None:
        write_workspace_skill(tmp_path, "good-one")
        write_workspace_skill(tmp_path, "good-two")
        job = make_cron_job_spec(
            skills=[
                {"name": "good-one"},
                {"name": "gone"},
                {"name": "good-two"},
            ],
        )

        block = build_skill_prompt_block(job, tmp_path)

        assert "[1/3] Use the [Disk Usage Advisor]" in block
        assert "[2/3] The [gone] skill was attached" in block
        assert block.count("Lead with the biggest directory.") == 2
