# -*- coding: utf-8 -*-
"""Tests for the shared archive helpers in ``utils.io_utils``.

``extract_zip_safely`` guards every "user uploads an archive" path in the
product (skill packages, cron template packages). It is tested here rather
than only through its callers so the defenses are pinned down once, for
everyone that depends on them.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from qwenpaw.utils.io_utils import extract_zip_safely, staged_dir


class Boom(Exception):
    """Stand-in for a caller's domain error type."""


def zip_bytes(
    entries: dict[str, str],
    *,
    symlink: str | None = None,
) -> bytes:
    buffer = io.BytesIO()
    # Deflated, like a real upload — so the bomb test exercises the gap
    # between wire size and uncompressed size.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            # High bits of external_attr carry the Unix mode.
            info.external_attr = (0o120777 | 0o120000) << 16
            zf.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def extract(data: bytes, dest: Path, **kwargs) -> None:
    kwargs.setdefault("max_bytes", 10 * 1024 * 1024)
    extract_zip_safely(data, dest, error_factory=Boom, **kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_extracts_nested_tree(tmp_path: Path):
    extract(zip_bytes({"pkg/a.txt": "A", "pkg/sub/b.txt": "B"}), tmp_path)
    assert (tmp_path / "pkg" / "a.txt").read_text() == "A"
    assert (tmp_path / "pkg" / "sub" / "b.txt").read_text() == "B"


def test_no_entry_cap_by_default(tmp_path: Path):
    """Skill imports rely on there being no member-count limit."""
    entries = {f"pkg/f{i}.txt": "x" for i in range(500)}
    extract(zip_bytes(entries), tmp_path)
    assert len(list((tmp_path / "pkg").iterdir())) == 500


# ---------------------------------------------------------------------------
# Rejections — nothing may be written before these fire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["../escaped.txt", "../../escaped.txt", "pkg/../../escaped.txt"],
)
def test_rejects_path_traversal(tmp_path: Path, bad_name: str):
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(Boom, match="Unsafe path in zip"):
        extract(zip_bytes({"pkg/ok.txt": "x", bad_name: "evil"}), dest)
    assert not (tmp_path / "escaped.txt").exists()
    # Nothing is written when a member is rejected.
    assert list(dest.iterdir()) == []


def test_rejects_symlink(tmp_path: Path):
    with pytest.raises(Boom, match="Symlink not allowed"):
        extract(zip_bytes({"pkg/a.txt": "x"}, symlink="pkg/link"), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_rejects_oversized_payload(tmp_path: Path):
    with pytest.raises(Boom, match="exceeds"):
        extract(
            zip_bytes({"pkg/big.txt": "x" * 5000}), tmp_path, max_bytes=100
        )
    assert list(tmp_path.iterdir()) == []


def test_rejects_too_many_entries(tmp_path: Path):
    entries = {f"pkg/f{i}.txt": "x" for i in range(10)}
    with pytest.raises(Boom, match="too many entries"):
        extract(zip_bytes(entries), tmp_path, max_entries=5)
    assert list(tmp_path.iterdir()) == []


def test_entry_cap_counts_members_not_bytes(tmp_path: Path):
    """A tiny-but-numerous archive is the bomb this cap exists for."""
    entries = {f"pkg/f{i}.txt": "" for i in range(6)}
    with pytest.raises(Boom, match="too many entries"):
        extract(zip_bytes(entries), tmp_path, max_entries=5, max_bytes=10**9)


def test_error_factory_controls_exception_type(tmp_path: Path):
    class Other(Exception):
        pass

    with pytest.raises(Other):
        extract_zip_safely(
            zip_bytes({"../x": "e"}),
            tmp_path,
            max_bytes=1000,
            error_factory=Other,
        )


def test_size_limit_message_reports_megabytes(tmp_path: Path):
    # Must actually exceed the limit: the check is on uncompressed size,
    # so the payload has to be bigger than max_bytes even though it zips
    # down to almost nothing.
    oversized = "x" * (3 * 1024 * 1024)
    with pytest.raises(Boom, match="2MB limit"):
        extract(
            zip_bytes({"pkg/big.txt": oversized}),
            tmp_path,
            max_bytes=2 * 1024 * 1024,
        )


def test_size_limit_uses_uncompressed_size(tmp_path: Path):
    """A highly-compressible payload must not slip past on wire size."""
    payload = "a" * (2 * 1024 * 1024)
    data = zip_bytes({"pkg/bomb.txt": payload})
    # The archive itself is tiny; the check must look at file_size.
    assert len(data) < 100 * 1024
    with pytest.raises(Boom, match="exceeds"):
        extract(data, tmp_path, max_bytes=1024)


# ---------------------------------------------------------------------------
# staged_dir
# ---------------------------------------------------------------------------


def test_staged_dir_yields_named_child_and_cleans_up():
    with staged_dir("my-pkg", prefix="test_stage_") as stage:
        assert stage.name == "my-pkg"
        stage.mkdir(parents=True)
        (stage / "f.txt").write_text("x")
        root = stage.parent
        assert root.name.startswith("test_stage_my-pkg")
        assert root.exists()
    assert not root.exists()


def test_staged_dir_cleans_up_on_exception():
    captured: list[Path] = []
    with pytest.raises(RuntimeError):
        with staged_dir("boom", prefix="test_stage_") as stage:
            stage.mkdir(parents=True)
            captured.append(stage.parent)
            raise RuntimeError("failure mid-write")
    assert not captured[0].exists()
