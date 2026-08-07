# -*- coding: utf-8 -*-
"""Store-level tests: frontmatter, validation, zip safety, manifest."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from qwenpaw.app.cron_templates import store
from qwenpaw.app.cron_templates.models import (
    CronTemplateFrontmatter,
    CronTemplatePayload,
)
from qwenpaw.exceptions import CronTemplateError

from .conftest import (
    BATCH_JSON,
    TEMPLATE_DOC,
    TEMPLATE_PAYLOAD,
    make_zip,
    valid_zip_entries,
)


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


def test_normalize_accepts_plain_name():
    assert store.normalize_template_name(" daily-brief ") == "daily-brief"


@pytest.mark.parametrize(
    "name",
    ["", "   ", ".", "..", "a/b", "a\\b", "manifest.json", ".DS_Store"],
)
def test_normalize_rejects_unsafe_names(name: str):
    with pytest.raises(CronTemplateError):
        store.normalize_template_name(name)


def test_normalize_rejects_nul_byte():
    with pytest.raises(CronTemplateError):
        store.normalize_template_name("a\x00b")


def test_safe_template_dir_refuses_escape(tmp_path: Path):
    with pytest.raises(CronTemplateError):
        store.safe_template_dir(tmp_path, "../outside")


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_parse_frontmatter_reads_metadata(package_dir: Path):
    fm, body = store.parse_template_frontmatter(package_dir / "TEMPLATE.md")
    assert fm.name == "sample-template"
    assert fm.description == "示例模板"
    assert fm.title == "示例"
    assert fm.category == "cron"
    assert fm.frequency == "每天 09:00"
    assert fm.emoji == "📊"
    assert fm.tags == ["personal", "reminder"]
    assert fm.version_text == "1.2"
    assert "正文说明" in body


_DOC_UNKNOWN_CATEGORY = (
    "---\n"
    "name: x\n"
    "metadata:\n"
    "  qwenpaw:\n"
    "    category: weird\n"
    "---\n\nbody\n"
)


def test_parse_frontmatter_defaults_unknown_category(tmp_path: Path):
    doc = tmp_path / "TEMPLATE.md"
    doc.write_text(_DOC_UNKNOWN_CATEGORY, encoding="utf-8")
    fm, _ = store.parse_template_frontmatter(doc)
    assert fm.category == "cron"


def test_parse_frontmatter_survives_malformed_yaml(tmp_path: Path):
    """A broken package must still be listable, not crash the whole page."""
    doc = tmp_path / "TEMPLATE.md"
    doc.write_text("---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
    fm, body = store.parse_template_frontmatter(doc, "fallback-name")
    assert fm.name == "fallback-name"
    assert body == ""


_DOC_FLAT_METADATA = (
    "---\n"
    "name: x\n"
    "description: d\n"
    "metadata:\n"
    "  category: once\n"
    "---\n\nb\n"
)


def test_parse_frontmatter_accepts_flat_metadata(tmp_path: Path):
    doc = tmp_path / "TEMPLATE.md"
    doc.write_text(_DOC_FLAT_METADATA, encoding="utf-8")
    fm, _ = store.parse_template_frontmatter(doc)
    assert fm.category == "once"


def test_render_doc_round_trips(tmp_path: Path):
    fm = CronTemplateFrontmatter(
        name="round-trip",
        description="说明",
        title="标题",
        category="once",
        frequency="每周一",
        emoji="🎯",
        tags=["team"],
        version_text="2.0",
    )
    doc = tmp_path / "TEMPLATE.md"
    doc.write_text(store.render_template_doc(fm, "# 正文"), encoding="utf-8")

    parsed, body = store.parse_template_frontmatter(doc)
    assert parsed.model_dump() == fm.model_dump()
    assert body.strip() == "# 正文"


_DOC_COMMA_TAGS = (
    "---\n"
    "name: x\n"
    "metadata:\n"
    "  qwenpaw:\n"
    "    tags: 'team, personal'\n"
    "---\n\nb\n"
)


def test_tags_accept_comma_string(tmp_path: Path):
    doc = tmp_path / "TEMPLATE.md"
    doc.write_text(_DOC_COMMA_TAGS, encoding="utf-8")
    fm, _ = store.parse_template_frontmatter(doc)
    assert fm.tags == ["team", "personal"]


# ---------------------------------------------------------------------------
# Package read / validate
# ---------------------------------------------------------------------------


def test_read_package_collects_contents(package_dir: Path):
    info = store.read_template_package(package_dir, "sample-template")
    assert info.batch_files == ["batch/go.json"]
    assert info.skills == ["sample-skill"]
    assert "TEMPLATE.md" in info.files
    assert "skills/sample-skill/SKILL.md" in info.files
    assert info.payload.form["cronCustom"] == "0 9 * * *"


def test_read_package_exposes_absolute_paths(package_dir: Path):
    """Clients need real paths to resolve ``{{batch_entry}}``."""
    payload = json.loads((package_dir / "template.json").read_text("utf-8"))
    payload["batch_entry"] = "batch/go.json"
    (package_dir / "template.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    info = store.read_template_package(package_dir, "sample-template")
    assert info.package_dir == str(package_dir.resolve())
    assert info.batch_entry_path == str(
        (package_dir / "batch" / "go.json").resolve(),
    )


def test_batch_entry_path_empty_when_unset(package_dir: Path):
    info = store.read_template_package(package_dir, "sample-template")
    assert info.payload.batch_entry is None
    assert info.batch_entry_path == ""
    assert info.package_dir


def test_validate_requires_doc(tmp_path: Path):
    root = tmp_path / "nodoc"
    root.mkdir()
    (root / "template.json").write_text(TEMPLATE_PAYLOAD, encoding="utf-8")
    with pytest.raises(CronTemplateError, match="TEMPLATE.md"):
        store.validate_template_package(root)


def test_validate_requires_payload(tmp_path: Path):
    root = tmp_path / "nopayload"
    root.mkdir()
    (root / "TEMPLATE.md").write_text(TEMPLATE_DOC, encoding="utf-8")
    with pytest.raises(CronTemplateError, match="template.json"):
        store.validate_template_package(root)


def test_validate_rejects_empty_payload(tmp_path: Path):
    root = tmp_path / "emptypayload"
    root.mkdir()
    (root / "TEMPLATE.md").write_text(TEMPLATE_DOC, encoding="utf-8")
    (root / "template.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CronTemplateError, match="form.*job"):
        store.validate_template_package(root)


def test_validate_rejects_non_object_payload(tmp_path: Path):
    root = tmp_path / "listpayload"
    root.mkdir()
    (root / "TEMPLATE.md").write_text(TEMPLATE_DOC, encoding="utf-8")
    (root / "template.json").write_text("[]", encoding="utf-8")
    with pytest.raises(CronTemplateError, match="JSON object"):
        store.validate_template_package(root)


def test_validate_rejects_malformed_batch(package_dir: Path):
    (package_dir / "batch" / "go.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(CronTemplateError, match="not valid JSON"):
        store.validate_template_package(package_dir)


def test_validate_rejects_batch_without_actions(package_dir: Path):
    (package_dir / "batch" / "go.json").write_text(
        json.dumps({"steps": []}),
        encoding="utf-8",
    )
    with pytest.raises(CronTemplateError, match="actions"):
        store.validate_template_package(package_dir)


def test_validate_accepts_bare_actions_array(package_dir: Path):
    (package_dir / "batch" / "go.json").write_text(
        json.dumps([{"tool_name": "x", "arguments": {}}]),
        encoding="utf-8",
    )
    store.validate_template_package(package_dir)


def test_validate_rejects_missing_batch_entry(package_dir: Path):
    payload = json.loads((package_dir / "template.json").read_text("utf-8"))
    payload["batch_entry"] = "batch/absent.json"
    (package_dir / "template.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    with pytest.raises(CronTemplateError, match="batch_entry"):
        store.validate_template_package(package_dir)


def test_validate_rejects_skill_without_skill_md(package_dir: Path):
    (package_dir / "skills" / "sample-skill" / "SKILL.md").unlink()
    with pytest.raises(CronTemplateError, match="missing SKILL.md"):
        store.validate_template_package(package_dir)


# ---------------------------------------------------------------------------
# Bundled batch scripts get the pool's save-time gate
#
# A preprocess step can reference `<template>/batch/<file>.json` directly,
# which runs it unattended with no model in the loop — so "parses and is a
# list" is not enough. These pin the checks a pool script already gets.
# ---------------------------------------------------------------------------


def _write_batch(package_dir: Path, content: object) -> None:
    (package_dir / "batch" / "go.json").write_text(
        json.dumps(content),
        encoding="utf-8",
    )


def test_validate_rejects_nested_run_tool_batch_in_a_package(
    package_dir: Path,
):
    _write_batch(
        package_dir,
        [{"tool_name": "run_tool_batch", "arguments": {"file_path": "x"}}],
    )
    with pytest.raises(CronTemplateError, match="nested batches"):
        store.validate_template_package(package_dir)


def test_validate_rejects_a_package_batch_with_too_many_steps(
    package_dir: Path,
):
    from qwenpaw.app.tool_batches.store import MAX_BATCH_STEPS

    _write_batch(
        package_dir,
        [{"tool_name": "x"} for _ in range(MAX_BATCH_STEPS + 1)],
    )
    with pytest.raises(CronTemplateError, match="Too many steps"):
        store.validate_template_package(package_dir)


def test_validate_rejects_an_oversized_package_batch(package_dir: Path):
    """Padding past the scanner's 5 MB skip must not be a way in."""
    from qwenpaw.app.tool_batches.store import MAX_BATCH_FILE_BYTES

    _write_batch(
        package_dir,
        [{"tool_name": "x", "arguments": {"pad": "a" * MAX_BATCH_FILE_BYTES}}],
    )
    with pytest.raises(CronTemplateError, match="too large"):
        store.validate_template_package(package_dir)


def test_validate_names_the_offending_batch_file(package_dir: Path):
    _write_batch(package_dir, [{"arguments": {}}])
    with pytest.raises(CronTemplateError, match="batch/go.json"):
        store.validate_template_package(package_dir)


def test_every_shipped_builtin_passes_the_gate():
    """The gate is only worth having if the packages we ship clear it."""
    builtin_root = store.get_builtin_cron_template_dir()
    packages = list(store.iter_template_dirs(builtin_root))
    assert packages, "no builtin templates found"
    for package in packages:
        store.validate_template_package(package, package.name)


def test_scan_sees_a_shell_payload_inside_a_packaged_batch(
    package_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The regression test for the hole this feature had to close.

    No shipped scanner rule lists `json` in its `file_types`, so a template
    package's `batch/*.json` was read and matched against nothing at all.
    """
    payload = "chmod 777 /etc/passwd"
    seen: dict[str, str] = {}

    def _fake_scan(dir_path: Path, skill_name: str = "", **_kwargs):
        for path in sorted(dir_path.rglob("*")):
            if path.is_file():
                seen[path.name] = path.read_text(encoding="utf-8")
        return None

    monkeypatch.setattr(store, "scan_skill_directory", _fake_scan)
    (package_dir / "batch" / "sub").mkdir()
    (package_dir / "batch" / "sub" / "deep.json").write_text(
        json.dumps(
            [
                {
                    "tool_name": "execute_shell_command",
                    "arguments": {"command": payload},
                },
            ],
        ),
        encoding="utf-8",
    )

    store.scan_template_dir_or_raise(package_dir, "sample-template")

    shell = [
        name
        for name, body in seen.items()
        if name.endswith(".sh") and payload in body
    ]
    assert shell, f"payload never reached the scanner as shell: {sorted(seen)}"


def test_scan_surrogate_never_stays_in_the_package(
    package_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Templates copy the whole staged dir, so a leftover would ship."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("scanner exploded")

    before = sorted(p.name for p in package_dir.iterdir())
    monkeypatch.setattr(store, "scan_skill_directory", lambda *a, **k: None)
    store.scan_template_dir_or_raise(package_dir, "sample-template")
    assert sorted(p.name for p in package_dir.iterdir()) == before

    monkeypatch.setattr(store, "scan_skill_directory", _boom)
    with pytest.raises(RuntimeError):
        store.scan_template_dir_or_raise(package_dir, "sample-template")
    assert sorted(p.name for p in package_dir.iterdir()) == before


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def test_write_package_materializes_tree(tmp_path: Path):
    target = tmp_path / "written"
    store.write_template_package(
        target,
        frontmatter_data=CronTemplateFrontmatter(
            name="written", description="d"
        ),
        body="# doc",
        payload=CronTemplatePayload(form={"a": 1}),
        # Extension is added when the author omits it.
        batch_files={"go": BATCH_JSON},
        skills={"s1": "---\nname: s1\ndescription: d\n---\n"},
        extra_files={"assets": {"note.txt": "hi"}},
    )
    assert (target / "TEMPLATE.md").is_file()
    assert (target / "batch" / "go.json").is_file()
    assert (target / "skills" / "s1" / "SKILL.md").is_file()
    assert (target / "assets" / "note.txt").read_text("utf-8") == "hi"
    store.validate_template_package(target)


def test_write_package_rejects_traversal_in_extra_files(tmp_path: Path):
    with pytest.raises(CronTemplateError, match="Unsafe file path"):
        store.write_template_package(
            tmp_path / "bad",
            frontmatter_data=CronTemplateFrontmatter(name="bad"),
            body="",
            payload=CronTemplatePayload(form={"a": 1}),
            extra_files={"../escape.txt": "x"},
        )


# ---------------------------------------------------------------------------
# Zip safety
# ---------------------------------------------------------------------------


def test_extract_rejects_non_zip():
    with pytest.raises(CronTemplateError, match="not a valid zip"):
        store.extract_template_zip(b"definitely not a zip")


def test_extract_rejects_path_traversal():
    entries = valid_zip_entries()
    entries["../../pwned.txt"] = "x"
    with pytest.raises(CronTemplateError, match="Unsafe path in zip"):
        store.extract_template_zip(make_zip(entries))


def test_extract_rejects_symlink():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in valid_zip_entries().items():
            zf.writestr(name, content)
        info = zipfile.ZipInfo("sample-template/link")
        # 0o120000 marks a symlink in the high bits of external_attr.
        info.external_attr = (0o120777 | 0o120000) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(CronTemplateError, match="Symlink not allowed"):
        store.extract_template_zip(buffer.getvalue())


def test_extract_rejects_too_many_entries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(store, "MAX_TEMPLATE_ZIP_ENTRIES", 2)
    with pytest.raises(CronTemplateError, match="too many entries"):
        store.extract_template_zip(make_zip(valid_zip_entries()))


def test_extract_rejects_oversized_payload(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(store, "MAX_TEMPLATE_ZIP_BYTES", 10)
    with pytest.raises(CronTemplateError, match="exceeds"):
        store.extract_template_zip(make_zip(valid_zip_entries()))


def test_extract_rejects_zip_without_template_doc():
    with pytest.raises(CronTemplateError, match="No valid template packages"):
        store.extract_template_zip(make_zip({"foo/bar.txt": "x"}))


def test_extract_finds_single_nested_package():
    tmp_dir, found = store.extract_template_zip(make_zip(valid_zip_entries()))
    try:
        assert [name for _, name in found] == ["sample-template"]
    finally:
        __import__("shutil").rmtree(tmp_dir, ignore_errors=True)


def test_extract_finds_bare_package_at_zip_root():
    entries = {
        "TEMPLATE.md": TEMPLATE_DOC,
        "template.json": TEMPLATE_PAYLOAD,
    }
    tmp_dir, found = store.extract_template_zip(make_zip(entries))
    try:
        # Falls back to the frontmatter name when there is no directory.
        assert [name for _, name in found] == ["sample-template"]
    finally:
        __import__("shutil").rmtree(tmp_dir, ignore_errors=True)


def test_extract_finds_multiple_packages():
    entries = {**valid_zip_entries("first"), **valid_zip_entries("second")}
    # Both packages carry the same frontmatter name; the loader reports it
    # twice and the service turns that into a conflict.
    tmp_dir, found = store.extract_template_zip(make_zip(entries))
    try:
        assert len(found) == 2
    finally:
        __import__("shutil").rmtree(tmp_dir, ignore_errors=True)


def test_pack_zip_is_rooted_at_template_name(package_dir: Path):
    blob = store.pack_template_to_zip(package_dir, "sample-template")
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = zf.namelist()
    assert "sample-template/TEMPLATE.md" in names
    assert "sample-template/batch/go.json" in names
    assert "sample-template/skills/sample-skill/SKILL.md" in names


def test_pack_then_extract_round_trip(package_dir: Path):
    blob = store.pack_template_to_zip(package_dir, "sample-template")
    tmp_dir, found = store.extract_template_zip(blob)
    try:
        extracted, name = found[0]
        assert name == "sample-template"
        store.validate_template_package(extracted, name)
        assert store.list_bundled_skills(extracted) == ["sample-skill"]
    finally:
        __import__("shutil").rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_reconcile_adds_manually_copied_package(
    workspace: Path,
    package_dir: Path,
):
    """A directory dropped in by hand must show up."""
    pool = store.ensure_template_pool_initialized(workspace)
    store.copy_template_dir(package_dir, pool / "sample-template")

    manifest = store.reconcile_template_manifest(workspace)
    entry = manifest["templates"]["sample-template"]
    assert entry["description"] == "示例模板"
    assert entry["category"] == "cron"
    assert entry["tags"] == ["personal", "reminder"]


def test_reconcile_drops_vanished_package(workspace: Path, package_dir: Path):
    pool = store.ensure_template_pool_initialized(workspace)
    store.copy_template_dir(package_dir, pool / "sample-template")
    store.reconcile_template_manifest(workspace)

    __import__("shutil").rmtree(pool / "sample-template")
    manifest = store.reconcile_template_manifest(workspace)
    assert "sample-template" not in manifest["templates"]


def test_record_origin_and_forget(workspace: Path):
    store.ensure_template_pool_initialized(workspace)
    store.record_template_origin("x", "upload", workspace)
    assert (
        store.read_template_manifest(workspace)["templates"]["x"][
            "installed_from"
        ]
        == "upload"
    )
    store.forget_template("x", workspace)
    assert "x" not in store.read_template_manifest(workspace)["templates"]


def test_suggest_conflict_name_skips_taken(workspace: Path):
    assert store.suggest_conflict_name("a", {"a-2", "a-3"}) == "a-4"
