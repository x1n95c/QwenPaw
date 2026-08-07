# -*- coding: utf-8 -*-
"""Shared fixtures for cron template package tests."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

TEMPLATE_DOC = """---
name: sample-template
description: 示例模板
metadata:
  qwenpaw:
    title: 示例
    category: cron
    frequency: 每天 09:00
    emoji: "📊"
    tags: [personal, reminder]
    version: '1.2'
---

# 示例

正文说明。
"""

TEMPLATE_PAYLOAD = json.dumps(
    {
        "schema_version": "cron-template.v1",
        "form": {"scheduleType": "cron", "cronCustom": "0 9 * * *"},
        "job": {
            "name": "示例",
            "schedule": {"type": "cron", "cron": "0 9 * * *"},
        },
    },
    ensure_ascii=False,
)

BATCH_JSON = json.dumps(
    {"actions": [{"tool_name": "execute_shell_command", "arguments": {}}]},
)

SKILL_DOC = """---
name: sample-skill
description: 示例 skill
---

# 示例 skill
"""


@pytest.fixture
def working_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point WORKING_DIR at a temp dir.

    ``store.get_cron_template_dir`` imports WORKING_DIR lazily inside the
    function, so patching the module attribute is enough — no need to
    reload the store module.
    """
    import qwenpaw.constant as constant

    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(constant, "WORKING_DIR", str(root))
    return root


@pytest.fixture
def workspace(working_dir: Path) -> Path:
    """An agent workspace. Templates live at ``<workspace>/cron_templates``.

    Per workspace rather than global: `WORKING_DIR` is still patched because
    packaged builtins and the legacy batch pool resolve against it.
    """
    root = working_dir / "workspaces" / "default"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def package_dir(tmp_path: Path) -> Path:
    """A complete on-disk package: docs, payload, one batch, one skill."""
    root = tmp_path / "pkg" / "sample-template"
    (root / "batch").mkdir(parents=True)
    (root / "skills" / "sample-skill").mkdir(parents=True)
    (root / "TEMPLATE.md").write_text(TEMPLATE_DOC, encoding="utf-8")
    (root / "template.json").write_text(TEMPLATE_PAYLOAD, encoding="utf-8")
    (root / "batch" / "go.json").write_text(BATCH_JSON, encoding="utf-8")
    (root / "skills" / "sample-skill" / "SKILL.md").write_text(
        SKILL_DOC,
        encoding="utf-8",
    )
    return root


def make_zip(entries: dict[str, str]) -> bytes:
    """Build an in-memory zip from ``{arcname: text}``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def valid_zip_entries(prefix: str = "sample-template") -> dict[str, str]:
    """Entries for a minimal valid single-package zip."""
    return {
        f"{prefix}/TEMPLATE.md": TEMPLATE_DOC,
        f"{prefix}/template.json": TEMPLATE_PAYLOAD,
        f"{prefix}/batch/go.json": BATCH_JSON,
    }
