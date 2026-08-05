# -*- coding: utf-8 -*-
"""Shared fixtures for tool batch pool tests."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

SAMPLE_ACTIONS = [
    {
        "tool_name": "execute_shell_command",
        "arguments": {"command": "echo ${args.greeting}"},
    },
]

SAMPLE_BATCH = {"actions": SAMPLE_ACTIONS, "description": "示例脚本"}


@pytest.fixture
def working_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point WORKING_DIR at a temp dir.

    ``store.get_tool_batch_dir`` delegates to the preprocess runner's
    ``get_batch_pool_dir``, which imports WORKING_DIR lazily inside the
    function, so patching the module attribute is enough — no need to
    reload anything.
    """
    from qwenpaw import constant

    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(constant, "WORKING_DIR", str(root))
    return root


def make_zip(entries: dict[str, str]) -> bytes:
    """Build an in-memory zip from ``{arcname: text}``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def batch_json(actions=None, description=None) -> str:
    """Serialize an object-form batch; ``actions`` defaults to SAMPLE."""
    content = {
        "actions": list(SAMPLE_ACTIONS) if actions is None else actions,
    }
    if description is not None:
        content["description"] = description
    return json.dumps(content, ensure_ascii=False)
