# -*- coding: utf-8 -*-
"""Listing the skills a cron job may attach.

One list from two filesystem layouts. What matters is that the two are
normalised into the same shape (the picker renders them through one code
path), that ``name`` + ``template`` reconstruct the ref the job stores, and
that a single broken skill never blanks the list.
"""

from __future__ import annotations

import json
from pathlib import Path

from qwenpaw.app.crons.skills_api import collect_cron_skills

SKILL_MD = """---
name: Disk Usage Advisor
description: Reads a scan and advises.
---

body
"""

TEMPLATE_DOC = """---
title: Space check
metadata:
  title_key: cronTemplates.spaceCheck
---

# Space check
"""


def write_skill(root: Path, name: str, text: str = SKILL_MD) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def write_package(workspace: Path, name: str) -> Path:
    package = workspace / "cron_templates" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "TEMPLATE.md").write_text(TEMPLATE_DOC, encoding="utf-8")
    (package / "template.json").write_text("{}", encoding="utf-8")
    return package


def by_name(skills: list, name: str):
    return next(s for s in skills if s.name == name)


class TestWorkspaceSkills:
    def test_lists_an_installed_skill_with_its_frontmatter(
        self,
        tmp_path: Path,
    ) -> None:
        write_skill(tmp_path / "skills", "advisor")

        found = by_name(collect_cron_skills(tmp_path, False), "advisor")

        assert found.source == "workspace"
        # Empty, and that is the discriminator: `name` + `template` are
        # exactly what `CronJobSkillRef` needs, and an installed skill has
        # no package.
        assert found.template == ""
        assert found.display_name == "Disk Usage Advisor"
        assert found.description == "Reads a scan and advises."

    def test_display_name_falls_back_to_the_directory(
        self,
        tmp_path: Path,
    ) -> None:
        write_skill(tmp_path / "skills", "bare", "no frontmatter")

        assert by_name(collect_cron_skills(tmp_path, False), "bare")

    def test_does_not_require_a_manifest(self, tmp_path: Path) -> None:
        # Nothing here is installed in the manifest sense; the picker lists
        # what is on disk because the trigger path reads what is on disk.
        write_skill(tmp_path / "skills", "advisor")

        assert not (tmp_path / "skill.json").exists()
        assert len(collect_cron_skills(tmp_path, False)) == 1

    def test_skips_directories_that_are_not_skills(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "skills" / "hollow").mkdir(parents=True)
        (tmp_path / "skills" / "__pycache__").mkdir()
        (tmp_path / "skills" / "loose.txt").write_text("x")
        write_skill(tmp_path / "skills", "real")

        assert [s.name for s in collect_cron_skills(tmp_path, False)] == [
            "real",
        ]

    def test_no_skills_directory_is_empty_not_an_error(
        self,
        tmp_path: Path,
    ) -> None:
        assert collect_cron_skills(tmp_path, False) == []


class TestTemplateSkills:
    def test_lists_a_bundled_skill_with_its_package(
        self,
        tmp_path: Path,
    ) -> None:
        package = write_package(tmp_path, "space-check")
        write_skill(package / "skills", "advisor")

        found = by_name(collect_cron_skills(tmp_path, False), "advisor")

        assert found.source == "template"
        assert found.template == "space-check"
        assert found.template_source == "user"
        # Key and literal both travel: resolving the title here would emit
        # the untranslated literal for every builtin that ships a key.
        assert found.template_title == "Space check"
        assert found.template_title_key == "cronTemplates.spaceCheck"

    def test_workspace_skills_come_first(self, tmp_path: Path) -> None:
        # The console regroups these into three tiers (the job's origin
        # package, then installed, then everything else), but it preserves
        # the order *within* each tier — so this response's order still
        # decides what the user reads first.
        package = write_package(tmp_path, "space-check")
        write_skill(package / "skills", "bundled")
        write_skill(tmp_path / "skills", "installed")

        sources = [s.source for s in collect_cron_skills(tmp_path, False)]

        assert sources == ["workspace", "template"]

    def test_include_builtin_controls_the_builtin_packages(
        self,
        tmp_path: Path,
    ) -> None:
        without = collect_cron_skills(tmp_path, False)
        with_builtin = collect_cron_skills(tmp_path, True)

        assert without == []
        # The two builtin packages that ship skills.
        assert {s.name for s in with_builtin} == {
            "weather-report",
            "disk-usage-advisor",
        }
        assert all(s.source == "template" for s in with_builtin)

    def test_a_user_package_shadows_a_builtin(self, tmp_path: Path) -> None:
        package = write_package(tmp_path, "workspace-usage")
        write_skill(package / "skills", "mine")

        found = collect_cron_skills(tmp_path, True)
        usage = [s for s in found if s.template == "workspace-usage"]

        assert [s.name for s in usage] == ["mine"]
        assert usage[0].template_source == "user"
        # The builtin's own skill is gone with the package it belonged to.
        assert "disk-usage-advisor" not in {s.name for s in found}


class TestBatchFiles:
    def test_reports_the_batch_json_a_skill_carries(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = write_skill(tmp_path / "skills", "collector")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "phase1.json").write_text(
            json.dumps([{"tool_name": "a"}]),
        )
        (skill_dir / "batch").mkdir()
        (skill_dir / "batch" / "collect.json").write_text("[]")

        found = by_name(collect_cron_skills(tmp_path, False), "collector")

        assert found.batch_files == [
            "scripts/phase1.json",
            "batch/collect.json",
        ]

    def test_excludes_references_and_non_json(self, tmp_path: Path) -> None:
        skill_dir = write_skill(tmp_path / "skills", "collector")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "doc.json").write_text("[]")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "helper.py").write_text("pass")

        found = by_name(collect_cron_skills(tmp_path, False), "collector")

        assert found.batch_files == []

    def test_a_skill_carrying_nothing_reports_nothing(
        self,
        tmp_path: Path,
    ) -> None:
        write_skill(tmp_path / "skills", "plain")

        found = by_name(collect_cron_skills(tmp_path, False), "plain")

        assert found.batch_files == []


class TestDegradation:
    def test_one_unparseable_skill_does_not_blank_the_list(
        self,
        tmp_path: Path,
    ) -> None:
        # A picker that renders nothing reads like the feature is broken,
        # which is worse than one missing row.
        write_skill(
            tmp_path / "skills",
            "broken",
            "---\nname: [unclosed\n  bad: : yaml\n---\nbody\n",
        )
        write_skill(tmp_path / "skills", "fine")

        found = collect_cron_skills(tmp_path, False)

        assert "fine" in {s.name for s in found}
        # `read_frontmatter_safe_from_path` degrades rather than failing, so
        # the broken one is still listed — just without its metadata.
        broken = by_name(found, "broken")
        assert broken.description == ""

    def test_the_response_never_carries_the_skill_body(
        self,
        tmp_path: Path,
    ) -> None:
        # The body is a prompt block, not picker data. Shipping it would put
        # every attached skill's full text in every list response.
        write_skill(tmp_path / "skills", "advisor")

        found = by_name(collect_cron_skills(tmp_path, False), "advisor")

        assert "content" not in found.model_dump()
        assert "body" not in json.dumps(found.model_dump())
