# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name,unused-argument
"""Service-level tests: CRUD, import/export, shared import pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qwenpaw.app.tool_batches import store
from qwenpaw.app.tool_batches.models import (
    CreateToolBatchRequest,
    UpdateToolBatchRequest,
)
from qwenpaw.app.tool_batches.service import ToolBatchService
from qwenpaw.exceptions import (
    SkillScanError,
    ToolBatchConflictError,
    ToolBatchError,
)

from .conftest import SAMPLE_ACTIONS, SAMPLE_BATCH, batch_json, make_zip


@pytest.fixture
def service(batch_root: Path) -> ToolBatchService:
    return ToolBatchService(batch_root)


def make_request(
    name: str = "daily-collect",
    content=None,
    **overrides,
) -> CreateToolBatchRequest:
    payload = {
        "name": name,
        "content": dict(SAMPLE_BATCH) if content is None else content,
    }
    payload.update(overrides)
    return CreateToolBatchRequest(**payload)


def make_scan_error(name: str = "danger") -> SkillScanError:
    from qwenpaw.security.skill_scanner import (
        Finding,
        ScanResult,
        Severity,
        ThreatCategory,
    )

    result = ScanResult(
        skill_name=name,
        skill_directory="/tmp",
        findings=[
            Finding(
                id="R1",
                rule_id="R1",
                category=ThreatCategory.COMMAND_INJECTION,
                severity=Severity.HIGH,
                title="Dangerous command",
                description="test",
                file_path=f"{name}.json",
                line_number=1,
            ),
        ],
    )
    return SkillScanError(result)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_writes_pool_file(service: ToolBatchService):
    info = service.create_batch(make_request())
    assert info.name == "daily-collect"
    assert info.description == "示例脚本"
    assert info.arg_names == ["greeting"]
    assert info.action_count == 1
    assert info.updated_at
    assert (service.root / "daily-collect.json").is_file()


def test_create_normalizes_name(service: ToolBatchService):
    info = service.create_batch(make_request(name="collect.json"))
    assert info.name == "collect"


def test_create_derives_description_from_content(
    service: ToolBatchService,
):
    content = {"actions": SAMPLE_ACTIONS, "description": "来自内容"}
    info = service.create_batch(make_request(content=content))
    assert info.description == "来自内容"


def test_create_explicit_description_wins(service: ToolBatchService):
    info = service.create_batch(make_request(description="显式说明"))
    assert info.description == "显式说明"
    content = service.get_batch("daily-collect").content
    assert content["description"] == "显式说明"


def test_create_empty_description_clears_content_key(
    service: ToolBatchService,
):
    info = service.create_batch(make_request(description=""))
    assert info.description == ""
    content = service.get_batch("daily-collect").content
    assert "description" not in content


def test_create_wraps_array_content_when_description_wanted(
    service: ToolBatchService,
):
    info = service.create_batch(
        make_request(content=list(SAMPLE_ACTIONS), description="包装"),
    )
    assert info.description == "包装"
    content = service.get_batch("daily-collect").content
    assert content["actions"] == SAMPLE_ACTIONS


def test_create_keeps_bare_array_without_description(
    service: ToolBatchService,
):
    service.create_batch(make_request(content=list(SAMPLE_ACTIONS)))
    content = service.get_batch("daily-collect").content
    assert content == SAMPLE_ACTIONS


def test_create_conflict_suggests_name(service: ToolBatchService):
    service.create_batch(make_request())
    with pytest.raises(ToolBatchConflictError) as excinfo:
        service.create_batch(make_request())
    detail = excinfo.value.detail
    assert detail["name"] == "daily-collect"
    assert detail["suggested_name"] == "daily-collect-2"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", ".DS_Store"])
def test_create_rejects_unsafe_name(service: ToolBatchService, bad: str):
    with pytest.raises(ToolBatchError):
        service.create_batch(make_request(name=bad))


def test_create_rejects_invalid_content(service: ToolBatchService):
    with pytest.raises(ToolBatchError, match="actions"):
        service.create_batch(make_request(content={"steps": []}))


def test_create_rejects_over_max_steps(service: ToolBatchService):
    from qwenpaw.agents.tools.run_tool_batch import MAX_BATCH_STEPS

    actions = [{"tool_name": "x"} for _ in range(MAX_BATCH_STEPS + 1)]
    with pytest.raises(ToolBatchError, match=str(MAX_BATCH_STEPS)):
        service.create_batch(make_request(content=actions))


def test_failed_create_leaves_no_file(service: ToolBatchService):
    with pytest.raises(ToolBatchError):
        service.create_batch(make_request(content={"steps": []}))
    assert not (service.root / "daily-collect.json").exists()
    assert service.list_batches() == []


def test_create_scan_rejection_writes_nothing(
    service: ToolBatchService,
    monkeypatch: pytest.MonkeyPatch,
):
    def _reject(*_args, **_kwargs):
        raise make_scan_error()

    monkeypatch.setattr(store, "scan_skill_directory", _reject)
    with pytest.raises(SkillScanError):
        service.create_batch(make_request())
    assert service.list_batches() == []


def test_write_lands_via_rename_not_truncation(
    service: ToolBatchService,
    monkeypatch: pytest.MonkeyPatch,
):
    """A cron preprocess may be reading this file while we replace it.

    Copying onto the live path truncates it first, so a concurrent reader
    can see half a script. Landing via ``os.replace`` means a reader gets
    either the old content or the new one.
    """
    service.create_batch(make_request(name="collect"))
    target = service.root / "collect.json"
    original = target.read_text(encoding="utf-8")

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src, dst, **kwargs):
        # The target must still hold the OLD content at rename time —
        # that is what proves it was never truncated in place.
        assert target.read_text(encoding="utf-8") == original
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", _spy)
    service.update_batch(
        "collect",
        UpdateToolBatchRequest(
            content=[{"tool_name": "read_file", "arguments": {}}],
        ),
    )

    assert len(replaced) == 1
    assert replaced[0][1] == str(target)
    assert "read_file" in target.read_text(encoding="utf-8")
    # No stray handoff file left in the pool.
    assert sorted(p.name for p in service.root.iterdir()) == [
        "collect.json",
    ]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_list_returns_pool_scripts_sorted(service: ToolBatchService):
    service.create_batch(make_request(name="b"))
    service.create_batch(make_request(name="a"))
    assert [b.name for b in service.list_batches()] == ["a", "b"]


def test_list_skips_malformed_file(service: ToolBatchService):
    """One broken script must not blank out the whole list."""
    service.create_batch(make_request(name="good"))
    (service.root / "broken.json").write_text(
        "{oops",
        encoding="utf-8",
    )
    assert [b.name for b in service.list_batches()] == ["good"]


def test_get_returns_content(service: ToolBatchService):
    service.create_batch(make_request())
    detail = service.get_batch("daily-collect")
    assert detail.content == SAMPLE_BATCH
    assert detail.arg_names == ["greeting"]
    assert detail.action_count == 1


def test_get_missing_raises(service: ToolBatchService):
    with pytest.raises(ToolBatchError, match="not found"):
        service.get_batch("nope")


def test_manually_dropped_file_is_listed(service: ToolBatchService):
    """The filesystem is the source of truth; no manifest to update."""
    pool = service.root
    store.write_batch_file(pool / "manual.json", SAMPLE_BATCH)
    assert [b.name for b in service.list_batches()] == ["manual"]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_replaces_content(service: ToolBatchService):
    service.create_batch(make_request())
    new_actions = [{"tool_name": "read_file", "arguments": {}}]
    info = service.update_batch(
        "daily-collect",
        UpdateToolBatchRequest(content=new_actions),
    )
    assert info.action_count == 1
    detail = service.get_batch("daily-collect")
    assert detail.content["actions"] == new_actions
    # Description was kept (None means leave as-is).
    assert detail.description == "示例脚本"


def test_update_description_none_keeps_current(
    service: ToolBatchService,
):
    service.create_batch(make_request())
    info = service.update_batch(
        "daily-collect",
        UpdateToolBatchRequest(description=None),
    )
    assert info.description == "示例脚本"


def test_update_description_empty_clears(service: ToolBatchService):
    service.create_batch(make_request())
    info = service.update_batch(
        "daily-collect",
        UpdateToolBatchRequest(description=""),
    )
    assert info.description == ""


def test_update_rejects_invalid_content_and_keeps_original(
    service: ToolBatchService,
):
    service.create_batch(make_request())
    with pytest.raises(ToolBatchError):
        service.update_batch(
            "daily-collect",
            UpdateToolBatchRequest(content={"steps": []}),
        )
    assert service.get_batch("daily-collect").content == SAMPLE_BATCH


def test_update_missing_raises(service: ToolBatchService):
    with pytest.raises(ToolBatchError, match="not found"):
        service.update_batch("nope", UpdateToolBatchRequest(description="x"))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_file(service: ToolBatchService):
    service.create_batch(make_request())
    assert service.delete_batch("daily-collect") is True
    assert service.list_batches() == []


def test_delete_missing_returns_false(service: ToolBatchService):
    assert service.delete_batch("nope") is False


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------


def test_export_then_reimport_is_lossless(service: ToolBatchService):
    service.create_batch(make_request())
    filename, blob = service.export_to_zip("daily-collect")
    assert filename == "daily-collect.zip"

    assert service.delete_batch("daily-collect") is True
    result = service.import_from_zip(blob)
    assert result == {
        "imported": ["daily-collect"],
        "conflicts": [],
    }
    assert service.get_batch("daily-collect").content == SAMPLE_BATCH


def test_export_missing_raises(service: ToolBatchService):
    with pytest.raises(ToolBatchError, match="not found"):
        service.export_to_zip("nope")


def test_import_single_json_directly(service: ToolBatchService):
    blob = make_zip({"collect.json": batch_json(description="导入")})
    result = service.import_from_zip(blob)
    assert result == {"imported": ["collect"], "conflicts": []}
    assert service.get_batch("collect").description == "导入"


def test_import_without_json_files_raises(service: ToolBatchService):
    with pytest.raises(ToolBatchError, match="No .json"):
        service.import_from_zip(make_zip({"readme.md": "x"}))


def test_import_multi_without_select_reports_candidates(
    service: ToolBatchService,
):
    service.create_batch(make_request(name="existing"))
    blob = make_zip(
        {
            "a.json": batch_json(description="甲"),
            "nested/b.json": batch_json(description="乙"),
            "existing.json": batch_json(),
        },
    )
    result = service.import_from_zip(blob)
    assert result["imported"] == []
    candidates = result["candidates"]
    assert [c["file_name"] for c in candidates] == [
        "a.json",
        "existing.json",
        "nested/b.json",
    ]
    by_name = {c["name"]: c for c in candidates}
    assert by_name["a"]["arg_names"] == ["greeting"]
    assert by_name["a"]["action_count"] == 1
    assert by_name["existing"]["exists"] is True
    assert by_name["b"]["exists"] is False
    # Two-phase means nothing was written.
    assert service.list_batches()[0].name == "existing"
    assert len(service.list_batches()) == 1
    assert all(c["valid"] is True for c in candidates)


def test_import_lists_candidates_even_when_one_file_is_broken(
    service: ToolBatchService,
):
    """One bad script must not cost the user the whole zip.

    The listing phase is where a user chooses; failing it outright means
    they cannot pick the good files at all. The refusal still happens on
    import if a broken file is actually selected (below).
    """
    blob = make_zip(
        {
            "good.json": batch_json(),
            "broken.json": "{not json",
            "empty.json": "[]",
        },
    )
    result = service.import_from_zip(blob)
    by_name = {c["name"]: c for c in result["candidates"]}
    assert set(by_name) == {"good", "broken", "empty"}
    assert by_name["good"]["valid"] is True

    assert by_name["broken"]["valid"] is False
    assert "not valid JSON" in by_name["broken"]["error"]
    assert by_name["broken"]["action_count"] == 0

    # Validation failures land here too, not just parse failures.
    assert by_name["empty"]["valid"] is False
    assert "at least one action" in by_name["empty"]["error"]

    assert service.list_batches() == []


def test_selecting_a_broken_file_still_refuses_the_import(
    service: ToolBatchService,
):
    blob = make_zip(
        {"good.json": batch_json(), "broken.json": "{not json"},
    )
    with pytest.raises(ToolBatchError, match="not valid JSON"):
        service.import_from_zip(blob, select=["good.json", "broken.json"])
    # All-or-nothing: the good file is not written either.
    assert service.list_batches() == []


def test_import_multi_with_select_imports_subset(
    service: ToolBatchService,
):
    blob = make_zip(
        {
            "a.json": batch_json(),
            "b.json": batch_json(),
            "c.json": batch_json(),
        },
    )
    result = service.import_from_zip(blob, select=["b.json", "c.json"])
    assert result["imported"] == ["b", "c"]
    assert {b.name for b in service.list_batches()} == {"b", "c"}


def test_import_select_unknown_file_raises(service: ToolBatchService):
    blob = make_zip({"a.json": batch_json()})
    with pytest.raises(ToolBatchError, match="not found in zip"):
        service.import_from_zip(blob, select=["ghost.json"])


def test_import_conflict_reports_all_and_writes_nothing(
    service: ToolBatchService,
):
    service.create_batch(make_request(name="a"))
    service.create_batch(make_request(name="b"))
    blob = make_zip({"a.json": batch_json(), "b.json": batch_json()})
    result = service.import_from_zip(blob, select=["a.json", "b.json"])
    assert result["imported"] == []
    assert {c["name"] for c in result["conflicts"]} == {"a", "b"}
    for conflict in result["conflicts"]:
        assert conflict["suggested_name"].endswith("-2")
        assert conflict["file_name"]
    # The originals are untouched.
    assert len(service.list_batches()) == 2


def test_import_overwrite(service: ToolBatchService):
    service.create_batch(make_request(name="collect"))
    replacement = batch_json(
        actions=[{"tool_name": "read_file", "arguments": {}}],
    )
    result = service.import_from_zip(
        make_zip({"collect.json": replacement}),
        overwrite=True,
    )
    assert result["imported"] == ["collect"]
    detail = service.get_batch("collect")
    assert detail.content["actions"][0]["tool_name"] == "read_file"


def test_import_rename_map(service: ToolBatchService):
    service.create_batch(make_request(name="collect"))
    result = service.import_from_zip(
        make_zip({"collect.json": batch_json()}),
        rename_map={"collect": "renamed"},
    )
    assert result["imported"] == ["renamed"]
    assert {b.name for b in service.list_batches()} == {"collect", "renamed"}


def test_import_duplicate_names_inside_one_zip_conflict(
    service: ToolBatchService,
):
    """Both files claim the same pool name; neither should land."""
    blob = make_zip(
        {
            "a/collect.json": batch_json(),
            "b/collect.json": batch_json(),
        },
    )
    result = service.import_from_zip(
        blob,
        select=["a/collect.json", "b/collect.json"],
    )
    assert result["imported"] == []
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["name"] == "collect"
    assert result["conflicts"][0]["file_name"] == "b/collect.json"
    assert service.list_batches() == []


def test_import_invalid_json_reports_file_name(
    service: ToolBatchService,
):
    blob = make_zip({"bad.json": "{oops"})
    with pytest.raises(ToolBatchError, match="bad.json"):
        service.import_from_zip(blob)
    assert service.list_batches() == []


def test_import_invalid_content_reports_file_name(
    service: ToolBatchService,
):
    blob = make_zip({"bad.json": json.dumps({"actions": []})})
    with pytest.raises(ToolBatchError, match="bad.json"):
        service.import_from_zip(blob)


def test_import_scan_rejection_writes_nothing(
    service: ToolBatchService,
    monkeypatch: pytest.MonkeyPatch,
):
    def _reject(*_args, **_kwargs):
        raise make_scan_error("collect")

    monkeypatch.setattr(store, "scan_skill_directory", _reject)
    with pytest.raises(SkillScanError):
        service.import_from_zip(make_zip({"collect.json": batch_json()}))
    assert service.list_batches() == []


# ---------------------------------------------------------------------------
# Shared pipeline via explicit file pairs (the install-batches path)
# ---------------------------------------------------------------------------


@pytest.fixture
def staged_sources(tmp_path: Path) -> list[tuple[Path, str]]:
    first = tmp_path / "first.json"
    first.write_text(batch_json(description="一"), encoding="utf-8")
    second = tmp_path / "second.json"
    second.write_text(batch_json(description="二"), encoding="utf-8")
    return [(first, "batch/first.json"), (second, "batch/second.json")]


def test_import_batch_files_copies_into_pool(
    service: ToolBatchService,
    staged_sources: list[tuple[Path, str]],
):
    result = service.import_batch_files(staged_sources)
    assert result == {
        "imported": ["first", "second"],
        "conflicts": [],
    }
    assert service.get_batch("first").description == "一"


def test_import_batch_files_conflicts_write_nothing(
    service: ToolBatchService,
    staged_sources: list[tuple[Path, str]],
):
    service.create_batch(make_request(name="first"))
    result = service.import_batch_files(staged_sources)
    assert result["imported"] == []
    assert result["conflicts"][0]["name"] == "first"
    assert result["conflicts"][0]["file_name"] == "batch/first.json"
    # "second" was clean but still must not land (all-or-nothing).
    assert {b.name for b in service.list_batches()} == {"first"}
