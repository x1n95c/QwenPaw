# -*- coding: utf-8 -*-
"""Builtin templates that reference their own bundled skills.

Two builtins ship a `skills/` directory and write prompts against it —
`weather-report` says "按 weather-report skill 的格式播报" and
`workspace-usage` says "按 disk-usage-advisor skill 的说明操作". They used to
work because applying the template *installed* those skills. Installation is
gone, so the ref declared in `template.json` is now the only thing making
them work, and a typo in it fails silently: the job runs, tells the model the
instructions were unavailable, and produces a plausible wrong report.

Cheap to guard, and it guards the exact regression this change risks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.app.crons.models import CronJobSkillRef
from qwenpaw.app.crons.skill_refs import resolve_skill_dir
from qwenpaw.app.cron_templates.store import (
    get_builtin_cron_template_dir,
    iter_template_dirs,
    list_bundled_skills,
)

#: Every builtin package, so a newly added one is covered without an edit.
PACKAGES = sorted(
    path.name for path in iter_template_dirs(get_builtin_cron_template_dir())
)

#: The two that ship skills today. Named explicitly as well, so deleting a
#: package's `skills/` directory shows up as a failure here rather than as
#: this file quietly testing nothing.
WITH_SKILLS = {"weather-report", "workspace-usage"}


def package_dir(name: str) -> Path:
    return get_builtin_cron_template_dir() / name


def payload(name: str) -> dict:
    return json.loads(
        (package_dir(name) / "template.json").read_text(encoding="utf-8"),
    )


def test_the_packages_that_bundle_skills_are_the_expected_ones() -> None:
    bundling = {
        name for name in PACKAGES if list_bundled_skills(package_dir(name))
    }

    assert bundling == WITH_SKILLS


@pytest.mark.parametrize("name", sorted(WITH_SKILLS))
def test_declared_refs_name_a_skill_the_package_actually_bundles(
    name: str,
) -> None:
    bundled = set(list_bundled_skills(package_dir(name)))
    data = payload(name)

    for half in ("form", "job"):
        refs = data[half].get("skills")
        assert refs, f"{name}.{half} declares no skills"
        for ref in refs:
            assert ref["name"] in bundled, f"{name}.{half} -> {ref}"
            # Self-referential on purpose: the package points at its own
            # `skills/` directory, which is what makes it work without
            # anything having been installed.
            assert ref["template"] == name


@pytest.mark.parametrize("name", sorted(WITH_SKILLS))
def test_both_halves_declare_the_same_refs(name: str) -> None:
    # `form` drives the drawer through `toFormValues`; `job` drives headless
    # creation. A drift means the console and the API create different jobs
    # from one template.
    data = payload(name)

    assert data["form"]["skills"] == data["job"]["skills"]


@pytest.mark.parametrize("name", sorted(WITH_SKILLS))
def test_declared_refs_resolve_at_run_time(name: str, tmp_path: Path) -> None:
    """The end-to-end check: a fresh workspace, nothing installed."""
    for ref in payload(name)["job"]["skills"]:
        found = resolve_skill_dir(CronJobSkillRef(**ref), tmp_path)

        assert found is not None, ref
        assert (found / "SKILL.md").is_file()


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_every_builtin_declares_skills_the_spec_model_accepts(
    name: str,
) -> None:
    """Guards the new field against the all-or-nothing spec validator.

    `jobs.json` is validated as one document, so a ref the model rejects
    would not just fail this job — it would make every job in the file
    disappear. Only the `skills` field is validated here: both halves of a
    packaged template are deliberately *skeletons* (no `request`, no
    dispatch target), filled in at creation time.
    """
    data = payload(name)

    for half in ("form", "job"):
        refs = data[half].get("skills", [])
        assert isinstance(refs, list)
        for ref in refs:
            parsed = CronJobSkillRef.model_validate(ref)
            assert parsed.source == "template"
