# -*- coding: utf-8 -*-
"""Resolving a job's skill refs to directories on disk.

Two properties are asserted throughout: the resolvers are **fail-closed**
(anything they are not certain about becomes ``None``) and they **never
raise** — a cron fire is unattended, so an unresolvable ref has to become a
reported failure rather than an exception or a read of some other directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.app.crons.models import CronJobSkillRef
from qwenpaw.app.crons.skill_refs import (
    list_skill_batch_files,
    resolve_skill_batch_script,
    resolve_skill_dir,
)

TEMPLATE_DOC = """---
title: Space check
---

# Space check
"""


def write_skill(root: Path, name: str, *, skill_md: bool = True) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    if skill_md:
        (skill_dir / "SKILL.md").write_text(
            "---\nname: A Skill\ndescription: d\n---\n\nbody\n",
            encoding="utf-8",
        )
    return skill_dir


def write_workspace_skill(workspace: Path, name: str) -> Path:
    return write_skill(workspace / "skills", name)


def write_template_skill(
    workspace: Path,
    template: str,
    name: str,
) -> Path:
    package = workspace / "cron_templates" / template
    package.mkdir(parents=True, exist_ok=True)
    # `TEMPLATE.md` is what marks a directory as a package; without it
    # `resolve_template_dir` reports nothing there.
    (package / "TEMPLATE.md").write_text(TEMPLATE_DOC, encoding="utf-8")
    (package / "template.json").write_text("{}", encoding="utf-8")
    return write_skill(package / "skills", name)


class TestResolveSkillDir:
    def test_finds_an_installed_workspace_skill(self, tmp_path: Path) -> None:
        expected = write_workspace_skill(tmp_path, "disk-advisor")
        ref = CronJobSkillRef(name="disk-advisor")

        assert resolve_skill_dir(ref, tmp_path) == expected

    def test_finds_a_skill_bundled_in_a_template(self, tmp_path: Path) -> None:
        expected = write_template_skill(tmp_path, "space-check", "advisor")
        ref = CronJobSkillRef(name="advisor", template="space-check")

        assert resolve_skill_dir(ref, tmp_path) == expected

    def test_does_not_require_the_skill_to_be_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        # The whole point of referencing instead of installing: there is no
        # `skill.json` here at all, so a manifest lookup would fail.
        write_workspace_skill(tmp_path, "never-installed")

        assert not (tmp_path / "skill.json").exists()
        assert (
            resolve_skill_dir(
                CronJobSkillRef(name="never-installed"), tmp_path
            )
            is not None
        )

    def test_a_user_package_shadows_a_builtin_of_the_same_name(
        self,
        tmp_path: Path,
    ) -> None:
        # One definition of that precedence, borrowed from
        # `resolve_template_dir` rather than re-implemented here.
        expected = write_template_skill(
            tmp_path,
            "workspace-usage",
            "disk-usage-advisor",
        )
        ref = CronJobSkillRef(
            name="disk-usage-advisor",
            template="workspace-usage",
        )

        assert resolve_skill_dir(ref, tmp_path) == expected

    def test_resolves_a_builtin_package_when_nothing_shadows_it(
        self,
        tmp_path: Path,
    ) -> None:
        ref = CronJobSkillRef(
            name="disk-usage-advisor",
            template="workspace-usage",
        )
        found = resolve_skill_dir(ref, tmp_path)

        assert found is not None
        assert found.name == "disk-usage-advisor"

    def test_unknown_workspace_skill_is_none(self, tmp_path: Path) -> None:
        assert (
            resolve_skill_dir(CronJobSkillRef(name="nope"), tmp_path) is None
        )

    def test_unknown_template_is_none(self, tmp_path: Path) -> None:
        ref = CronJobSkillRef(name="advisor", template="no-such-package")

        assert resolve_skill_dir(ref, tmp_path) is None

    def test_a_directory_without_skill_md_is_none(
        self,
        tmp_path: Path,
    ) -> None:
        # A bare directory is not a skill anywhere else in the system
        # either; accepting it would inject an empty body.
        write_skill(tmp_path / "skills", "hollow", skill_md=False)

        assert (
            resolve_skill_dir(CronJobSkillRef(name="hollow"), tmp_path) is None
        )

    @pytest.mark.parametrize("name", ["__pycache__", ".DS_Store", "~draft"])
    def test_an_ignored_entry_name_is_none(
        self,
        tmp_path: Path,
        name: str,
    ) -> None:
        write_workspace_skill(tmp_path, name)

        assert resolve_skill_dir(CronJobSkillRef(name=name), tmp_path) is None

    @pytest.mark.parametrize(
        "name",
        ["../escape", "a/b", "a\\b", "..", "."],
    )
    def test_a_traversing_name_is_none_not_an_exception(
        self,
        tmp_path: Path,
        name: str,
    ) -> None:
        # These are rejected by the model validator too, but a hand-edited
        # `jobs.json` reaches the resolver directly, so it fails closed on
        # its own rather than trusting that.
        ref = CronJobSkillRef.model_construct(name=name, template=None)

        assert resolve_skill_dir(ref, tmp_path) is None

    def test_a_nul_bearing_name_is_none_not_an_exception(
        self,
        tmp_path: Path,
    ) -> None:
        ref = CronJobSkillRef.model_construct(name="a\x00b", template=None)

        assert resolve_skill_dir(ref, tmp_path) is None

    def test_a_traversing_template_is_none_not_an_exception(
        self,
        tmp_path: Path,
    ) -> None:
        ref = CronJobSkillRef.model_construct(name="advisor", template="../x")

        assert resolve_skill_dir(ref, tmp_path) is None


class TestResolveSkillBatchScript:
    @pytest.fixture
    def skill(self, tmp_path: Path) -> Path:
        skill_dir = write_workspace_skill(tmp_path, "collector")
        for subdir in ("scripts", "batch", "references"):
            (skill_dir / subdir).mkdir()
            (skill_dir / subdir / "x.json").write_text(
                json.dumps([{"tool_name": "a"}]),
                encoding="utf-8",
            )
        (skill_dir / "scripts" / "x.txt").write_text("nope", encoding="utf-8")
        (skill_dir / "root.json").write_text("[]", encoding="utf-8")
        return skill_dir

    @pytest.mark.parametrize("relative", ["scripts/x.json", "batch/x.json"])
    def test_accepts_the_two_batch_directories(
        self,
        tmp_path: Path,
        skill: Path,
        relative: str,
    ) -> None:
        found = resolve_skill_batch_script(
            CronJobSkillRef(name="collector"),
            relative,
            tmp_path,
        )

        assert found == skill / relative

    @pytest.mark.parametrize(
        "relative",
        [
            # Documents, not executables.
            "references/x.json",
            # Config next to SKILL.md must not be copyable as a script.
            "root.json",
            "x.json",
            # Only `.json` is a batch file, matching `_load_batch_file`.
            "scripts/x.txt",
            # Traversal, in every shape the guards run before touching disk.
            "scripts/../SKILL.md",
            "../SKILL.md",
            "scripts\\x.json",
            "/etc/passwd",
            "",
        ],
    )
    def test_rejects_everything_else(
        self,
        tmp_path: Path,
        skill: Path,
        relative: str,
    ) -> None:
        assert (
            resolve_skill_batch_script(
                CronJobSkillRef(name="collector"),
                relative,
                tmp_path,
            )
            is None
        )

    def test_an_unresolvable_skill_is_none(self, tmp_path: Path) -> None:
        assert (
            resolve_skill_batch_script(
                CronJobSkillRef(name="absent"),
                "scripts/x.json",
                tmp_path,
            )
            is None
        )

    def test_a_missing_file_in_a_real_skill_is_none(
        self,
        tmp_path: Path,
        skill: Path,
    ) -> None:
        assert (
            resolve_skill_batch_script(
                CronJobSkillRef(name="collector"),
                "scripts/absent.json",
                tmp_path,
            )
            is None
        )


class TestListSkillBatchFiles:
    def test_lists_both_directories_recursively(self, tmp_path: Path) -> None:
        skill_dir = write_workspace_skill(tmp_path, "collector")
        (skill_dir / "scripts" / "nested").mkdir(parents=True)
        (skill_dir / "scripts" / "phase1.json").write_text("[]")
        (skill_dir / "scripts" / "nested" / "phase2.json").write_text("[]")
        (skill_dir / "batch").mkdir()
        (skill_dir / "batch" / "collect.json").write_text("[]")

        assert list_skill_batch_files(skill_dir) == [
            "scripts/nested/phase2.json",
            "scripts/phase1.json",
            "batch/collect.json",
        ]

    def test_excludes_non_json_and_other_directories(
        self,
        tmp_path: Path,
    ) -> None:
        skill_dir = write_workspace_skill(tmp_path, "collector")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "helper.py").write_text("pass")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "doc.json").write_text("[]")

        assert list_skill_batch_files(skill_dir) == []

    def test_skips_ignored_entries(self, tmp_path: Path) -> None:
        skill_dir = write_workspace_skill(tmp_path, "collector")
        (skill_dir / "scripts" / "__pycache__").mkdir(parents=True)
        (skill_dir / "scripts" / "__pycache__" / "x.json").write_text("[]")
        (skill_dir / "scripts" / "real.json").write_text("[]")

        assert list_skill_batch_files(skill_dir) == ["scripts/real.json"]

    def test_a_skill_carrying_nothing_is_empty(self, tmp_path: Path) -> None:
        assert (
            list_skill_batch_files(write_workspace_skill(tmp_path, "s")) == []
        )
