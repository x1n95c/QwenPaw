# -*- coding: utf-8 -*-
"""Lifecycle service for cron job template packages.

The shape follows ``SkillPoolService``: one class owning create / update /
delete / import-from-zip / export-to-zip over a shared pool directory,
with packaged builtins merged in read-only. Every write is staged in a
temp directory, validated and security-scanned there, and only then moved
into the pool — a rejected package never leaves a partial directory behind.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, TypeVar

from ...exceptions import CronTemplateConflictError, CronTemplateError
from ...utils.io_utils import staged_dir
from ..tool_batches.service import ToolBatchService
from .models import (
    CreateCronTemplateRequest,
    CronTemplateFrontmatter,
    CronTemplateInfo,
    CronTemplatePayload,
    InstallTemplateBatchesRequest,
    InstallTemplateSkillsRequest,
    UpdateCronTemplateRequest,
    TEMPLATE_BATCH_DIR,
    TEMPLATE_SKILLS_DIR,
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
    list_batch_files,
    normalize_template_name,
    pack_template_to_zip,
    read_template_package,
    reconcile_template_manifest,
    record_template_origin,
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
    """Manage folder-based cron job templates in the shared pool.

    The pool lives at ``WORKING_DIR/cron_templates`` and is intentionally
    agent-agnostic: a template is a recipe, so it is shared across agents
    the same way the skill pool is. Packaged builtins under
    ``app/cron_templates/builtin`` are listed alongside user templates but
    cannot be modified — importing one copies it into the pool first.
    """

    def __init__(self) -> None:
        ensure_template_pool_initialized()

    # ----- read -----

    def list_templates(
        self, include_builtin: bool = True
    ) -> list[CronTemplateInfo]:
        """List pool templates, then packaged builtins not shadowed by them."""
        reconcile_template_manifest()
        templates: list[CronTemplateInfo] = []
        seen: set[str] = set()
        for package_dir in iter_template_dirs(get_cron_template_dir()):
            info = self._safe_read(package_dir, package_dir.name, "user")
            if info is not None:
                templates.append(info)
                seen.add(info.name)
        if include_builtin:
            for package_dir in iter_template_dirs(
                get_builtin_cron_template_dir(),
            ):
                if package_dir.name in seen:
                    continue
                info = self._safe_read(
                    package_dir,
                    package_dir.name,
                    "builtin",
                )
                if info is not None:
                    templates.append(info)
        return templates

    def get_template(self, name: str) -> CronTemplateInfo:
        resolved = resolve_template_dir(name)
        if resolved is None:
            raise CronTemplateError(f"Template not found: {name}")
        package_dir, source = resolved
        return read_template_package(package_dir, name, source)

    def read_package_file(self, name: str, relative_path: str) -> str:
        """Read one text file out of a package (batch JSON preview, docs).

        Resolves inside the package and refuses anything that escapes it.
        """
        resolved = resolve_template_dir(name)
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
        from ...agents.utils.file_handling import (
            read_text_file_with_encoding_fallback,
        )

        return read_text_file_with_encoding_fallback(target)

    # ----- write -----

    def create_template(
        self,
        body: CreateCronTemplateRequest,
    ) -> CronTemplateInfo:
        """Create (or, with ``overwrite``, replace) a pool template."""
        name = normalize_template_name(body.name)
        pool = ensure_template_pool_initialized()
        target = safe_template_dir(pool, name)
        if target.exists() and not body.overwrite:
            raise CronTemplateConflictError(
                {
                    "reason": "conflict",
                    "name": name,
                    "message": f"Template '{name}' already exists",
                    "suggested_name": suggest_conflict_name(name),
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

        record_template_origin(name, "api")
        reconcile_template_manifest()
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
        pool = ensure_template_pool_initialized()
        target = safe_template_dir(pool, normalized)
        if not target.is_dir():
            builtin = get_builtin_cron_template_dir() / normalized
            if builtin.is_dir():
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

        reconcile_template_manifest()
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
        target = safe_template_dir(get_cron_template_dir(), normalized)
        if not target.exists():
            builtin = get_builtin_cron_template_dir() / normalized
            if builtin.exists():
                raise CronTemplateError(
                    f"Builtin template '{normalized}' cannot be deleted",
                )
            return False
        shutil.rmtree(target)
        forget_template(normalized)
        return True

    def fork_builtin(self, name: str) -> CronTemplateInfo:
        """Copy a packaged builtin into the pool so it becomes editable."""
        normalized = normalize_template_name(name)
        builtin_dir = get_builtin_cron_template_dir() / normalized
        if not builtin_dir.is_dir():
            raise CronTemplateError(f"Builtin template not found: {name}")
        pool = ensure_template_pool_initialized()
        target = safe_template_dir(pool, normalized)
        if target.exists():
            raise CronTemplateConflictError(
                build_import_conflict(normalized, self._pool_names()),
            )
        validate_template_package(builtin_dir, normalized)
        copy_template_dir(builtin_dir, target)
        record_template_origin(normalized, "builtin")
        reconcile_template_manifest()
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
        pool = ensure_template_pool_initialized()
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
                record_template_origin(name, "upload")
                imported.append(name)
            reconcile_template_manifest()
            return {
                "imported": imported,
                "count": len(imported),
                "conflicts": [],
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def export_to_zip(self, name: str) -> tuple[str, bytes]:
        """Package a template as a zip. Returns ``(filename, bytes)``."""
        resolved = resolve_template_dir(name)
        if resolved is None:
            raise CronTemplateError(f"Template not found: {name}")
        package_dir, _ = resolved
        normalized = normalize_template_name(name)
        validate_template_package(package_dir, normalized)
        return (
            f"{normalized}.zip",
            pack_template_to_zip(package_dir, normalized),
        )

    # ----- bundled skills -----

    def install_skills(
        self,
        name: str,
        body: InstallTemplateSkillsRequest,
        workspace_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Install skills shipped inside a template package.

        Reuses the skill system's own import path (``import_skill_dir``)
        rather than copying directories by hand, so bundled skills land in
        the pool / workspace with the same validation and manifest
        bookkeeping as any other skill.
        """
        from ...agents.skill_system.registry import (
            reconcile_pool_manifest,
            reconcile_workspace_manifest,
        )

        wanted = self._resolve_wanted_skills(name, body)
        resolved = resolve_template_dir(name)
        assert resolved is not None  # get_template already validated
        package_dir, _ = resolved

        # Narrow workspace_dir once so the post-install bookkeeping below
        # does not need a cast on every use.
        ws_dir = (
            self._require_workspace_dir(workspace_dir)
            if body.target == "workspace"
            else None
        )
        skill_root = self._skill_root_for(ws_dir)

        installed, skipped = self._copy_bundled_skills(
            package_dir=package_dir,
            skill_root=skill_root,
            wanted=wanted,
            overwrite=body.overwrite,
        )

        if ws_dir is not None:
            reconcile_workspace_manifest(ws_dir)
            if body.enable and installed:
                self._enable_workspace_skills(ws_dir, installed)
        else:
            reconcile_pool_manifest()

        return {
            "installed": installed,
            "skipped": skipped,
            "target": body.target,
        }

    # ----- bundled batches -----

    def install_batches(
        self,
        name: str,
        body: InstallTemplateBatchesRequest,
    ) -> dict[str, Any]:
        """Copy a template package's ``batch/*.json`` scripts into the pool.

        Mirrors ``install_skills`` but for batch scripts: the files are
        copied (base names kept) rather than referenced, so jobs only
        ever resolve scripts from the one pool directory. The copy goes
        through the tool-batches import pipeline, so the scripts get the
        same validation, security scan and batch conflict reporting as a
        zip upload. Returns ``{"imported": [...], "conflicts": [...]}``;
        when conflicts is non-empty nothing was written.
        """
        resolved = resolve_template_dir(name)
        if resolved is None:
            raise CronTemplateError(f"Template not found: {name}")
        package_dir, _ = resolved
        relative_paths = list_batch_files(package_dir)
        if not relative_paths:
            raise CronTemplateError(
                f"Template '{name}' does not bundle any batch scripts",
            )
        candidates = [
            (package_dir / relative, relative) for relative in relative_paths
        ]
        return ToolBatchService().import_batch_files(
            candidates,
            rename_map=body.rename_map,
            overwrite=body.overwrite,
        )

    # ----- helpers -----

    def _resolve_wanted_skills(
        self,
        name: str,
        body: InstallTemplateSkillsRequest,
    ) -> list[str]:
        """Validate the requested subset against what the package ships."""
        info = self.get_template(name)
        available = set(info.skills)
        if not available:
            raise CronTemplateError(
                f"Template '{name}' does not bundle any skills",
            )
        wanted = list(body.skills or sorted(available))
        unknown = [item for item in wanted if item not in available]
        if unknown:
            raise CronTemplateError(
                f"Template '{name}' does not bundle skill(s): "
                f"{', '.join(sorted(unknown))}",
            )
        return wanted

    @staticmethod
    def _require_workspace_dir(workspace_dir: Path | None) -> Path:
        if workspace_dir is None:
            raise CronTemplateError(
                "workspace_dir is required when target is 'workspace'",
            )
        return Path(workspace_dir)

    @staticmethod
    def _skill_root_for(ws_dir: Path | None) -> Path:
        """Pick the install root: the workspace's skills dir, or the pool."""
        from ...agents.skill_system import (
            get_skill_pool_dir,
            get_workspace_skills_dir,
        )

        root = (
            get_workspace_skills_dir(ws_dir)
            if ws_dir is not None
            else get_skill_pool_dir()
        )
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _copy_bundled_skills(
        *,
        package_dir: Path,
        skill_root: Path,
        wanted: list[str],
        overwrite: bool,
    ) -> tuple[list[str], list[str]]:
        """Copy skills in, returning ``(installed, skipped)``.

        Goes through ``import_skill_dir`` rather than copying by hand so
        bundled skills get the same frontmatter validation as any other
        skill import.
        """
        from ...agents.skill_system.store import import_skill_dir

        installed: list[str] = []
        skipped: list[str] = []
        for skill_name in wanted:
            target = skill_root / skill_name
            if target.exists():
                if not overwrite:
                    skipped.append(skill_name)
                    continue
                shutil.rmtree(target)
            src = package_dir / TEMPLATE_SKILLS_DIR / skill_name
            if import_skill_dir(src, skill_root, skill_name):
                installed.append(skill_name)
            else:
                skipped.append(skill_name)
        return installed, skipped

    @staticmethod
    def _enable_workspace_skills(
        workspace_dir: Path,
        skill_names: list[str],
    ) -> None:
        from ...agents.skill_system.store import (
            default_workspace_manifest,
            get_workspace_skill_manifest_path,
            mutate_json,
        )

        def _update(payload: dict[str, Any]) -> dict[str, Any]:
            skills = payload.setdefault("skills", {})
            for skill_name in skill_names:
                entry = skills.get(skill_name)
                entry = dict(entry) if isinstance(entry, dict) else {}
                entry["enabled"] = True
                entry.setdefault("channels", ["all"])
                skills[skill_name] = entry
            return payload

        mutate_json(
            get_workspace_skill_manifest_path(workspace_dir),
            default_workspace_manifest(),
            _update,
        )

    @staticmethod
    def _pool_names() -> set[str]:
        pool = get_cron_template_dir()
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
