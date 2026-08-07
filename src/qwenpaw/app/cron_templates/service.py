# -*- coding: utf-8 -*-
"""Lifecycle service for cron job template packages.

The shape follows ``SkillPoolService``: one class owning create / update /
delete / import-from-zip / export-to-zip over one directory,
with packaged builtins merged in read-only. Every write is staged in a
temp directory, validated and security-scanned there, and only then moved
into the pool — a rejected package never leaves a partial directory behind.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, TypeVar

from ...agents.utils.file_handling import (
    read_text_file_with_encoding_fallback,
)
from ...exceptions import CronTemplateConflictError, CronTemplateError
from ...utils.io_utils import staged_dir
from .models import (
    CreateCronTemplateRequest,
    CronTemplateFrontmatter,
    CronTemplateInfo,
    CronTemplatePayload,
    TemplateBatchScriptInfo,
    UpdateCronTemplateRequest,
    TEMPLATE_BATCH_DIR,
)
from .store import (
    build_import_conflict,
    copy_template_dir,
    ensure_template_pool_initialized,
    extract_template_zip,
    forget_template,
    get_builtin_cron_template_dir,
    get_cron_template_dir,
    iter_template_dirs,
    materialized_builtin_names,
    normalize_template_name,
    pack_template_to_zip,
    read_template_package,
    reconcile_template_manifest,
    record_template_origin,
    resolve_builtin_template_dir,
    resolve_template_dir,
    safe_template_dir,
    scan_template_dir_or_raise,
    suggest_conflict_name,
    validate_template_package,
    write_template_package,
)

logger = logging.getLogger(__name__)


_T = TypeVar("_T")

#: Temp-root prefix for staged package writes, so leftovers are
#: identifiable when debugging.
_STAGE_PREFIX = "qwenpaw_cron_template_"


def _pick(patch: _T | None, current: _T) -> _T:
    """Return ``patch`` unless it is ``None``, in which case keep ``current``.

    ``None`` is the only "not provided" signal — an empty string or empty
    list is a deliberate clear and must be honoured.
    """
    return current if patch is None else patch


def _drop_key_if_overridden(literal: str | None, current_key: str) -> str:
    """Keep an i18n key only while no literal replaces it.

    A package forked from a shipped one inherits that package's i18n keys.
    Once the user types their own title, the key has to go or clients would
    keep rendering the shipped translation over the edit.
    """
    if literal is None:
        return current_key
    return "" if literal.strip() else current_key


class CronTemplateService:
    """Manage folder-based cron job templates for one workspace.

    Templates live at ``<workspace_dir>/cron_templates`` — per agent, not
    shared. A template is part of how *one* agent works, and two agents
    silently sharing an edit to one was surprising. Packaged builtins under
    ``app/cron_templates/builtin`` stay global and read-only; they are
    listed alongside a workspace's own and forking one copies it in.
    """

    def __init__(self, workspace_dir: Path | str) -> None:
        self._ws = Path(workspace_dir)
        ensure_template_pool_initialized(self._ws)

    # ----- read -----

    def list_templates(
        self, include_builtin: bool = True
    ) -> list[CronTemplateInfo]:
        """List this workspace's templates, then builtins they don't shadow."""
        reconcile_template_manifest(self._ws)
        templates: list[CronTemplateInfo] = []
        seen: set[str] = set()
        for package_dir in iter_template_dirs(get_cron_template_dir(self._ws)):
            info = self._safe_read(package_dir, package_dir.name, "user")
            if info is not None:
                templates.append(info)
                seen.add(info.name)
        if include_builtin:
            # Materialised builtins plus any not yet copied out of the wheel.
            # Not a directory walk of the store: that directory is shared
            # with packages predating per-workspace templates, and only the
            # record says which entries are ours to show.
            builtin_names = sorted(
                materialized_builtin_names()
                | {
                    path.name
                    for path in iter_template_dirs(
                        get_builtin_cron_template_dir(),
                    )
                },
            )
            for name in builtin_names:
                if name in seen:
                    continue
                # Through the resolver, so store-before-wheel precedence has
                # exactly one definition.
                resolved = resolve_template_dir(name, self._ws)
                if resolved is None or resolved[1] != "builtin":
                    continue
                info = self._safe_read(resolved[0], name, "builtin")
                if info is not None:
                    templates.append(info)
                    seen.add(name)
        return templates

    def get_template(self, name: str) -> CronTemplateInfo:
        resolved = resolve_template_dir(name, self._ws)
        if resolved is None:
            raise CronTemplateError(f"Template not found: {name}")
        package_dir, source = resolved
        return read_template_package(package_dir, name, source)

    def read_package_file(self, name: str, relative_path: str) -> str:
        """Read one text file out of a package (batch JSON preview, docs).

        Resolves inside the package and refuses anything that escapes it.
        """
        resolved = resolve_template_dir(name, self._ws)
        if resolved is None:
            raise CronTemplateError(f"Template not found: {name}")
        package_dir, _ = resolved
        normalized = (relative_path or "").replace("\\", "/").strip()
        if not normalized or normalized.startswith("/"):
            raise CronTemplateError(f"Invalid file path: {relative_path}")
        target = (package_dir / normalized).resolve()
        if not target.is_relative_to(package_dir.resolve()):
            raise CronTemplateError(
                f"Unsafe file path outside template: {relative_path}",
            )
        if not target.is_file():
            raise CronTemplateError(f"File not found: {relative_path}")
        return read_text_file_with_encoding_fallback(target)

    def list_batch_scripts(
        self, include_builtin: bool = True
    ) -> list[TemplateBatchScriptInfo]:
        """List every ``batch/*.json`` bundled across template packages.

        Feeds the job form's script picker: a preprocess step can address
        one of these directly as ``<template>/batch/<file>.json`` instead
        of being limited to the flat pool, which is what makes a script
        stay visibly attached to the task it belongs to.

        Built on ``list_templates`` rather than walking the two pools again
        so the user-shadows-builtin precedence has exactly one definition —
        the same one ``resolve_template_dir`` (and therefore the runtime
        resolver) applies. Description / arg names / preview come from
        ``build_batch_info``, the pool's own describer, so a packaged
        script and a pool script render identically.
        """
        from ..tool_batches.store import build_batch_info

        scripts: list[TemplateBatchScriptInfo] = []
        for info in self.list_templates(include_builtin):
            package_dir = Path(info.package_dir)
            for relative in info.batch_files:
                path = package_dir / relative
                try:
                    content = json.loads(
                        read_text_file_with_encoding_fallback(path),
                    )
                except (OSError, ValueError, RecursionError) as exc:
                    # One broken file must not blank out the whole picker.
                    logger.warning(
                        "Skipping unreadable template batch '%s/%s': %s",
                        info.name,
                        relative,
                        exc,
                    )
                    continue
                described = build_batch_info(path.stem, content, path)
                scripts.append(
                    TemplateBatchScriptInfo(
                        ref=f"{info.name}/{relative}",
                        template=info.name,
                        template_title=info.title,
                        template_title_key=info.title_key,
                        template_source=info.source,
                        file_path=relative,
                        file_name=path.name,
                        description=described.description,
                        arg_names=described.arg_names,
                        action_count=described.action_count,
                        preview_actions=described.preview_actions,
                        updated_at=described.updated_at,
                    ),
                )
        return scripts

    # ----- write -----

    def create_template(
        self,
        body: CreateCronTemplateRequest,
    ) -> CronTemplateInfo:
        """Create (or, with ``overwrite``, replace) a pool template."""
        name = normalize_template_name(body.name)
        pool = ensure_template_pool_initialized(self._ws)
        target = safe_template_dir(pool, name)
        if target.exists() and not body.overwrite:
            raise CronTemplateConflictError(
                {
                    "reason": "conflict",
                    "name": name,
                    "message": f"Template '{name}' already exists",
                    "suggested_name": suggest_conflict_name(
                        name,
                        self._pool_names(),
                    ),
                },
            )

        payload = CronTemplatePayload(
            form=body.form or {},
            job=body.job,
            batch_entry=body.batch_entry,
        )
        fm = CronTemplateFrontmatter(
            name=name,
            description=body.description,
            title=body.title or name,
            category=body.category,
            frequency=body.frequency,
            emoji=body.emoji,
            tags=body.tags,
            version_text=body.version_text,
        )
        doc_body = body.body or _default_doc_body(fm, body)

        with staged_dir(name, prefix=_STAGE_PREFIX) as stage:
            write_template_package(
                stage,
                frontmatter_data=fm,
                body=doc_body,
                payload=payload,
                batch_files=body.batch_files,
                skills=body.skills,
                extra_files=body.extra_files,
            )
            validate_template_package(stage, name)
            scan_template_dir_or_raise(stage, name)
            copy_template_dir(stage, target)

        record_template_origin(name, "api", self._ws)
        reconcile_template_manifest(self._ws)
        return read_template_package(target, name, "user")

    def update_template(
        self,
        name: str,
        body: UpdateCronTemplateRequest,
    ) -> CronTemplateInfo:
        """Patch an existing pool template in place.

        Stages from a *copy of the current package* rather than rebuilding
        from the request, so files the caller never mentioned — bundled
        skills, assets, batch scripts — survive the edit. Builtins are
        read-only; fork one first.
        """
        normalized = normalize_template_name(name)
        pool = ensure_template_pool_initialized(self._ws)
        target = safe_template_dir(pool, normalized)
        if not target.is_dir():
            if resolve_builtin_template_dir(normalized) is not None:
                raise CronTemplateError(
                    f"Builtin template '{normalized}' is read-only; "
                    f"fork it into the pool before editing",
                )
            raise CronTemplateError(f"Template not found: {name}")

        current = read_template_package(target, normalized, "user")
        merged_fm = CronTemplateFrontmatter(
            name=normalized,
            description=_pick(body.description, current.description),
            title=_pick(body.title, current.title),
            category=_pick(body.category, current.category),
            frequency=_pick(body.frequency, current.frequency),
            emoji=_pick(body.emoji, current.emoji),
            tags=_pick(body.tags, current.tags),
            version_text=_pick(body.version_text, current.version_text),
            # Supplying literal text drops the i18n key it replaces.
            # Otherwise a package forked from a shipped one would keep
            # resolving the shipped translation and the user's edit would
            # appear to do nothing.
            title_key=_drop_key_if_overridden(body.title, current.title_key),
            description_key=_drop_key_if_overridden(
                body.description,
                current.description_key,
            ),
            frequency_key=_drop_key_if_overridden(
                body.frequency,
                current.frequency_key,
            ),
        )
        merged_payload = CronTemplatePayload(
            form=_pick(body.form, current.payload.form),
            job=_pick(body.job, current.payload.job),
            # "" clears the entry; None leaves it as it was.
            batch_entry=(
                current.payload.batch_entry
                if body.batch_entry is None
                else (body.batch_entry.strip() or None)
            ),
        )
        doc_body = _pick(body.body, current.content)

        with staged_dir(normalized, prefix=_STAGE_PREFIX) as stage:
            copy_template_dir(target, stage)
            for filename in body.remove_batch_files or []:
                self._remove_batch_file(stage, filename)
            write_template_package(
                stage,
                frontmatter_data=merged_fm,
                body=doc_body,
                payload=merged_payload,
                batch_files=body.batch_files,
            )
            validate_template_package(stage, normalized)
            scan_template_dir_or_raise(stage, normalized)
            copy_template_dir(stage, target)

        reconcile_template_manifest(self._ws)
        return read_template_package(target, normalized, "user")

    @staticmethod
    def _remove_batch_file(stage: Path, filename: str) -> None:
        """Delete one file under ``batch/``, refusing path escapes."""
        candidate = str(filename or "").strip()
        if not candidate or "/" in candidate or "\\" in candidate:
            raise CronTemplateError(
                f"Invalid batch file name: {filename}",
            )
        if not candidate.endswith(".json"):
            candidate = f"{candidate}.json"
        (stage / TEMPLATE_BATCH_DIR / candidate).unlink(missing_ok=True)

    def delete_template(self, name: str) -> bool:
        """Remove a pool template. Builtins are never deleted."""
        normalized = normalize_template_name(name)
        target = safe_template_dir(get_cron_template_dir(self._ws), normalized)
        if not target.exists():
            if resolve_builtin_template_dir(normalized) is not None:
                raise CronTemplateError(
                    f"Builtin template '{normalized}' cannot be deleted",
                )
            return False
        shutil.rmtree(target)
        forget_template(normalized, self._ws)
        return True

    def fork_builtin(self, name: str) -> CronTemplateInfo:
        """Copy a builtin into the workspace so it becomes editable.

        The source is whichever copy resolution picked — the materialised one
        under the user-level store, or the wheel's if that has not been
        written yet. Either is byte-identical, so the fork does not care.
        """
        normalized = normalize_template_name(name)
        builtin_dir = resolve_builtin_template_dir(normalized)
        if builtin_dir is None or not builtin_dir.is_dir():
            raise CronTemplateError(f"Builtin template not found: {name}")
        pool = ensure_template_pool_initialized(self._ws)
        target = safe_template_dir(pool, normalized)
        if target.exists():
            raise CronTemplateConflictError(
                build_import_conflict(normalized, self._pool_names()),
            )
        validate_template_package(builtin_dir, normalized)
        copy_template_dir(builtin_dir, target)
        record_template_origin(normalized, "builtin", self._ws)
        reconcile_template_manifest(self._ws)
        return read_template_package(target, normalized, "user")

    # ----- import / export -----

    def import_from_zip(
        self,
        data: bytes,
        *,
        target_name: str | None = None,
        rename_map: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Import one or more template packages from an uploaded zip.

        Conflicts are reported as a batch instead of partially importing:
        the caller gets every colliding name plus a suggested rename, and
        nothing is written until all names are free (or ``overwrite``).
        """
        pool = ensure_template_pool_initialized(self._ws)
        tmp_dir, found = extract_template_zip(data)
        renames = rename_map or {}
        try:
            requested = str(target_name or "").strip()
            if requested:
                if len(found) != 1:
                    raise CronTemplateError(
                        "target_name is only supported for single-template "
                        "zip imports",
                    )
                found = [(found[0][0], normalize_template_name(requested))]
            found = [
                (path, normalize_template_name(renames.get(name, name)))
                for path, name in found
            ]

            existing = self._pool_names()
            conflicts: list[dict[str, Any]] = []
            planned: list[tuple[Path, str]] = []
            seen: set[str] = set()
            for package_dir, name in found:
                validate_template_package(package_dir, name)
                scan_template_dir_or_raise(package_dir, name)
                if name in seen:
                    conflicts.append(build_import_conflict(name, existing))
                    continue
                seen.add(name)
                if name in existing and not overwrite:
                    conflicts.append(build_import_conflict(name, existing))
                    continue
                planned.append((package_dir, name))

            if conflicts:
                return {
                    "imported": [],
                    "count": 0,
                    "conflicts": conflicts,
                }

            imported: list[str] = []
            for package_dir, name in planned:
                copy_template_dir(package_dir, safe_template_dir(pool, name))
                record_template_origin(name, "upload", self._ws)
                imported.append(name)
            reconcile_template_manifest(self._ws)
            return {
                "imported": imported,
                "count": len(imported),
                "conflicts": [],
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def export_to_zip(self, name: str) -> tuple[str, bytes]:
        """Package a template as a zip. Returns ``(filename, bytes)``."""
        resolved = resolve_template_dir(name, self._ws)
        if resolved is None:
            raise CronTemplateError(f"Template not found: {name}")
        package_dir, _ = resolved
        normalized = normalize_template_name(name)
        validate_template_package(package_dir, normalized)
        return (
            f"{normalized}.zip",
            pack_template_to_zip(package_dir, normalized),
        )

    def _pool_names(self) -> set[str]:
        """Template names already taken in this workspace."""
        pool = get_cron_template_dir(self._ws)
        if not pool.exists():
            return set()
        return {path.name for path in iter_template_dirs(pool)}

    @staticmethod
    def _safe_read(
        package_dir: Path,
        name: str,
        source: str,
    ) -> CronTemplateInfo | None:
        """Read a package, logging and skipping the ones that fail.

        One malformed package in the pool must not blank out the whole
        list in the UI.
        """
        try:
            return read_template_package(package_dir, name, source)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Skipping invalid cron template '%s' at %s: %s",
                name,
                package_dir,
                exc,
            )
            return None


def _default_doc_body(
    fm: CronTemplateFrontmatter,
    body: CreateCronTemplateRequest,
) -> str:
    """Generate a readable ``TEMPLATE.md`` body when none was supplied."""
    lines = [f"# {fm.title or fm.name}", ""]
    if fm.description:
        lines += [fm.description, ""]
    lines += ["## 包含内容", "", "- `template.json` — 定时任务规格"]
    for filename in sorted(body.batch_files):
        name = filename if filename.endswith(".json") else f"{filename}.json"
        lines.append(f"- `batch/{name}` — run_tool_batch 批处理脚本")
    for skill_name in sorted(body.skills):
        lines.append(f"- `skills/{skill_name}/` — 随模板附带的 skill")
    if fm.frequency:
        lines += ["", "## 执行频率", "", fm.frequency]
    return "\n".join(lines)


__all__ = ["CronTemplateService"]
