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
from pathlib import Path, PurePosixPath
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
from ...exceptions import CronTemplateError, ToolBatchError
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

#: Schema of the file recording which builtins were materialised, and at
#: what version. Its own schema, not the workspace manifest's — see
#: ``get_builtin_template_record_path`` for why they must not be confused.
BUILTIN_RECORD_SCHEMA_VERSION = "cron-builtin-templates.v1"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_cron_template_dir(workspace_dir: Path | str) -> Path:
    """Return a workspace's cron-template directory.

    Per workspace, not global: a template is part of how *this* agent
    works, and two agents sharing an edit to one was surprising. Packaged
    builtins stay global and read-only — they ship with the application.
    """
    return Path(workspace_dir) / "cron_templates"


def get_cron_template_manifest_path(workspace_dir: Path | str) -> Path:
    """Return a workspace's template manifest path."""
    return get_cron_template_dir(workspace_dir) / "manifest.json"


def get_builtin_cron_template_dir() -> Path:
    """Return the packaged builtin template directory.

    The *shipping* location, inside the wheel. It is the copy source for
    ``ensure_builtin_templates_materialized`` and a resolution fallback for
    the window before that has run — not where a running install normally
    reads builtins from. See ``get_builtin_template_store_dir``.
    """
    return Path(__file__).parent / "builtin"


def get_builtin_template_store_dir() -> Path:
    """Return the user-level directory builtin templates are copied into.

    Builtins used to be read straight out of the wheel, which meant a
    template's ``{{template_dir}}`` resolved into ``site-packages`` — and
    that path gets baked into a cron job's prompt when the template is
    applied, so it broke on every reinstall or venv move. Materialising them
    here makes it stable.

    Global rather than per-workspace, unlike ``get_cron_template_dir``: a
    builtin is part of the application, not of how one agent works.
    """
    from ...constant import WORKING_DIR

    return Path(WORKING_DIR) / "cron_templates"


def get_builtin_template_record_path() -> Path:
    """Return the bookkeeping file for materialised builtins.

    Deliberately **not** ``manifest.json``. That name is already taken in
    this directory by a pre-per-workspace-templates manifest listing the
    user's own packages, and it carries the same ``schema_version`` the
    current per-workspace manifest does — so reconciling it here would
    resurrect those packages as globally visible user templates. This file
    is read and written; that one is left byte-identical.
    """
    return get_builtin_template_store_dir() / "builtin.json"


def default_builtin_template_record() -> dict[str, Any]:
    """The empty bookkeeping payload."""
    return {
        "schema_version": BUILTIN_RECORD_SCHEMA_VERSION,
        "templates": {},
    }


def read_builtin_template_record() -> dict[str, Any]:
    """Read the bookkeeping file, or its empty default."""
    return read_json(
        get_builtin_template_record_path(),
        default_builtin_template_record(),
    )


def materialized_builtin_names() -> set[str]:
    """Names in the store that this application put there.

    The authority for "which directories under the store are builtins".
    Anything else sitting there — the user's own pre-existing packages, a
    hand-placed copy — is invisible to the app and never written to, which
    is what makes sharing the directory safe.
    """
    templates = read_builtin_template_record().get("templates")
    if not isinstance(templates, dict):
        return set()
    return {str(name) for name in templates}


def ensure_template_pool_initialized(workspace_dir: Path | str) -> Path:
    """Create the workspace's template directory and manifest if missing."""
    pool = get_cron_template_dir(workspace_dir)
    pool.mkdir(parents=True, exist_ok=True)
    manifest_path = get_cron_template_manifest_path(workspace_dir)
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


def resolve_template_dir(
    name: str,
    workspace_dir: Path | str,
) -> tuple[Path, str] | None:
    """Locate a template by name.

    Returns ``(dir, source)`` where source is ``"user"`` — meaning this
    workspace's own — or ``"builtin"``. The workspace wins, so a local copy
    shadows a builtin.

    Note the consequence: forking a builtin now forks it into *one*
    workspace, and editing that fork changes nothing for any other agent.

    Builtins are looked for in the user-level store first and only then in
    the wheel. The store is where they normally live (see
    ``get_builtin_template_store_dir``); the packaged directory remains a
    fallback for the window before ``ensure_builtin_templates_materialized``
    has run — a bare test workspace, a headless invocation, a failed copy —
    so nothing depends on materialisation having happened.

    Only names the record claims are read from the store. That directory is
    shared with packages predating per-workspace templates, and those must
    stay as invisible as they are today.
    """
    normalized = normalize_template_name(name)
    user_dir = get_cron_template_dir(workspace_dir) / normalized
    if (user_dir / TEMPLATE_DOC_FILE).exists():
        return user_dir, "user"
    builtin_dir = resolve_builtin_template_dir(normalized)
    return (builtin_dir, "builtin") if builtin_dir else None


def resolve_builtin_template_dir(name: str) -> Path | None:
    """Locate a builtin, ignoring any workspace copy that shadows it.

    Store first, wheel second — the two halves of "where builtins live", so
    ``resolve_template_dir`` and the fork path share one definition of that
    order rather than probing roots independently.

    Separate from ``resolve_template_dir`` because forking needs the *source*
    even once a workspace copy exists: asking the shadow-aware resolver would
    answer ``"user"`` and make a second fork look like a missing builtin
    instead of the conflict it is.
    """
    normalized = normalize_template_name(name)
    if normalized in materialized_builtin_names():
        store_dir = get_builtin_template_store_dir() / normalized
        if (store_dir / TEMPLATE_DOC_FILE).exists():
            return store_dir
    packaged = get_builtin_cron_template_dir() / normalized
    return packaged if (packaged / TEMPLATE_DOC_FILE).exists() else None


def resolve_bundled_batch_script(
    template: str,
    relative: str,
    workspace_dir: Path | str,
) -> Path | None:
    """Resolve ``batch/<file>.json`` inside a template package.

    Used when copying a bundled script into a job. Fails closed on anything
    it is not certain about and never raises: the caller turns ``None`` into
    a 404, and ``normalize_template_name`` (reached via
    ``resolve_template_dir``) raises on empty / NUL-bearing / reserved
    names.

    Only the package's ``batch/`` directory is addressable, checked before
    any filesystem access so a traversal never even resolves a path.
    """
    if not template or not relative or "\\" in relative:
        return None

    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if len(candidate.parts) < 2 or candidate.parts[0] != TEMPLATE_BATCH_DIR:
        return None
    # `.lower()` to match `_load_batch_file`, which compares the same way.
    if candidate.suffix.lower() != ".json":
        return None

    try:
        found = resolve_template_dir(template, workspace_dir)
    except CronTemplateError:
        return None
    if found is None:
        return None
    package_dir = found[0]

    batch_root = (package_dir / TEMPLATE_BATCH_DIR).resolve()
    path = (package_dir / candidate).resolve()
    if not path.is_relative_to(batch_root):
        return None
    return path if path.is_file() else None


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

    # Same save-time gate a pool script gets. A packaged batch file can be
    # referenced by a cron preprocess and then runs unattended, so "parses
    # and is a list" is not enough: `validate_batch_content` is what bans a
    # nested `run_tool_batch`, caps the step count, and caps the file size
    # — that last one specifically because the scanner silently skips
    # anything over 5 MB, and "no findings" reads exactly like "clean".
    # Lazy import: see `scan_template_dir_or_raise`.
    from ..tool_batches.store import validate_batch_content

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
        try:
            validate_batch_content(batch)
        except ToolBatchError as exc:
            raise CronTemplateError(
                f"Batch file '{rel}': {exc.message or exc}",
            ) from exc

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

    ``batch/*.json`` needs the shell surrogate the pool already uses: no
    shipped signature rule lists ``json`` among its ``file_types``, so
    scanning a batch file in its stored form finds nothing regardless of
    what sits in ``arguments.command``. A cron preprocess can reference a
    packaged script directly, which executes it unattended with no model
    in the loop — so these files must be scanned as what they run.

    Note builtin packages are never scanned: no call site covers
    ``get_builtin_cron_template_dir()``. They ship with the application,
    which is the same trust level as the code doing the scanning.
    """
    # Lazy: `tool_batches.store` imports `run_tool_batch` at module scope,
    # which drags in psutil / html2text / browser control, and this module
    # is on the template *list* path.
    from ..tool_batches.store import staged_command_surrogate

    batch_paths = [package_dir / rel for rel in list_batch_files(package_dir)]
    with staged_command_surrogate(package_dir, batch_paths):
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


def _packaged_builtin_versions() -> dict[str, tuple[Path, str]]:
    """Every shipped builtin, as ``name -> (dir, version_text)``."""
    found: dict[str, tuple[Path, str]] = {}
    for package_dir in iter_template_dirs(get_builtin_cron_template_dir()):
        name = package_dir.name
        frontmatter_data, _ = parse_template_frontmatter(
            package_dir / TEMPLATE_DOC_FILE,
            name,
        )
        found[name] = (package_dir, frontmatter_data.version_text)
    return found


def ensure_builtin_templates_materialized() -> dict[str, list[str]]:
    """Copy the shipped builtins into the user-level store, idempotently.

    Returns what happened, keyed ``copied`` / ``updated`` / ``unchanged`` /
    ``removed`` / ``blocked``.

    Per package:

    * absent from the store — copy it in
    * present, recorded, same version — leave it
    * present, recorded, version differs — overwrite. Safe *because* builtins
      are read-only through the API: a user who wants to change one forks it
      into their workspace, so the store copy has no edits to lose. That is
      the whole reason this needs none of the conflict-confirmation machinery
      ``import_builtin_skills`` carries.
    * **present but not recorded — refuse.** The store directory is shared
      with packages that predate per-workspace templates, and a name
      collision must never let an upgrade delete somebody's own work.
    * recorded but no longer shipped — drop the copy and the record, so a
      retired builtin actually disappears.

    The copies deliberately do **not** go through the security scanner.
    Builtins have never been scanned (they ship with the application, so the
    scanner would be checking our own code); moving where they live must not
    quietly change that.

    Never raises: this runs on the startup path, and a workspace that cannot
    materialise templates should still start — resolution falls back to the
    packaged directory.
    """
    result: dict[str, list[str]] = {
        "copied": [],
        "updated": [],
        "unchanged": [],
        "removed": [],
        "blocked": [],
    }
    try:
        packaged = _packaged_builtin_versions()
    except OSError as exc:
        logger.warning("Cannot read packaged cron templates: %s", exc)
        return result

    store = get_builtin_template_store_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create cron template store %s: %s", store, exc)
        return result

    recorded = read_builtin_template_record().get("templates")
    recorded = dict(recorded) if isinstance(recorded, dict) else {}

    for name, (source_dir, version) in sorted(packaged.items()):
        target = store / name
        entry = recorded.get(name)
        known = isinstance(entry, dict)
        try:
            if target.exists() and not known:
                # Somebody else's directory. Leave it completely alone.
                logger.warning(
                    "Not materialising builtin cron template '%s': %s already "
                    "exists and was not put there by this application",
                    name,
                    target,
                )
                result["blocked"].append(name)
                continue
            if not target.exists():
                copy_template_dir(source_dir, target)
                result["copied"].append(name)
            elif str(entry.get("version", "")) == version:
                result["unchanged"].append(name)
                continue
            else:
                copy_template_dir(source_dir, target)
                result["updated"].append(name)
        except OSError as exc:
            logger.warning(
                "Cannot materialise builtin cron template '%s': %s",
                name,
                exc,
            )
            continue
        recorded[name] = {"version": version}

    for name in sorted(set(recorded) - set(packaged)):
        target = store / name
        try:
            if target.is_dir():
                shutil.rmtree(target)
        except OSError as exc:
            logger.warning("Cannot remove retired '%s': %s", name, exc)
            continue
        recorded.pop(name, None)
        result["removed"].append(name)

    if result["copied"] or result["updated"] or result["removed"]:

        def _record(payload: dict[str, Any]) -> dict[str, Any]:
            # In place: `mutate_json` persists the payload it handed us and
            # discards whatever the mutator returns, so building a new dict
            # here would silently write the untouched default.
            payload["schema_version"] = BUILTIN_RECORD_SCHEMA_VERSION
            payload["templates"] = recorded
            return payload

        try:
            mutate_json(
                get_builtin_template_record_path(),
                default_builtin_template_record(),
                _record,
            )
        except OSError as exc:
            logger.warning("Cannot record materialised builtins: %s", exc)
    logger.info(
        "cron builtin templates: copied=%s updated=%s unchanged=%s "
        "removed=%s blocked=%s",
        len(result["copied"]),
        len(result["updated"]),
        len(result["unchanged"]),
        len(result["removed"]),
        len(result["blocked"]),
    )
    return result


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def default_template_manifest() -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "version": 0,
        "templates": {},
    }


def read_template_manifest(workspace_dir: Path | str) -> dict[str, Any]:
    return read_json(
        get_cron_template_manifest_path(workspace_dir),
        default_template_manifest(),
    )


def reconcile_template_manifest(
    workspace_dir: Path | str,
) -> dict[str, Any]:
    """Sync the manifest with what is actually on disk.

    The filesystem is authoritative: directories present but unlisted get
    added, entries whose directory vanished get dropped. This makes manual
    ``cp -r`` into the directory a supported workflow, exactly as it is for
    the skill pool — and it means a workspace that starts with no manifest
    self-heals on first listing.
    """
    pool = ensure_template_pool_initialized(workspace_dir)

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
        get_cron_template_manifest_path(workspace_dir),
        default_template_manifest(),
        _update,
    )


def record_template_origin(
    name: str,
    origin: str,
    workspace_dir: Path | str,
) -> None:
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
        get_cron_template_manifest_path(workspace_dir),
        default_template_manifest(),
        _update,
    )


def forget_template(name: str, workspace_dir: Path | str) -> None:
    """Drop a manifest entry (called after the directory is removed)."""

    def _update(payload: dict[str, Any]) -> dict[str, Any]:
        templates = payload.setdefault("templates", {})
        templates.pop(name, None)
        return payload

    mutate_json(
        get_cron_template_manifest_path(workspace_dir),
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


def suggest_conflict_name(name: str, existing: set[str]) -> str:
    """Return ``<name>-2``, ``<name>-3``, … skipping taken names.

    ``existing`` is required. It used to default to scanning the one global
    pool, which has no meaning now that templates live per workspace — a
    caller that forgot the set would silently compute suggestions against
    the wrong directory.
    """
    taken = set(existing)
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"


__all__ = [
    "BUILTIN_RECORD_SCHEMA_VERSION",
    "default_builtin_template_record",
    "ensure_builtin_templates_materialized",
    "get_builtin_template_record_path",
    "get_builtin_template_store_dir",
    "materialized_builtin_names",
    "read_builtin_template_record",
    "resolve_builtin_template_dir",
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
    "resolve_bundled_batch_script",
    "resolve_template_dir",
    "safe_template_dir",
    "scan_template_dir_or_raise",
    "suggest_conflict_name",
    "validate_template_package",
    "write_template_package",
]
