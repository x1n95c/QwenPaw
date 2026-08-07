# -*- coding: utf-8 -*-
"""Materialising the shipped builtin templates into the user-level store.

Builtins used to be read straight out of the wheel, so a template's
``{{template_dir}}`` resolved into ``site-packages`` — and that path is baked
into a cron job's prompt when the template is applied, breaking on every
reinstall. Copying them to ``~/.qwenpaw/cron_templates/`` fixes that.

The store directory is *shared* with packages that predate per-workspace
templates, which is why the "leave anything we did not put there alone" rule
below is the most important test in this file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwenpaw.app.cron_templates.store import (
    ensure_builtin_templates_materialized,
    get_builtin_cron_template_dir,
    get_builtin_template_record_path,
    get_builtin_template_store_dir,
    iter_template_dirs,
    materialized_builtin_names,
    read_builtin_template_record,
)

FOREIGN_DOC = """---
name: 111
description: a package the user made before any of this existed
metadata:
  qwenpaw:
    category: cron
---

# 111
"""


def shipped_names() -> set[str]:
    return {
        p.name for p in iter_template_dirs(get_builtin_cron_template_dir())
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_record(templates: dict) -> None:
    path = get_builtin_template_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "cron-builtin-templates.v1",
                "templates": templates,
            },
        ),
        encoding="utf-8",
    )


class TestFirstRun:
    def test_copies_every_shipped_builtin(self, working_dir: Path) -> None:
        result = ensure_builtin_templates_materialized()

        assert set(result["copied"]) == shipped_names()
        assert len(result["copied"]) >= 12
        store = get_builtin_template_store_dir()
        for name in shipped_names():
            assert (store / name / "TEMPLATE.md").is_file()

    def test_copies_the_whole_package_not_just_the_docs(
        self,
        working_dir: Path,
    ) -> None:
        # A template's `batch/` scripts and bundled `skills/` are the parts a
        # job actually reaches for; a shallow copy would leave the prompt
        # pointing at files that are not there.
        ensure_builtin_templates_materialized()
        store = get_builtin_template_store_dir()

        assert (store / "weather-report" / "batch" / "weather.json").is_file()
        assert (
            store
            / "workspace-usage"
            / "skills"
            / "disk-usage-advisor"
            / "SKILL.md"
        ).is_file()

    def test_records_the_version_it_copied(self, working_dir: Path) -> None:
        ensure_builtin_templates_materialized()

        recorded = read_builtin_template_record()["templates"]
        assert recorded["weather-report"] == {"version": "1.0"}
        assert set(recorded) == shipped_names()

    def test_the_record_is_the_authority_on_what_is_builtin(
        self,
        working_dir: Path,
    ) -> None:
        ensure_builtin_templates_materialized()

        assert materialized_builtin_names() == shipped_names()


class TestIdempotence:
    def test_a_second_call_changes_nothing(self, working_dir: Path) -> None:
        ensure_builtin_templates_materialized()
        before = digest(get_builtin_template_record_path())

        result = ensure_builtin_templates_materialized()

        assert result["copied"] == []
        assert set(result["unchanged"]) == shipped_names()
        # Not rewritten either: this runs on every workspace start.
        assert digest(get_builtin_template_record_path()) == before

    def test_a_stale_version_is_refreshed(self, working_dir: Path) -> None:
        # Safe to overwrite precisely because builtins are read-only through
        # the API — a user who wants changes forks into their workspace, so
        # the store copy never holds edits worth keeping.
        ensure_builtin_templates_materialized()
        store = get_builtin_template_store_dir()
        (store / "weather-report" / "TEMPLATE.md").write_text(
            "clobbered", encoding="utf-8"
        )
        record = read_builtin_template_record()["templates"]
        record["weather-report"] = {"version": "0.9"}
        write_record(record)

        result = ensure_builtin_templates_materialized()

        assert result["updated"] == ["weather-report"]
        assert "clobbered" not in (
            store / "weather-report" / "TEMPLATE.md"
        ).read_text(encoding="utf-8")
        assert read_builtin_template_record()["templates"][
            "weather-report"
        ] == {"version": "1.0"}

    def test_a_missing_copy_is_restored(self, working_dir: Path) -> None:
        import shutil

        ensure_builtin_templates_materialized()
        store = get_builtin_template_store_dir()
        shutil.rmtree(store / "diet-plan")

        result = ensure_builtin_templates_materialized()

        assert result["copied"] == ["diet-plan"]
        assert (store / "diet-plan" / "TEMPLATE.md").is_file()


class TestLeavesForeignPackagesAlone:
    """The rule that makes sharing the directory with the user's own safe."""

    def test_an_unrecorded_directory_is_never_overwritten(
        self,
        working_dir: Path,
    ) -> None:
        # Name-collide with a real builtin on purpose: this is the case that
        # would destroy someone's work if the rule were "just copy".
        store = get_builtin_template_store_dir()
        (store / "diet-plan").mkdir(parents=True)
        (store / "diet-plan" / "TEMPLATE.md").write_text(
            FOREIGN_DOC, encoding="utf-8"
        )
        before = digest(store / "diet-plan" / "TEMPLATE.md")

        result = ensure_builtin_templates_materialized()

        assert result["blocked"] == ["diet-plan"]
        assert digest(store / "diet-plan" / "TEMPLATE.md") == before
        # And it does not get claimed as a builtin.
        assert "diet-plan" not in materialized_builtin_names()

    def test_blocking_one_does_not_stop_the_others(
        self,
        working_dir: Path,
    ) -> None:
        store = get_builtin_template_store_dir()
        (store / "diet-plan").mkdir(parents=True)
        (store / "diet-plan" / "TEMPLATE.md").write_text(
            FOREIGN_DOC, encoding="utf-8"
        )

        result = ensure_builtin_templates_materialized()

        assert "weather-report" in result["copied"]
        assert set(result["copied"]) == shipped_names() - {"diet-plan"}

    def test_unrelated_packages_are_untouched_and_stay_invisible(
        self,
        working_dir: Path,
    ) -> None:
        store = get_builtin_template_store_dir()
        for name in ("111", "222"):
            (store / name).mkdir(parents=True)
            (store / name / "TEMPLATE.md").write_text(
                FOREIGN_DOC, encoding="utf-8"
            )
        before = {n: digest(store / n / "TEMPLATE.md") for n in ("111", "222")}

        ensure_builtin_templates_materialized()

        for name in ("111", "222"):
            assert digest(store / name / "TEMPLATE.md") == before[name]
            assert name not in materialized_builtin_names()

    def test_the_legacy_manifest_is_left_byte_identical(
        self,
        working_dir: Path,
    ) -> None:
        # The pre-per-workspace manifest lives at `manifest.json` in this same
        # directory and lists the user's own packages under the *same*
        # schema_version the workspace manifest uses. Reading or rewriting it
        # here would resurrect them as globally visible user templates, so the
        # bookkeeping goes in `builtin.json` instead.
        store = get_builtin_template_store_dir()
        store.mkdir(parents=True, exist_ok=True)
        legacy = store / "manifest.json"
        legacy.write_text(
            json.dumps(
                {
                    "schema_version": "cron-template-manifest.v1",
                    "version": 1785989610785,
                    "templates": {"111": {"name": "111", "source": "user"}},
                },
            ),
            encoding="utf-8",
        )
        before = digest(legacy)

        ensure_builtin_templates_materialized()

        assert digest(legacy) == before
        assert get_builtin_template_record_path().name == "builtin.json"


class TestRetirement:
    def test_a_no_longer_shipped_builtin_is_removed(
        self,
        working_dir: Path,
    ) -> None:
        ensure_builtin_templates_materialized()
        store = get_builtin_template_store_dir()
        # Pretend an older version shipped `retired-thing`.
        (store / "retired-thing").mkdir()
        (store / "retired-thing" / "TEMPLATE.md").write_text("x")
        record = read_builtin_template_record()["templates"]
        record["retired-thing"] = {"version": "1.0"}
        write_record(record)

        result = ensure_builtin_templates_materialized()

        assert result["removed"] == ["retired-thing"]
        assert not (store / "retired-thing").exists()
        assert "retired-thing" not in materialized_builtin_names()


class TestNeverRaises:
    @pytest.mark.parametrize(
        "broken",
        ['{"templates": []}', '{"templates": "nope"}', "not json at all", ""],
    )
    def test_a_corrupt_record_degrades_instead_of_failing(
        self,
        working_dir: Path,
        broken: str,
    ) -> None:
        # This runs on the startup path; a workspace must still start.
        path = get_builtin_template_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(broken, encoding="utf-8")

        result = ensure_builtin_templates_materialized()

        assert isinstance(result, dict)
        assert materialized_builtin_names() >= set(result["copied"])


class TestResolution:
    """How store, wheel, and a workspace copy rank against each other."""

    def test_resolves_to_the_store_once_materialized(
        self,
        workspace: Path,
    ) -> None:
        # The point of the whole change: `{{template_dir}}` and a bundled
        # skill's path stop pointing into the install tree.
        from qwenpaw.app.cron_templates.store import resolve_template_dir

        ensure_builtin_templates_materialized()

        found = resolve_template_dir("weather-report", workspace)
        assert found is not None
        assert found[1] == "builtin"
        assert found[0] == get_builtin_template_store_dir() / "weather-report"
        assert get_builtin_cron_template_dir() not in found[0].parents

    def test_falls_back_to_the_wheel_before_materializing(
        self,
        workspace: Path,
    ) -> None:
        # Keeps a bare test workspace, a headless run, and a failed copy all
        # working — which is what makes this change additive.
        from qwenpaw.app.cron_templates.store import resolve_template_dir

        found = resolve_template_dir("weather-report", workspace)
        assert found is not None
        assert found[1] == "builtin"
        assert found[0].parent == get_builtin_cron_template_dir()

    def test_a_workspace_copy_still_shadows_the_store(
        self,
        workspace: Path,
    ) -> None:
        from qwenpaw.app.cron_templates.store import (
            get_cron_template_dir,
            resolve_template_dir,
        )

        ensure_builtin_templates_materialized()
        mine = get_cron_template_dir(workspace) / "weather-report"
        mine.mkdir(parents=True)
        (mine / "TEMPLATE.md").write_text(FOREIGN_DOC, encoding="utf-8")

        found = resolve_template_dir("weather-report", workspace)
        assert found == (mine, "user")

    def test_an_unrecorded_store_directory_is_not_resolvable(
        self,
        workspace: Path,
    ) -> None:
        # The user's pre-existing packages must stay as invisible as they are
        # today, even though they sit in the store directory.
        from qwenpaw.app.cron_templates.store import resolve_template_dir

        store = get_builtin_template_store_dir()
        (store / "111").mkdir(parents=True)
        (store / "111" / "TEMPLATE.md").write_text(
            FOREIGN_DOC, encoding="utf-8"
        )
        ensure_builtin_templates_materialized()

        assert resolve_template_dir("111", workspace) is None

    def test_the_builtin_source_ignores_a_workspace_shadow(
        self,
        workspace: Path,
    ) -> None:
        # Forking needs the *source* even once a workspace copy exists;
        # asking the shadow-aware resolver would answer "user" and make a
        # second fork look like a missing builtin.
        from qwenpaw.app.cron_templates.store import (
            get_cron_template_dir,
            resolve_builtin_template_dir,
        )

        ensure_builtin_templates_materialized()
        mine = get_cron_template_dir(workspace) / "weather-report"
        mine.mkdir(parents=True)
        (mine / "TEMPLATE.md").write_text(FOREIGN_DOC, encoding="utf-8")

        assert (
            resolve_builtin_template_dir("weather-report")
            == get_builtin_template_store_dir() / "weather-report"
        )


class TestListing:
    def test_materializing_does_not_duplicate_a_template(
        self,
        workspace: Path,
    ) -> None:
        from qwenpaw.app.cron_templates.service import CronTemplateService

        ensure_builtin_templates_materialized()
        names = [
            t.name for t in CronTemplateService(workspace).list_templates()
        ]

        assert len(names) == len(set(names))
        assert set(names) == shipped_names()

    def test_the_users_own_store_packages_are_never_listed(
        self,
        workspace: Path,
    ) -> None:
        from qwenpaw.app.cron_templates.service import CronTemplateService

        store = get_builtin_template_store_dir()
        for name in ("111", "222"):
            (store / name).mkdir(parents=True)
            (store / name / "TEMPLATE.md").write_text(
                FOREIGN_DOC, encoding="utf-8"
            )
        ensure_builtin_templates_materialized()

        service = CronTemplateService(workspace)
        for include_builtin in (True, False):
            names = {t.name for t in service.list_templates(include_builtin)}
            assert "111" not in names
            assert "222" not in names

    def test_excluding_builtins_still_excludes_materialized_ones(
        self,
        workspace: Path,
    ) -> None:
        # `include_builtin` filters by *source*, not by which root won.
        from qwenpaw.app.cron_templates.service import CronTemplateService

        ensure_builtin_templates_materialized()

        assert CronTemplateService(workspace).list_templates(False) == []

    def test_a_materialized_builtin_is_still_read_only(
        self,
        workspace: Path,
    ) -> None:
        from qwenpaw.app.cron_templates.service import CronTemplateService
        from qwenpaw.app.cron_templates.models import UpdateCronTemplateRequest
        from qwenpaw.exceptions import CronTemplateError

        ensure_builtin_templates_materialized()
        service = CronTemplateService(workspace)

        with pytest.raises(CronTemplateError, match="read-only"):
            service.update_template(
                "weather-report",
                UpdateCronTemplateRequest(title="mine"),
            )
        with pytest.raises(CronTemplateError, match="cannot be deleted"):
            service.delete_template("weather-report")

    def test_forking_copies_from_the_store(self, workspace: Path) -> None:
        from qwenpaw.app.cron_templates.service import CronTemplateService
        from qwenpaw.app.cron_templates.store import get_cron_template_dir

        ensure_builtin_templates_materialized()
        info = CronTemplateService(workspace).fork_builtin("weather-report")

        assert info.source == "user"
        forked = get_cron_template_dir(workspace) / "weather-report"
        assert (forked / "batch" / "weather.json").is_file()
