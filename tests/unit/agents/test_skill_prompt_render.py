# -*- coding: utf-8 -*-
"""Turning a skill directory into prompt text.

Guards the two things the cron path and the ``/<skill>`` slash command must
agree on: the invocation wording, and that the body is frontmatter-stripped.
"""

from pathlib import Path

from qwenpaw.agents.skill_system.prompt import (
    load_skill_body,
    render_skill_invocation,
)

SKILL_MD = """---
name: Weather Reporter
description: Reads a weather payload and reports it.
---

# How to report

Lead with the temperature, then the advice.
"""


def _write_skill(root: Path, name: str, text: str = SKILL_MD) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


class TestLoadSkillBody:
    def test_strips_frontmatter_from_the_body(self, tmp_path: Path) -> None:
        # The whole reason this helper exists rather than reusing
        # `read_skill_from_dir`, whose `content` keeps the frontmatter: this
        # text goes into a prompt on every single cron fire.
        loaded = load_skill_body(_write_skill(tmp_path, "weather"))

        assert loaded is not None
        _, body = loaded
        assert body.startswith("# How to report")
        assert "---" not in body
        assert "description:" not in body

    def test_display_name_comes_from_frontmatter(self, tmp_path: Path) -> None:
        loaded = load_skill_body(_write_skill(tmp_path, "weather"))

        assert loaded is not None
        assert loaded[0] == "Weather Reporter"

    def test_display_name_falls_back_to_the_directory(
        self,
        tmp_path: Path,
    ) -> None:
        # Identity is the directory name everywhere in the skill system;
        # frontmatter `name` is only a label, and may be absent.
        loaded = load_skill_body(
            _write_skill(tmp_path, "bare-skill", "no frontmatter here"),
        )

        assert loaded is not None
        assert loaded == ("bare-skill", "no frontmatter here")

    def test_missing_skill_md_is_none_not_an_exception(
        self,
        tmp_path: Path,
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()

        assert load_skill_body(empty) is None

    def test_malformed_frontmatter_is_none_not_an_exception(
        self,
        tmp_path: Path,
    ) -> None:
        # A cron job referencing a broken skill has to degrade into a
        # message the model can read, never into a traceback that kills
        # the fire.
        broken = _write_skill(
            tmp_path,
            "broken",
            "---\nname: [unclosed\n  bad: : yaml\n---\nbody\n",
        )

        assert load_skill_body(broken) is None


class TestRenderSkillInvocation:
    def test_matches_the_slash_command_wording(self) -> None:
        # Byte-for-byte what `/<skill> <input>` has always injected. Every
        # shipped skill is written against this sentence, so it is
        # reproduced rather than improved.
        rendered = render_skill_invocation(
            "Weather Reporter",
            Path("/ws/skills/weather"),
            "BODY",
            "report Beijing",
        )

        assert rendered == (
            "Use the [Weather Reporter] skill in "
            "`/ws/skills/weather` to fulfill "
            "user's task: report Beijing\n\n"
            "BODY"
        )

    def test_there_is_only_one_format(self) -> None:
        # A caller with no literal user text passes a fixed sentence rather
        # than getting a second phrasing — two phrasings would be two
        # things to keep in sync. The cron path's is `CRON_SKILL_TASK`.
        from qwenpaw.app.crons.skill_prompt import CRON_SKILL_TASK

        rendered = render_skill_invocation(
            "Weather Reporter",
            Path("/ws/skills/weather"),
            "BODY",
            CRON_SKILL_TASK,
        )

        assert rendered.startswith(
            "Use the [Weather Reporter] skill in "
            "`/ws/skills/weather` to fulfill user's task: ",
        )
        assert "do not call any tool to read the skill file" in rendered
        assert rendered.endswith("\n\nBODY")

    def test_the_body_is_the_tail_so_it_is_never_truncated(self) -> None:
        rendered = render_skill_invocation(
            "n", Path("/d"), "LINE1\nLINE2", "t"
        )

        assert rendered.endswith("LINE1\nLINE2")
