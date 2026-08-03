# -*- coding: utf-8 -*-
"""Filesystem layer for folder-based cron job template packages.

Deliberately mirrors ``agents/skill_system/store.py``: a pool directory of
self-describing package folders, a lock-protected JSON manifest, safe zip
extraction, and a security scan before anything lands on disk. Where the
primitives are already generic (cross-process locked JSON, artifact-aware
directory copy, the skill scanner) we reuse them instead of re-deriving
them, so template packages and skill packages cannot drift apart.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import frontmatter
import yaml

from ...agents.skill_system.store import (
    copy_skill_dir,
    is_ignored_skill_entry,
    mutate_json,
    read_json,
)
from ...agents.utils.file_handling import read_text_file_with_encoding_fallback
from ...exceptions import CronTemplateError
from ...security.skill_scanner import scan_skill_directory
from ...utils.io_utils import extract_zip_safely
from .models import (
    MANIFEST_SCHEMA_VERSION,
    METADATA_NAMESPACES,
    PAYLOAD_SCHEMA_VERSION,
    TEMPLATE_BATCH_DIR,
    TEMPLATE_DOC_FILE,
    TEMPLATE_PAYLOAD_FILE,
    TEMPLATE_SKILLS_DIR,
    CronTemplateFrontmatter,
    CronTemplateInfo,
    CronTemplatePayload,
)

logger = logging.getLogger(__name__)

#: Uncompressed ceiling for an uploaded template zip. Lower than the skill
#: limit (200MB) because a template is a recipe, not a payload of assets.
MAX_TEMPLATE_ZIP_BYTES = 100 * 1024 * 1024
#: Guard against zip bombs with many tiny entries.
MAX_TEMPLATE_ZIP_ENTRIES = 4096


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_cron_template_dir() -> Path:
    """Return the shared cron-template pool directory."""
    from ...constant import WORKING_DIR

    return Path(WORKING_DIR) / "cron_templates"


def get_cron_template_manifest_path() -> Path:
    """Return the pool manifest path."""
    return get_cron_template_dir() / "manifest.json"


def get_builtin_cron_template_dir() -> Path:
    """Return the packaged builtin template directory."""
    return Path(__file__).parent / "builtin"


def ensure_template_pool_initialized() -> Path:
    """Create the pool directory and manifest if missing."""
    pool = get_cron_template_dir()
    pool.mkdir(parents=True, exist_ok=True)
    manifest_path = get_cron_template_manifest_path()
    if not manifest_path.exists():
        mutate_json(
            manifest_path,
            default_template_manifest(),
            lambda payload: payload,
        )
    return pool


def normalize_template_name(name: str) -> str:
    """Validate a template directory name.

    Same layered defense as ``normalize_skill_dir_name``: reject empty,
    NUL, dot-relative and separator-bearing names before they ever reach
    a path join.
    """
    normalized = str(name or "").strip()
    if not normalized:
        raise CronTemplateError("Template name cannot be empty")
    if "\x00" in normalized:
        raise CronTemplateError("Template name cannot contain NUL bytes")
    if normalized in {".", ".."}:
        raise CronTemplateError(f"Invalid template name: {normalized}")
    if "/" in normalized or "\\" in normalized:
        raise CronTemplateError(
            "Template name cannot contain path separators",
        )
    if is_ignored_skill_entry(normalized):
        raise CronTemplateError(f"Reserved template name: {normalized}")
    if normalized == "manifest.json":
        raise CronTemplateError("Reserved template name: manifest.json")
    return normalized


def safe_template_dir(base_dir: Path, name: str) -> Path:
    """Resolve ``base_dir/name`` and refuse anything escaping ``base_dir``."""
    normalized = normalize_template_name(name)
    candidate = (base_dir / normalized).resolve()
    base_resolved = base_dir.resolve()
    if not candidate.is_relative_to(base_resolved):
        raise CronTemplateError(f"Unsafe template path outside root: {name}")
    return candidate


def resolve_template_dir(name: str) -> tuple[Path, str] | None:
    """Locate a template by name.

    Returns ``(dir, source)`` where source is ``"user"`` or ``"builtin"``.
    The user pool wins so a user copy can shadow a packaged builtin.
    """
    normalized = normalize_template_name(name)
    user_dir = get_cron_template_dir() / normalized
    if (user_dir / TEMPLATE_DOC_FILE).exists():
        return user_dir, "user"
    builtin_dir = get_builtin_cron_template_dir() / normalized
    if (builtin_dir / TEMPLATE_DOC_FILE).exists():
        return builtin_dir, "builtin"
    return None


def iter_template_dirs(root: Path) -> Iterator[Path]:
    """Yield package directories under ``root`` in stable order."""
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if is_ignored_skill_entry(path.name):
            continue
        if path.is_dir() and (path / TEMPLATE_DOC_FILE).exists():
            yield path


# ---------------------------------------------------------------------------
# TEMPLATE.md frontmatter
# ---------------------------------------------------------------------------


def _metadata_block(post: Any) -> dict[str, Any]:
    """Return the vendor metadata block from frontmatter, if any."""
    metadata = post.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    for namespace in METADATA_NAMESPACES:
        block = metadata.get(namespace)
        if isinstance(block, dict):
            return block
    # Allow flat metadata (``metadata: {category: cron}``) as a convenience.
    return metadata


def _normalize_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.replace(",", " ").split()]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item).strip() for item in raw]
    else:
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for tag in candidates:
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def parse_template_frontmatter(
    doc_path: Path,
    fallback_name: str = "",
) -> tuple[CronTemplateFrontmatter, str]:
    """Parse ``TEMPLATE.md`` into (metadata, markdown body).

    Never raises on malformed frontmatter: a template that fails to parse
    still shows up in the list with its directory name, matching how
    ``read_frontmatter_safe_from_path`` degrades for skills.
    """
    name = fallback_name or doc_path.parent.name
    try:
        post = frontmatter.loads(
            read_text_file_with_encoding_fallback(doc_path)
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "Failed to parse TEMPLATE.md frontmatter for '%s' at %s: %s",
            name,
            doc_path,
            exc,
        )
        return CronTemplateFrontmatter(name=name), ""

    meta = _metadata_block(post)
    category = str(meta.get("category") or "cron").strip().lower()
    if category not in ("cron", "once"):
        category = "cron"
    version_text = ""
    for value in (post.get("version"), meta.get("version")):
        if value not in (None, ""):
            version_text = str(value)
            break
    return (
        CronTemplateFrontmatter(
            name=str(post.get("name") or name),
            description=str(post.get("description") or ""),
            title=str(meta.get("title") or post.get("title") or ""),
            category=category,  # type: ignore[arg-type]
            frequency=str(meta.get("frequency") or ""),
            emoji=str(meta.get("emoji") or ""),
            tags=_normalize_tags(meta.get("tags")),
            version_text=version_text,
            title_key=str(meta.get("title_key") or ""),
            description_key=str(meta.get("description_key") or ""),
            frequency_key=str(meta.get("frequency_key") or ""),
        ),
        str(post.content or ""),
    )


def render_template_doc(fm: CronTemplateFrontmatter, body: str) -> str:
    """Serialize frontmatter + body back into ``TEMPLATE.md`` text."""
    metadata: dict[str, Any] = {"category": fm.category}
    if fm.title:
        metadata["title"] = fm.title
    if fm.frequency:
        metadata["frequency"] = fm.frequency
    if fm.emoji:
        metadata["emoji"] = fm.emoji
    if fm.tags:
        metadata["tags"] = list(fm.tags)
    if fm.version_text:
        metadata["version"] = fm.version_text
    for key, value in (
        ("title_key", fm.title_key),
        ("description_key", fm.description_key),
        ("frequency_key", fm.frequency_key),
    ):
        if value:
            metadata[key] = value

    header = {
        "name": fm.name,
        "description": fm.description,
        "metadata": {"qwenpaw": metadata},
    }
    dumped = yaml.safe_dump(
        header,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n\n{(body or '').strip()}\n"


# ---------------------------------------------------------------------------
# Package read / validate / write
# ---------------------------------------------------------------------------


def _relative_files(package_dir: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(is_ignored_skill_entry(part) for part in path.parts):
            continue
        files.append(path.relative_to(package_dir).as_posix())
    return files


def read_template_payload(package_dir: Path) -> CronTemplatePayload:
    """Read and validate ``template.json``."""
    payload_path = package_dir / TEMPLATE_PAYLOAD_FILE
    if not payload_path.is_file():
        raise CronTemplateError(
            f"Template package is missing {TEMPLATE_PAYLOAD_FILE}",
        )
    try:
        raw = json.loads(read_text_file_with_encoding_fallback(payload_path))
    except (OSError, json.JSONDecodeError) as exc:
        raise CronTemplateError(
            f"{TEMPLATE_PAYLOAD_FILE} is not valid JSON: {exc}",
        ) from exc
    if not isinstance(raw, dict):
        raise CronTemplateError(
            f"{TEMPLATE_PAYLOAD_FILE} must contain a JSON object",
        )
    try:
        return CronTemplatePayload.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise CronTemplateError(
            f"{TEMPLATE_PAYLOAD_FILE} is invalid: {exc}",
        ) from exc


def list_batch_files(package_dir: Path) -> list[str]:
    """Return package-relative paths of ``run_tool_batch`` JSON files."""
    batch_dir = package_dir / TEMPLATE_BATCH_DIR
    if not batch_dir.is_dir():
        return []
    return [
        path.relative_to(package_dir).as_posix()
        for path in sorted(batch_dir.rglob("*.json"))
        if path.is_file()
        and not any(is_ignored_skill_entry(part) for part in path.parts)
    ]


def list_bundled_skills(package_dir: Path) -> list[str]:
    """Return names of skills shipped under ``skills/``."""
    skills_dir = package_dir / TEMPLATE_SKILLS_DIR
    if not skills_dir.is_dir():
        return []
    return [
        path.name
        for path in sorted(skills_dir.iterdir())
        if path.is_dir()
        and not is_ignored_skill_entry(path.name)
        and (path / "SKILL.md").exists()
    ]


def validate_template_package(package_dir: Path, name: str = "") -> None:
    """Structural validation before a package is accepted.

    Checks the two required files, that every ``batch/*.json`` parses, and
    that each bundled skill actually carries a ``SKILL.md``. Content-level
    threat detection is the scanner's job (``scan_template_dir_or_raise``).
    """
    label = name or package_dir.name
    if not (package_dir / TEMPLATE_DOC_FILE).is_file():
        raise CronTemplateError(
            f"Template '{label}' is missing {TEMPLATE_DOC_FILE}",
        )
    payload = read_template_payload(package_dir)
    if not payload.form and not payload.job:
        raise CronTemplateError(
            f"Template '{label}' must define either 'form' or 'job' "
            f"in {TEMPLATE_PAYLOAD_FILE}",
        )

    for rel in list_batch_files(package_dir):
        batch_path = package_dir / rel
        try:
            batch = json.loads(
                read_text_file_with_encoding_fallback(batch_path),
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise CronTemplateError(
                f"Batch file '{rel}' is not valid JSON: {exc}",
            ) from exc
        actions = batch.get("actions") if isinstance(batch, dict) else batch
        if not isinstance(actions, list):
            raise CronTemplateError(
                f"Batch file '{rel}' must be an actions array or an object "
                f"with an 'actions' array",
            )

    entry = (payload.batch_entry or "").strip()
    if entry and not (package_dir / entry).is_file():
        raise CronTemplateError(
            f"batch_entry '{entry}' does not exist in template '{label}'",
        )

    skills_dir = package_dir / TEMPLATE_SKILLS_DIR
    if skills_dir.is_dir():
        for path in sorted(skills_dir.iterdir()):
            if not path.is_dir() or is_ignored_skill_entry(path.name):
                continue
            if not (path / "SKILL.md").exists():
                raise CronTemplateError(
                    f"Bundled skill '{path.name}' is missing SKILL.md",
                )


def scan_template_dir_or_raise(package_dir: Path, name: str) -> None:
    """Run the skill security scanner over a template package.

    Template packages can carry scripts and whole skills, so they get the
    same treatment as an uploaded skill rather than being trusted.
    """
    scan_skill_directory(package_dir, skill_name=name)


def read_template_package(
    package_dir: Path,
    name: str = "",
    source: str = "user",
) -> CronTemplateInfo:
    """Load a package directory into a ``CronTemplateInfo``."""
    normalized = name or package_dir.name
    fm, body = parse_template_frontmatter(
        package_dir / TEMPLATE_DOC_FILE,
        normalized,
    )
    payload = read_template_payload(package_dir)
    try:
        mtime = datetime.fromtimestamp(
            (package_dir / TEMPLATE_DOC_FILE).stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except OSError:
        mtime = ""
    resolved_dir = package_dir.resolve()
    entry = (payload.batch_entry or "").strip()
    entry_path = str(resolved_dir / entry) if entry else ""
    return CronTemplateInfo(
        name=normalized,
        title=fm.title or fm.name or normalized,
        description=fm.description,
        category=fm.category,
        frequency=fm.frequency,
        emoji=fm.emoji,
        tags=fm.tags,
        version_text=fm.version_text,
        source=source,  # type: ignore[arg-type]
        title_key=fm.title_key,
        description_key=fm.description_key,
        frequency_key=fm.frequency_key,
        content=body,
        payload=payload,
        batch_files=list_batch_files(package_dir),
        skills=list_bundled_skills(package_dir),
        files=_relative_files(package_dir),
        package_dir=str(resolved_dir),
        batch_entry_path=entry_path,
        updated_at=mtime,
    )


def _write_file_tree(base_dir: Path, tree: dict[str, Any]) -> None:
    """Materialize a nested ``{name: str | dict}`` tree under ``base_dir``.

    Mirrors the skill system's ``_create_files_from_tree`` but re-validates
    each segment so a crafted key cannot escape ``base_dir``.
    """
    for raw_name, value in (tree or {}).items():
        segment = str(raw_name or "").strip()
        if not segment or segment in {".", ".."}:
            raise CronTemplateError(
                f"Invalid file name in package: {raw_name}"
            )
        target = (base_dir / segment).resolve()
        if not target.is_relative_to(base_dir.resolve()):
            raise CronTemplateError(
                f"Unsafe file path in package: {raw_name}",
            )
        if isinstance(value, dict):
            target.mkdir(parents=True, exist_ok=True)
            _write_file_tree(target, value)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(value), encoding="utf-8")


def write_template_package(
    package_dir: Path,
    *,
    frontmatter_data: CronTemplateFrontmatter,
    body: str,
    payload: CronTemplatePayload,
    batch_files: dict[str, str] | None = None,
    skills: dict[str, str] | None = None,
    extra_files: dict[str, Any] | None = None,
) -> None:
    """Write a complete package into ``package_dir``."""
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / TEMPLATE_DOC_FILE).write_text(
        render_template_doc(frontmatter_data, body),
        encoding="utf-8",
    )
    payload_json = json.dumps(
        payload.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    (package_dir / TEMPLATE_PAYLOAD_FILE).write_text(
        payload_json + "\n",
        encoding="utf-8",
    )
    if batch_files:
        _write_file_tree(
            package_dir / TEMPLATE_BATCH_DIR,
            {
                (k if k.endswith(".json") else f"{k}.json"): v
                for k, v in batch_files.items()
            },
        )
    if skills:
        _write_file_tree(
            package_dir / TEMPLATE_SKILLS_DIR,
            {name: {"SKILL.md": content} for name, content in skills.items()},
        )
    if extra_files:
        _write_file_tree(package_dir, extra_files)


def copy_template_dir(source: Path, target: Path) -> None:
    """Replace ``target`` with a copy of ``source`` (artifacts filtered)."""
    copy_skill_dir(source, target)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def default_template_manifest() -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "version": 0,
        "templates": {},
    }


def read_template_manifest() -> dict[str, Any]:
    return read_json(
        get_cron_template_manifest_path(),
        default_template_manifest(),
    )


def reconcile_template_manifest() -> dict[str, Any]:
    """Sync the manifest with what is actually on disk.

    The filesystem is authoritative: directories present but unlisted get
    added, entries whose directory vanished get dropped. This makes manual
    ``cp -r`` into the pool a supported workflow, exactly as it is for the
    skill pool.
    """
    pool = ensure_template_pool_initialized()

    def _update(payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
        templates = payload.setdefault("templates", {})
        if not isinstance(templates, dict):
            templates = {}
            payload["templates"] = templates

        discovered: set[str] = set()
        for package_dir in iter_template_dirs(pool):
            name = package_dir.name
            discovered.add(name)
            fm, _ = parse_template_frontmatter(
                package_dir / TEMPLATE_DOC_FILE,
                name,
            )
            entry = templates.get(name)
            entry = dict(entry) if isinstance(entry, dict) else {}
            entry.update(
                {
                    "name": name,
                    "description": fm.description,
                    "category": fm.category,
                    "tags": fm.tags,
                    "version": fm.version_text,
                    "source": entry.get("source") or "user",
                },
            )
            templates[name] = entry

        for name in list(templates):
            if name not in discovered:
                templates.pop(name, None)
        return payload

    return mutate_json(
        get_cron_template_manifest_path(),
        default_template_manifest(),
        _update,
    )


def record_template_origin(name: str, origin: str) -> None:
    """Note where a template came from (``upload`` / ``api`` / ``builtin``)."""

    def _update(payload: dict[str, Any]) -> dict[str, Any]:
        templates = payload.setdefault("templates", {})
        entry = templates.get(name)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["name"] = name
        entry["installed_from"] = origin
        templates[name] = entry
        return payload

    mutate_json(
        get_cron_template_manifest_path(),
        default_template_manifest(),
        _update,
    )


def forget_template(name: str) -> None:
    """Drop a manifest entry (called after the directory is removed)."""

    def _update(payload: dict[str, Any]) -> dict[str, Any]:
        templates = payload.setdefault("templates", {})
        templates.pop(name, None)
        return payload

    mutate_json(
        get_cron_template_manifest_path(),
        default_template_manifest(),
        _update,
    )


# ---------------------------------------------------------------------------
# Zip pack / unpack
# ---------------------------------------------------------------------------


def pack_template_to_zip(package_dir: Path, name: str) -> bytes:
    """Zip a package, rooted at a single ``<name>/`` directory.

    Rooting the archive at the template name means the exported file is
    also a valid import, so export → import is a lossless round trip.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _relative_files(package_dir):
            zf.write(package_dir / rel, arcname=f"{name}/{rel}")
    return buffer.getvalue()


def _extract_and_validate_zip(data: bytes, tmp_dir: Path) -> None:
    """Extract an uploaded template zip with the shared archive defenses.

    Same helper the skill importer uses, so traversal / symlink / bomb
    protection can never drift between the two upload paths. Templates
    take a lower size ceiling and add an entry-count cap: a template is a
    recipe, not a payload of assets.
    """
    extract_zip_safely(
        data,
        tmp_dir,
        max_bytes=MAX_TEMPLATE_ZIP_BYTES,
        max_entries=MAX_TEMPLATE_ZIP_ENTRIES,
        error_factory=CronTemplateError,
    )


def extract_template_zip(data: bytes) -> tuple[Path, list[tuple[Path, str]]]:
    """Extract an upload and locate the template packages inside.

    Accepts three layouts, same tolerance as the skill importer:

    - ``TEMPLATE.md`` at the zip root (a bare package)
    - a single top-level directory holding ``TEMPLATE.md``
    - several top-level directories, each holding ``TEMPLATE.md``

    Returns ``(tmp_dir, [(package_dir, name)])``; the caller owns
    ``tmp_dir`` and must remove it.
    """
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise CronTemplateError("Uploaded file is not a valid zip archive")
    tmp_dir = Path(tempfile.mkdtemp(prefix="qwenpaw_cron_template_upload_"))
    try:
        _extract_and_validate_zip(data, tmp_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    real_entries = [
        path
        for path in tmp_dir.iterdir()
        if not is_ignored_skill_entry(path.name)
    ]
    extract_root = (
        real_entries[0]
        if len(real_entries) == 1 and real_entries[0].is_dir()
        else tmp_dir
    )
    if (extract_root / TEMPLATE_DOC_FILE).exists():
        found = [(extract_root, _resolve_package_name(extract_root))]
    else:
        found = [
            (path, _resolve_package_name(path))
            for path in iter_template_dirs(extract_root)
        ]
    if not found:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise CronTemplateError(
            f"No valid template packages found in uploaded zip "
            f"(each package needs a {TEMPLATE_DOC_FILE})",
        )
    return tmp_dir, found


def _resolve_package_name(package_dir: Path) -> str:
    """Prefer the frontmatter ``name``, falling back to the directory name."""
    fm, _ = parse_template_frontmatter(
        package_dir / TEMPLATE_DOC_FILE,
        package_dir.name,
    )
    candidate = (fm.name or package_dir.name).strip()
    try:
        return normalize_template_name(candidate)
    except CronTemplateError:
        return normalize_template_name(package_dir.name)


def build_import_conflict(name: str, existing: set[str]) -> dict[str, Any]:
    """Describe a name collision plus a free alternative to rename to."""
    return {
        "reason": "conflict",
        "name": name,
        "message": f"Template '{name}' already exists",
        "suggested_name": suggest_conflict_name(name, existing),
    }


def suggest_conflict_name(name: str, existing: set[str] | None = None) -> str:
    """Return ``<name>-2``, ``<name>-3``, … skipping taken names."""
    taken = set(existing or ())
    if not taken:
        pool = get_cron_template_dir()
        if pool.exists():
            taken = {
                path.name
                for path in pool.iterdir()
                if path.is_dir() and not is_ignored_skill_entry(path.name)
            }
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"


__all__ = [
    "MAX_TEMPLATE_ZIP_BYTES",
    "MAX_TEMPLATE_ZIP_ENTRIES",
    "PAYLOAD_SCHEMA_VERSION",
    "build_import_conflict",
    "copy_template_dir",
    "default_template_manifest",
    "ensure_template_pool_initialized",
    "extract_template_zip",
    "forget_template",
    "get_builtin_cron_template_dir",
    "get_cron_template_dir",
    "get_cron_template_manifest_path",
    "iter_template_dirs",
    "list_batch_files",
    "list_bundled_skills",
    "normalize_template_name",
    "pack_template_to_zip",
    "parse_template_frontmatter",
    "read_template_manifest",
    "read_template_package",
    "read_template_payload",
    "reconcile_template_manifest",
    "record_template_origin",
    "render_template_doc",
    "resolve_template_dir",
    "safe_template_dir",
    "scan_template_dir_or_raise",
    "suggest_conflict_name",
    "validate_template_package",
    "write_template_package",
]
