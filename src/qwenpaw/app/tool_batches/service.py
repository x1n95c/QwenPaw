# -*- coding: utf-8 -*-
"""Lifecycle service for a directory of ``run_tool_batch`` scripts.

The shape follows ``CronTemplateService``: one class owning create /
update / delete / import-from-zip / export-to-zip over one
directory. The layout is flatter than a template package — plain
``<name>.json`` files with no manifest — but every write is still staged
in a temp directory, validated and security-scanned there, and only then
copied into place, so a rejected script never lands on disk.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...exceptions import ToolBatchConflictError, ToolBatchError
from ...utils.io_utils import staged_dir
from .models import (
    CreateToolBatchRequest,
    ToolBatchDetail,
    ToolBatchInfo,
    UpdateToolBatchRequest,
)
from .store import (
    apply_description,
    build_batch_info,
    build_import_conflict,
    discover_zip_batch_files,
    ensure_batch_root,
    extract_arg_names,
    extract_actions,
    extract_description,
    extract_upload_zip,
    iter_batch_files,
    name_from_file_name,
    normalize_batch_name,
    pack_batch_to_zip,
    read_batch_content,
    resolve_batch_file,
    safe_batch_path,
    scan_batch_dir_or_raise,
    suggest_conflict_name,
    validate_batch_content,
    write_batch_file,
)

logger = logging.getLogger(__name__)

#: Temp-root prefix for staged writes, so leftovers are identifiable.
_STAGE_PREFIX = "qwenpaw_tool_batch_"


class ToolBatchService:
    """Manage ``run_tool_batch`` scripts inside one directory.

    The directory is a cron job's own
    ``<workspace_dir>/cron_jobs/<job_id>/batch/`` — scripts belong to the
    job that runs them, so editing one cannot change what another job does
    and deleting a job cannot leave another dangling. Callers build the
    root with ``store.job_batch_dir``.

    The class is root-agnostic on purpose: every method works against
    whatever directory it was handed, which is what let the tests written
    against the old shared directory carry over unchanged.
    """

    def __init__(self, root: Path) -> None:
        self._root = ensure_batch_root(Path(root))

    @property
    def root(self) -> Path:
        """The directory this service manages."""
        return self._root

    # ----- read -----

    def list_batches(self) -> list[ToolBatchInfo]:
        """List the scripts in stable order, skipping unreadable ones."""
        batches: list[ToolBatchInfo] = []
        for path in iter_batch_files(self._root):
            info = self._safe_read(path)
            if info is not None:
                batches.append(info)
        return batches

    def get_batch(self, name: str) -> ToolBatchDetail:
        """Fetch one script with its parsed content."""
        normalized = normalize_batch_name(name)
        path = resolve_batch_file(self._root, normalized)
        if path is None:
            raise ToolBatchError(f"Batch not found: {name}")
        content = read_batch_content(path)
        info = build_batch_info(normalized, content, path)
        return ToolBatchDetail(**info.model_dump(), content=content)

    # ----- write -----

    def create_batch(self, body: CreateToolBatchRequest) -> ToolBatchInfo:
        """Create a script. Conflicts are reported, not overwritten."""
        name = normalize_batch_name(body.name)
        root = self._root
        target = safe_batch_path(root, name)
        if target.exists():
            raise ToolBatchConflictError(
                {
                    "message": f"Batch '{name}' already exists",
                    "name": name,
                    "suggested_name": suggest_conflict_name(
                        name,
                        self._existing_names(),
                    ),
                },
            )
        description = (
            body.description
            if body.description is not None
            else extract_description(body.content)
        )
        content = apply_description(body.content, description)
        validate_batch_content(content)
        self._staged_write(name, content, target)
        return self._read_info(target, name)

    def update_batch(
        self,
        name: str,
        body: UpdateToolBatchRequest,
    ) -> ToolBatchInfo:
        """Patch an existing script in place.

        ``None`` fields keep their current values, so a partial edit never
        blanks out the part the client did not send.
        """
        normalized = normalize_batch_name(name)
        root = self._root
        target = safe_batch_path(root, normalized)
        if not target.is_file():
            raise ToolBatchError(f"Batch not found: {name}")

        current = read_batch_content(target)
        new_content = body.content if body.content is not None else current
        description = (
            body.description
            if body.description is not None
            else extract_description(current)
        )
        content = apply_description(new_content, description)
        validate_batch_content(content)
        self._staged_write(normalized, content, target)
        return self._read_info(target, normalized)

    def delete_batch(self, name: str) -> bool:
        """Remove a script. Returns ``False`` when it is absent."""
        normalized = normalize_batch_name(name)
        target = safe_batch_path(self._root, normalized)
        if not target.is_file():
            return False
        target.unlink()
        return True

    # ----- import / export -----

    def export_to_zip(self, name: str) -> tuple[str, bytes]:
        """Package a script as a zip. Returns ``(filename, bytes)``.

        The stored bytes are zipped as-is, so the download can be fed
        straight back into ``POST /tool-batches/upload``.
        """
        normalized = normalize_batch_name(name)
        path = resolve_batch_file(self._root, normalized)
        if path is None:
            raise ToolBatchError(f"Batch not found: {name}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ToolBatchError(
                f"Cannot read batch '{name}': {exc}",
            ) from exc
        return f"{normalized}.zip", pack_batch_to_zip(normalized, data)

    def import_from_zip(
        self,
        data: bytes,
        *,
        select: list[str] | None = None,
        rename_map: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Import batch scripts from an uploaded zip.

        With several ``.json`` files and no ``select``, nothing is
        written: the caller gets the candidate list and picks. Otherwise
        the selected (or single) files go through the shared
        validate + scan + conflict pipeline.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="qwenpaw_tool_batch_upload_"))
        try:
            extract_upload_zip(data, tmp_dir)
            candidates = discover_zip_batch_files(tmp_dir)
            if not candidates:
                raise ToolBatchError(
                    "No .json batch files found in uploaded zip",
                )
            if select is not None:
                candidates = self._apply_select(candidates, select)
            if select is None and len(candidates) > 1:
                existing = self._existing_names()
                return {
                    "imported": [],
                    "candidates": [
                        self._describe_candidate(path, file_name, existing)
                        for path, file_name in candidates
                    ],
                }
            return self.import_batch_files(
                candidates,
                rename_map=rename_map,
                overwrite=overwrite,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def import_batch_files(
        self,
        candidates: list[tuple[Path, str]],
        *,
        rename_map: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Shared write path behind zip upload and bulk import.

        Every candidate is parsed, validated and security-scanned before
        anything is written. Conflicts are reported as a batch — all
        colliding names plus suggested renames — and nothing is written
        until every name is free (or ``overwrite``).

        ``candidates`` are ``(source_path, display_file_name)`` pairs.
        """
        root = self._root
        renames = rename_map or {}
        existing = self._existing_names()
        conflicts: list[dict[str, Any]] = []
        planned: list[tuple[str, str, Any]] = []
        seen: set[str] = set()
        for source_path, file_name in candidates:
            content = self._read_candidate(source_path, file_name)
            name = self._resolve_import_name(file_name, renames)
            if name in seen or (name in existing and not overwrite):
                conflicts.append(
                    build_import_conflict(name, file_name, existing | seen),
                )
                continue
            seen.add(name)
            planned.append((file_name, name, content))

        if conflicts:
            return {"imported": [], "conflicts": conflicts}
        if not planned:
            return {"imported": [], "conflicts": []}

        label = planned[0][1] if len(planned) == 1 else "tool-batch-import"
        with staged_dir("import", prefix=_STAGE_PREFIX) as stage:
            stage.mkdir(parents=True, exist_ok=True)
            for _file_name, name, content in planned:
                write_batch_file(stage / f"{name}.json", content)
            scan_batch_dir_or_raise(stage, label)
            for _file_name, name, content in planned:
                shutil.copyfile(
                    stage / f"{name}.json",
                    root / f"{name}.json",
                )
        return {
            "imported": [name for _file_name, name, _c in planned],
            "conflicts": [],
        }

    # ----- helpers -----

    @staticmethod
    def _apply_select(
        candidates: list[tuple[Path, str]],
        select: list[str],
    ) -> list[tuple[Path, str]]:
        """Narrow candidates to the selected file names (deduplicated)."""
        by_name = {file_name: path for path, file_name in candidates}
        wanted = list(dict.fromkeys(item.strip() for item in select))
        wanted = [item for item in wanted if item]
        unknown = [item for item in wanted if item not in by_name]
        if unknown:
            raise ToolBatchError(
                "Selected file(s) not found in zip: " + ", ".join(unknown),
            )
        return [(by_name[item], item) for item in wanted]

    def _describe_candidate(
        self,
        path: Path,
        file_name: str,
        existing: set[str],
    ) -> dict[str, Any]:
        """Describe one zip candidate for the two-phase selection flow.

        A bad file is reported as an unselectable candidate rather than
        failing the whole listing: one broken script in a zip of ten
        should not stop the user from picking the nine good ones. The
        hard failure still happens in ``import_batch_files`` if a broken
        file is actually selected.
        """
        name = name_from_file_name(file_name)
        try:
            content = self._read_candidate(path, file_name)
        except ToolBatchError as exc:
            return {
                "file_name": file_name,
                "name": name,
                "arg_names": [],
                "action_count": 0,
                "exists": name in existing,
                "valid": False,
                "error": exc.message,
            }
        actions = extract_actions(content)
        return {
            "file_name": file_name,
            "name": name,
            "arg_names": extract_arg_names(content),
            "action_count": len(actions) if actions is not None else 0,
            "exists": name in existing,
            "valid": True,
        }

    @staticmethod
    def _read_candidate(source_path: Path, file_name: str) -> Any:
        """Parse and validate one candidate, naming it in every error."""
        try:
            content = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolBatchError(
                f"'{file_name}' is not valid JSON: {exc}",
            ) from exc
        except RecursionError as exc:
            # Deeply nested input blows the recursion limit inside
            # json.loads; without this it escapes as a 500.
            raise ToolBatchError(
                f"'{file_name}' is nested too deeply to parse",
            ) from exc
        try:
            validate_batch_content(content)
        except ToolBatchError as exc:
            raise ToolBatchError(f"'{file_name}': {exc.message}") from exc
        return content

    @staticmethod
    def _resolve_import_name(
        file_name: str,
        renames: dict[str, str],
    ) -> str:
        """Apply ``rename_map`` (keyed by file name or default name)."""
        default_name = name_from_file_name(file_name)
        renamed = renames.get(file_name, renames.get(default_name))
        if renamed is None:
            return default_name
        return normalize_batch_name(renamed)

    @staticmethod
    def _staged_write(name: str, content: Any, target: Path) -> None:
        """Write, scan and land one batch file atomically.

        The file is built in a temp directory and scanned there, then
        copied in beside its target and renamed over it.
        ``os.replace`` is atomic within a filesystem, so a cron preprocess
        reading this script concurrently sees either the old content or
        the new one — never the truncated middle that a plain copy onto a
        live path exposes.
        """
        with staged_dir(name, prefix=_STAGE_PREFIX) as stage:
            stage.mkdir(parents=True, exist_ok=True)
            staged = stage / f"{name}.json"
            write_batch_file(staged, content)
            scan_batch_dir_or_raise(stage, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Land in the target directory first: os.replace cannot be
            # atomic across filesystems, and the temp dir may well be on
            # a different one.
            handoff = target.parent / f".{target.name}.tmp"
            try:
                shutil.copyfile(staged, handoff)
                os.replace(handoff, target)
            finally:
                handoff.unlink(missing_ok=True)

    def _read_info(self, path: Path, name: str) -> ToolBatchInfo:
        return build_batch_info(name, read_batch_content(path), path)

    def _existing_names(self) -> set[str]:
        return {path.stem for path in iter_batch_files(self._root)}

    @staticmethod
    def _safe_read(path: Path) -> ToolBatchInfo | None:
        """Read one file, logging and skipping the ones that fail.

        One malformed script must not blank out the whole list in the UI.
        """
        try:
            content = read_batch_content(path)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Skipping invalid batch script '%s': %s",
                path.name,
                exc,
            )
            return None
        return build_batch_info(path.stem, content, path)


__all__ = ["ToolBatchService"]
