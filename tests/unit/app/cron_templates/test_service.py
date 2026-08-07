# -*- coding: utf-8 -*-
"""Service-level tests: create, delete, import/export, bundled skills."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.app.cron_templates import store
from qwenpaw.app.cron_templates.models import (
    CreateCronTemplateRequest,
    UpdateCronTemplateRequest,
)
from qwenpaw.app.cron_templates.service import CronTemplateService
from qwenpaw.exceptions import CronTemplateConflictError, CronTemplateError

from .conftest import BATCH_JSON, SKILL_DOC, make_zip, valid_zip_entries


@pytest.fixture
def service(workspace: Path) -> CronTemplateService:
    return CronTemplateService(workspace)


def make_request(
    name: str = "daily-brief", **overrides
) -> CreateCronTemplateRequest:
    payload = {
        "name": name,
        "title": "每日简报",
        "description": "每天 9 点生成简报",
        "category": "cron",
        "frequency": "每天 09:00",
        "emoji": "📊",
        "tags": ["personal"],
        "form": {"scheduleType": "cron", "cronCustom": "0 9 * * *"},
        "job": {
            "name": "每日简报",
            "schedule": {"type": "cron", "cron": "0 9 * * *"},
        },
    }
    payload.update(overrides)
    return CreateCronTemplateRequest(**payload)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_writes_full_package(service: CronTemplateService):
    info = service.create_template(
        make_request(
            batch_entry="batch/collect.json",
            batch_files={"collect": BATCH_JSON},
            skills={"brief-writer": SKILL_DOC},
        ),
    )
    assert info.name == "daily-brief"
    assert info.source == "user"
    assert info.batch_files == ["batch/collect.json"]
    assert info.skills == ["brief-writer"]
    assert info.payload.batch_entry == "batch/collect.json"
    assert info.title == "每日简报"
    assert info.tags == ["personal"]


def test_create_generates_readable_docs(service: CronTemplateService):
    info = service.create_template(
        make_request(
            batch_files={"collect": BATCH_JSON, "notify": BATCH_JSON},
            skills={"s": SKILL_DOC},
        ),
    )
    # The generated body should point at what shipped in the package —
    # every script, not just the one the chain happens to name, or the
    # docs understate what the package carries.
    assert "batch/collect.json" in info.content
    assert "batch/notify.json" in info.content
    assert "skills/s/" in info.content
    assert "每天 09:00" in info.content


def test_create_keeps_author_body(service: CronTemplateService):
    info = service.create_template(make_request(body="# 我自己写的说明"))
    assert info.content.strip() == "# 我自己写的说明"


def test_create_conflict_suggests_name(service: CronTemplateService):
    service.create_template(make_request())
    with pytest.raises(CronTemplateConflictError) as excinfo:
        service.create_template(make_request())
    assert excinfo.value.detail["suggested_name"] == "daily-brief-2"


def test_create_overwrite_replaces(service: CronTemplateService):
    service.create_template(make_request(description="v1"))
    info = service.create_template(
        make_request(description="v2", overwrite=True)
    )
    assert info.description == "v2"
    assert len(service.list_templates(include_builtin=False)) == 1


def test_create_overwrite_removes_stale_files(service: CronTemplateService):
    """Replacing a package must not leave files from the old version."""
    service.create_template(make_request(batch_files={"old": BATCH_JSON}))
    info = service.create_template(
        make_request(batch_files={"new": BATCH_JSON}, overwrite=True),
    )
    assert info.batch_files == ["batch/new.json"]


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "manifest.json"])
def test_create_rejects_unsafe_name(service: CronTemplateService, bad: str):
    with pytest.raises(CronTemplateError):
        service.create_template(make_request(name=bad))


def test_create_rejects_invalid_batch(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="not valid JSON"):
        service.create_template(make_request(batch_files={"bad": "{oops"}))


def test_create_packages_scripts_no_preprocess_names(
    service: CronTemplateService,
):
    """A package may ship scripts nothing in it declares.

    Saving a job as a template packages every script the job owns, not
    only the ones its preprocess chain names — the same reason applying a
    template copies every bundled file. Nothing may require an entry, and
    nothing may require the form to mention the scripts at all.
    """
    info = service.create_template(
        make_request(
            batch_files={"scan-unix": BATCH_JSON, "scan-windows": BATCH_JSON},
            form={"scheduleType": "cron"},
        ),
    )
    assert info.batch_files == [
        "batch/scan-unix.json",
        "batch/scan-windows.json",
    ]
    assert info.payload.batch_entry is None
    assert info.batch_entry_path == ""


def test_create_rejects_batch_entry_pointing_nowhere(
    service: CronTemplateService,
):
    """The guard the save-as-template entry rule is written against.

    Only the update path was covered. The console omits ``batch_entry``
    unless the declared script is actually among the packaged files, and
    this is what makes that caution necessary rather than superstition.
    """
    with pytest.raises(CronTemplateError):
        service.create_template(
            make_request(
                batch_files={"collect": BATCH_JSON},
                batch_entry="batch/gone.json",
            ),
        )


def test_failed_create_leaves_no_partial_dir(service: CronTemplateService):
    """Staged writes mean a rejected package leaves nothing behind."""
    with pytest.raises(CronTemplateError):
        service.create_template(make_request(batch_files={"bad": "{oops"}))
    assert not (
        store.get_cron_template_dir(service._ws) / "daily-brief"
    ).exists()
    assert service.list_templates(include_builtin=False) == []


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_list_includes_builtins(service: CronTemplateService):
    names = {t.name for t in service.list_templates()}
    assert "weather-report" in names
    assert "workspace-usage" in names
    # Migrated from the console's former hardcoded list.
    assert "daily-tech-news-brief" in names


def test_every_shipped_builtin_is_valid(service: CronTemplateService):
    """Guards the packages we ship: a malformed one would vanish silently.

    ``list_templates`` skips packages it cannot read, so a broken shipped
    package would just not appear rather than raise. Validate each one
    explicitly instead.
    """
    from qwenpaw.app.cron_templates.store import (
        get_builtin_cron_template_dir,
        iter_template_dirs,
        validate_template_package,
    )

    package_dirs = list(iter_template_dirs(get_builtin_cron_template_dir()))
    assert len(package_dirs) >= 12
    for package_dir in package_dirs:
        validate_template_package(package_dir, package_dir.name)

    listed = {t.name for t in service.list_templates(include_builtin=True)}
    assert {p.name for p in package_dirs} <= listed


def test_shipped_builtins_localize_or_state_their_text(
    service: CronTemplateService,
):
    """Every shipped package must be displayable in some language.

    The migrated ones carry i18n keys so they follow the UI language; the
    hand-written examples carry literals. Either is fine — having
    neither would render a blank card.
    """
    for info in service.list_templates():
        if info.source != "builtin":
            continue
        assert info.title_key or info.title, info.name
        assert info.description_key or info.description, info.name


def test_shipped_builtins_do_not_embed_a_dispatch_target(
    service: CronTemplateService,
):
    """A shipped template must not preset someone else's user/session id."""
    for info in service.list_templates():
        if info.source != "builtin":
            continue
        dispatch = info.payload.form.get("dispatch")
        if not isinstance(dispatch, dict):
            continue
        target = dispatch.get("target") or {}
        assert not target.get("user_id"), info.name
        assert not target.get("session_id"), info.name


def test_platform_specific_batches_are_named_for_their_platform(
    service: CronTemplateService,
):
    """A shell-specific batch file must say which platform it is for.

    ``df``/``du`` do not exist on Windows and PowerShell is not on macOS. A
    package may ship both (the skill picks at runtime), so the guard is per
    *file*: a script using one platform's commands must not be named for the
    other, and must not mix the two.
    """
    unix_only = ("df ", "du ", "awk ")
    windows_only = ("powershell", "Get-ChildItem", "Get-PSDrive")

    checked = 0
    for info in service.list_templates():
        if info.source != "builtin":
            continue
        for rel in info.batch_files:
            blob = service.read_package_file(info.name, rel)
            uses_unix = any(token in blob for token in unix_only)
            uses_windows = any(token in blob for token in windows_only)
            if not (uses_unix or uses_windows):
                continue
            checked += 1

            assert not (uses_unix and uses_windows), (
                f"{info.name}/{rel} mixes Unix and PowerShell commands; "
                f"split them into one file per platform"
            )
            if uses_unix:
                assert "windows" not in rel, f"{info.name}/{rel}"
            if uses_windows:
                assert "windows" in rel, (
                    f"{info.name}/{rel} uses PowerShell but its filename "
                    f"does not mark it as the Windows script"
                )
    assert checked, "no platform-specific batch scripts were checked"


def test_workspace_usage_ships_both_platform_scripts(
    service: CronTemplateService,
):
    """One package, one skill, one script per platform."""
    info = service.get_template("workspace-usage")
    assert sorted(info.batch_files) == [
        "batch/scan-unix.json",
        "batch/scan-windows.json",
    ]
    assert info.skills == ["disk-usage-advisor"]
    # With two scripts there is no single entry point; the skill picks.
    assert info.payload.batch_entry is None
    assert info.batch_entry_path == ""


def test_workspace_usage_skill_documents_both_platforms(
    service: CronTemplateService,
):
    """The platform choice lives in the skill, so it must be spelled out."""
    skill = service.read_package_file(
        "workspace-usage",
        "skills/disk-usage-advisor/SKILL.md",
    )
    for needle in (
        "scan-unix.json",
        "scan-windows.json",
        "darwin",
        "win32",
    ):
        assert needle in skill, needle


def test_shipped_batch_scripts_have_no_personal_paths(
    service: CronTemplateService,
):
    """No home directory of whoever authored the template."""
    for info in service.list_templates():
        if info.source != "builtin" or not info.batch_files:
            continue
        for rel in info.batch_files:
            blob = service.read_package_file(info.name, rel)
            for marker in ("/Users/", "/home/", "C:\\Users\\"):
                assert marker not in blob, f"{info.name}/{rel} has {marker}"


def test_list_can_hide_builtins(service: CronTemplateService):
    assert service.list_templates(include_builtin=False) == []


# ---------------------------------------------------------------------------
# list_batch_scripts — what the job form's script picker reads
# ---------------------------------------------------------------------------


def test_list_batch_scripts_describes_shipped_scripts(
    service: CronTemplateService,
):
    by_ref = {s.ref: s for s in service.list_batch_scripts()}
    weather = by_ref["weather-report/batch/weather.json"]
    assert weather.template == "weather-report"
    assert weather.template_source == "builtin"
    assert weather.file_path == "batch/weather.json"
    assert weather.file_name == "weather.json"
    # Derived by the pool's own describer, so a packaged script and a pool
    # script render through identical fields.
    assert weather.arg_names == ["city"]
    assert weather.action_count == 1
    assert weather.preview_actions
    # None of the shipped scripts carry one, which makes the picker's
    # "fall back to <title>/<file>" tooltip the common case, not the edge.
    assert weather.description == ""


def test_list_batch_scripts_leaves_the_title_unresolved(
    service: CronTemplateService,
):
    """Rendering it here would emit the key for i18n-keyed packages."""
    by_template = {s.template: s for s in service.list_batch_scripts()}
    weather = by_template["weather-report"]
    assert weather.template_title or weather.template_title_key


def test_list_batch_scripts_skips_a_broken_file(
    service: CronTemplateService,
):
    """One unparseable script must not blank out the whole picker."""
    service.create_template(make_request(batch_files={"ok": BATCH_JSON}))
    package = store.get_cron_template_dir(service._ws) / "daily-brief"
    (package / "batch" / "broken.json").write_text("{oops", encoding="utf-8")

    refs = {s.ref for s in service.list_batch_scripts()}
    assert "daily-brief/batch/ok.json" in refs
    assert "daily-brief/batch/broken.json" not in refs


def test_list_batch_scripts_lets_a_user_package_shadow_a_builtin(
    service: CronTemplateService,
):
    """Same precedence the runtime resolver applies, so refs agree."""
    service.create_template(
        make_request(name="weather-report", batch_files={"mine": BATCH_JSON}),
    )
    scripts = [
        s
        for s in service.list_batch_scripts()
        if s.template == "weather-report"
    ]
    assert [s.file_name for s in scripts] == ["mine.json"]
    assert scripts[0].template_source == "user"


def test_list_batch_scripts_can_hide_builtins(service: CronTemplateService):
    assert service.list_batch_scripts(include_builtin=False) == []


def test_list_puts_user_templates_first(service: CronTemplateService):
    service.create_template(make_request())
    listed = service.list_templates()
    assert listed[0].name == "daily-brief"
    assert listed[0].source == "user"


def test_list_skips_malformed_package(service: CronTemplateService):
    """One broken package must not blank out the whole list."""
    service.create_template(make_request())
    broken = store.get_cron_template_dir(service._ws) / "broken"
    broken.mkdir()
    (broken / "TEMPLATE.md").write_text("---\nname: broken\n---\n", "utf-8")
    # No template.json -> unreadable.
    names = {t.name for t in service.list_templates(include_builtin=False)}
    assert names == {"daily-brief"}


def test_get_missing_raises(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="not found"):
        service.get_template("nope")


def test_read_package_file(service: CronTemplateService):
    service.create_template(make_request(batch_files={"collect": BATCH_JSON}))
    content = service.read_package_file("daily-brief", "batch/collect.json")
    assert json.loads(content)["actions"]


@pytest.mark.parametrize(
    "bad_path",
    ["../../../etc/passwd", "/etc/passwd", "", "   "],
)
def test_read_package_file_rejects_escape(
    service: CronTemplateService,
    bad_path: str,
):
    service.create_template(make_request())
    with pytest.raises(CronTemplateError):
        service.read_package_file("daily-brief", bad_path)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_patches_only_given_fields(service: CronTemplateService):
    service.create_template(make_request())
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(description="换了说明"),
    )
    assert info.description == "换了说明"
    # Everything else survives.
    assert info.title == "每日简报"
    assert info.frequency == "每天 09:00"
    assert info.emoji == "📊"
    assert info.tags == ["personal"]
    assert info.payload.form["cronCustom"] == "0 9 * * *"


def test_update_preserves_unmentioned_files(service: CronTemplateService):
    """The whole point: editing metadata must not drop batch files/skills."""
    service.create_template(
        make_request(
            batch_files={"collect": BATCH_JSON},
            skills={"writer": SKILL_DOC},
            extra_files={"assets": {"note.txt": "keep me"}},
        ),
    )
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(title="新标题"),
    )
    assert info.title == "新标题"
    assert info.batch_files == ["batch/collect.json"]
    assert info.skills == ["writer"]
    assert "assets/note.txt" in info.files
    assert (
        service.read_package_file("daily-brief", "assets/note.txt")
        == "keep me"
    )


def test_update_can_add_a_batch_file(service: CronTemplateService):
    service.create_template(make_request(batch_files={"collect": BATCH_JSON}))
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(batch_files={"extra": BATCH_JSON}),
    )
    assert info.batch_files == ["batch/collect.json", "batch/extra.json"]


def test_update_can_replace_a_batch_file(service: CronTemplateService):
    service.create_template(make_request(batch_files={"collect": BATCH_JSON}))
    replacement = json.dumps(
        {"actions": [{"tool_name": "new", "arguments": {}}]}
    )
    service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(batch_files={"collect": replacement}),
    )
    content = service.read_package_file("daily-brief", "batch/collect.json")
    assert json.loads(content)["actions"][0]["tool_name"] == "new"


def test_update_can_remove_a_batch_file(service: CronTemplateService):
    service.create_template(
        make_request(batch_files={"collect": BATCH_JSON, "extra": BATCH_JSON}),
    )
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(remove_batch_files=["extra"]),
    )
    assert info.batch_files == ["batch/collect.json"]


def test_update_remove_accepts_name_with_extension(
    service: CronTemplateService,
):
    service.create_template(
        make_request(batch_files={"collect": BATCH_JSON, "extra": BATCH_JSON}),
    )
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(remove_batch_files=["extra.json"]),
    )
    assert info.batch_files == ["batch/collect.json"]


@pytest.mark.parametrize("bad", ["../escape.json", "a/b.json", "", "  "])
def test_update_remove_rejects_path_escape(
    service: CronTemplateService,
    bad: str,
):
    service.create_template(make_request(batch_files={"collect": BATCH_JSON}))
    with pytest.raises(CronTemplateError, match="Invalid batch file name"):
        service.update_template(
            "daily-brief",
            UpdateCronTemplateRequest(remove_batch_files=[bad]),
        )


def test_update_clears_batch_entry_with_empty_string(
    service: CronTemplateService,
):
    service.create_template(
        make_request(
            batch_entry="batch/collect.json",
            batch_files={"collect": BATCH_JSON},
        ),
    )
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(batch_entry=""),
    )
    assert info.payload.batch_entry is None
    assert info.batch_entry_path == ""


def test_update_keeps_batch_entry_when_omitted(service: CronTemplateService):
    service.create_template(
        make_request(
            batch_entry="batch/collect.json",
            batch_files={"collect": BATCH_JSON},
        ),
    )
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(title="t"),
    )
    assert info.payload.batch_entry == "batch/collect.json"


def test_update_rejects_batch_entry_pointing_nowhere(
    service: CronTemplateService,
):
    service.create_template(make_request())
    with pytest.raises(CronTemplateError, match="batch_entry"):
        service.update_template(
            "daily-brief",
            UpdateCronTemplateRequest(batch_entry="batch/ghost.json"),
        )


def test_update_honours_empty_tag_list_as_a_clear(
    service: CronTemplateService,
):
    service.create_template(make_request())
    info = service.update_template(
        "daily-brief",
        UpdateCronTemplateRequest(tags=[]),
    )
    assert info.tags == []


def test_editing_a_forked_builtin_drops_its_i18n_key(
    service: CronTemplateService,
):
    """Otherwise the user's new title loses to the shipped translation."""
    forked = service.fork_builtin("daily-tech-news-brief")
    assert forked.title_key  # inherited from the shipped package

    edited = service.update_template(
        "daily-tech-news-brief",
        UpdateCronTemplateRequest(title="我的科技早报"),
    )
    assert edited.title == "我的科技早报"
    assert edited.title_key == ""
    # Untouched fields keep deferring to i18n.
    assert edited.description_key
    assert edited.frequency_key


def test_blank_literal_keeps_the_i18n_key(service: CronTemplateService):
    """Clearing a field should fall back to i18n, not blank the card."""
    service.fork_builtin("daily-tech-news-brief")
    edited = service.update_template(
        "daily-tech-news-brief",
        UpdateCronTemplateRequest(title="   "),
    )
    assert edited.title_key


def test_update_missing_template_raises(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="not found"):
        service.update_template("nope", UpdateCronTemplateRequest(title="x"))


def test_update_builtin_refused_with_fork_hint(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="read-only"):
        service.update_template(
            "workspace-usage",
            UpdateCronTemplateRequest(title="x"),
        )


def test_update_builtin_works_after_fork(service: CronTemplateService):
    service.fork_builtin("workspace-usage")
    info = service.update_template(
        "workspace-usage",
        UpdateCronTemplateRequest(title="我的巡检"),
    )
    assert info.title == "我的巡检"
    # Forking kept both batch scripts, and editing did not drop them.
    assert info.batch_files == [
        "batch/scan-unix.json",
        "batch/scan-windows.json",
    ]


def test_failed_update_leaves_package_intact(service: CronTemplateService):
    service.create_template(make_request(batch_files={"collect": BATCH_JSON}))
    with pytest.raises(CronTemplateError):
        service.update_template(
            "daily-brief",
            UpdateCronTemplateRequest(batch_files={"bad": "{oops"}),
        )
    info = service.get_template("daily-brief")
    assert info.batch_files == ["batch/collect.json"]
    assert info.description == "每天 9 点生成简报"


# ---------------------------------------------------------------------------
# Delete / fork
# ---------------------------------------------------------------------------


def test_delete_removes_package(service: CronTemplateService):
    service.create_template(make_request())
    assert service.delete_template("daily-brief") is True
    assert service.list_templates(include_builtin=False) == []
    assert (
        "daily-brief"
        not in store.read_template_manifest(service._ws)["templates"]
    )


def test_delete_missing_returns_false(service: CronTemplateService):
    assert service.delete_template("nope") is False


def test_delete_builtin_refused(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="cannot be deleted"):
        service.delete_template("workspace-usage")


def test_fork_builtin_makes_it_editable(service: CronTemplateService):
    info = service.fork_builtin("workspace-usage")
    assert info.source == "user"
    # The pool copy now shadows the packaged one, so it appears once.
    matches = [
        t for t in service.list_templates() if t.name == "workspace-usage"
    ]
    assert len(matches) == 1
    assert matches[0].source == "user"
    assert service.delete_template("workspace-usage") is True


def test_fork_twice_conflicts(service: CronTemplateService):
    service.fork_builtin("weather-report")
    with pytest.raises(CronTemplateConflictError):
        service.fork_builtin("weather-report")


def test_fork_unknown_builtin_raises(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="not found"):
        service.fork_builtin("no-such-builtin")


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


def test_import_then_export_round_trips(service: CronTemplateService):
    result = service.import_from_zip(make_zip(valid_zip_entries()))
    assert result == {
        "imported": ["sample-template"],
        "count": 1,
        "conflicts": [],
    }

    filename, blob = service.export_to_zip("sample-template")
    assert filename == "sample-template.zip"

    assert service.delete_template("sample-template") is True
    again = service.import_from_zip(blob)
    assert again["imported"] == ["sample-template"]
    info = service.get_template("sample-template")
    assert info.batch_files == ["batch/go.json"]


def test_import_conflict_reports_all_and_writes_nothing(
    service: CronTemplateService,
):
    blob = make_zip(valid_zip_entries())
    service.import_from_zip(blob)
    result = service.import_from_zip(blob)
    assert result["count"] == 0
    assert result["conflicts"][0]["suggested_name"] == "sample-template-2"


def test_import_overwrite(service: CronTemplateService):
    blob = make_zip(valid_zip_entries())
    service.import_from_zip(blob)
    result = service.import_from_zip(blob, overwrite=True)
    assert result["imported"] == ["sample-template"]


def test_import_rename_map(service: CronTemplateService):
    blob = make_zip(valid_zip_entries())
    service.import_from_zip(blob)
    result = service.import_from_zip(
        blob,
        rename_map={"sample-template": "renamed"},
    )
    assert result["imported"] == ["renamed"]


def test_import_target_name(service: CronTemplateService):
    result = service.import_from_zip(
        make_zip(valid_zip_entries()),
        target_name="my-own-name",
    )
    assert result["imported"] == ["my-own-name"]


def test_import_target_name_rejected_for_multi_package(
    service: CronTemplateService,
):
    entries = {**valid_zip_entries("first"), **valid_zip_entries("second")}
    with pytest.raises(CronTemplateError, match="single-template"):
        service.import_from_zip(make_zip(entries), target_name="x")


def test_import_duplicate_names_inside_one_zip_conflict(
    service: CronTemplateService,
):
    """Both dirs declare the same frontmatter name; neither should land."""
    entries = {**valid_zip_entries("first"), **valid_zip_entries("second")}
    result = service.import_from_zip(make_zip(entries))
    assert result["count"] == 0
    assert result["conflicts"]
    assert service.list_templates(include_builtin=False) == []


def test_export_missing_raises(service: CronTemplateService):
    with pytest.raises(CronTemplateError, match="not found"):
        service.export_to_zip("nope")


def test_export_builtin_works(service: CronTemplateService):
    filename, blob = service.export_to_zip("weather-report")
    assert filename == "weather-report.zip"
    assert len(blob) > 0


def test_imported_package_records_origin(service: CronTemplateService):
    service.import_from_zip(make_zip(valid_zip_entries()))
    entry = store.read_template_manifest(service._ws)["templates"][
        "sample-template"
    ]
    assert entry["installed_from"] == "upload"
