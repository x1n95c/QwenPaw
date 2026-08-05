# -*- coding: utf-8 -*-
"""Filesystem layer for the shared ``run_tool_batch`` script pool.

The pool is a flat directory of ``<name>.json`` files — no manifest, no
frontmatter, the filesystem is the source of truth. It is the same
directory cron preprocesses resolve pool scripts from
(``app/crons/preprocess.py``), so anything stored here is immediately
runnable by name, and the file-name contract stays in one place.

Storage rules mirror ``run_tool_batch._load_batch_file``: a bare JSON
array of actions or an object with an ``actions`` array. Validation is
stricter than the loader — scripts are checked before they land on disk,
because a preprocess runs them unattended, with no model in the loop to
second-guess the call.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ...agents.skill_system.store import is_ignored_skill_entry
from ...agents.tools.run_tool_batch import (
    MAX_BATCH_STEPS,
    _ARG_REF_INLINE_PATTERN,
    _build_label_map,
)
from ...exceptions import ToolBatchError
from ...security.skill_scanner import scan_skill_directory
from ...utils.io_utils import extract_zip_safely
from .models import ToolBatchInfo

logger = logging.getLogger(__name__)

#: Uncompressed ceiling for an uploaded batch zip. Same as cron templates:
#: a batch script is a recipe, not a payload of assets.
MAX_BATCH_ZIP_BYTES = 100 * 1024 * 1024
#: Guard against zip bombs with many tiny entries.
MAX_BATCH_ZIP_ENTRIES = 4096

#: DOS device names, which Windows resolves before the filesystem does.
#: ``is_ignored_skill_entry`` does not cover them.
_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{index}" for index in range(1, 10)]
    + [f"LPT{index}" for index in range(1, 10)],
)

#: Ceiling on one batch script, comfortably under the security scanner's
#: own 5 MB ``max_file_size_bytes`` (``security/skill_scanner/
#: scan_policy.py``) — the scanner silently skips anything larger, so a
#: bigger script would be a way past the scan rather than a bigger recipe.
MAX_BATCH_FILE_BYTES = 1024 * 1024

#: How many leading actions ``ToolBatchInfo.preview_actions`` carries.
#: The console shows exactly these and then says how many steps remain,
#: so raising this here widens the preview without a frontend change —
#: but ``BATCH_PREVIEW_STEP_LIMIT`` in ``batchValidation.ts`` documents
#: the same number for the tests that pin the rendered row count.
PREVIEW_ACTION_LIMIT = 2


# ---------------------------------------------------------------------------
# Paths and names
# ---------------------------------------------------------------------------


def get_tool_batch_dir() -> Path:
    """Return the shared batch pool directory.

    Delegates to the preprocess runner so the pool location has exactly
    one definition (``WORKING_DIR/tool_batches``).
    """
    from ..crons.preprocess import get_batch_pool_dir

    return get_batch_pool_dir()


def ensure_batch_pool_initialized() -> Path:
    """Create the pool directory if missing."""
    pool = get_tool_batch_dir()
    pool.mkdir(parents=True, exist_ok=True)
    return pool


def normalize_batch_name(name: str) -> str:
    """Validate a pool script name.

    Same layered defense as ``normalize_template_name``: reject empty,
    NUL, dot-relative and separator-bearing names before they ever reach
    a path join. A trailing ``.json`` is tolerated and stripped, matching
    how ``resolve_batch_script`` accepts names with or without it.
    """
    normalized = str(name or "").strip()
    if normalized.endswith(".json"):
        normalized = normalized[: -len(".json")].strip()
    if normalized.endswith(".json"):
        # Exactly one suffix is stripped, but `safe_batch_path` normalizes
        # again on an already-normalized name — so `a.json.json` would be
        # stripped twice and the name reported by create/update would not
        # be the name `list` returns. Refusing keeps normalization
        # idempotent, which is what makes double-application harmless.
        raise ToolBatchError(
            f"Batch name cannot end with '.json': {normalized}",
        )
    if not normalized:
        raise ToolBatchError("Batch name cannot be empty")
    if "\x00" in normalized:
        raise ToolBatchError("Batch name cannot contain NUL bytes")
    if normalized in {".", ".."}:
        raise ToolBatchError(f"Invalid batch name: {normalized}")
    if "/" in normalized or "\\" in normalized:
        raise ToolBatchError(
            "Batch name cannot contain path separators",
        )
    if is_ignored_skill_entry(normalized):
        raise ToolBatchError(f"Reserved batch name: {normalized}")
    if normalized.upper() in _WINDOWS_RESERVED_NAMES:
        # `CON.json` opens the console device on Windows rather than
        # creating a file, so the write appears to succeed and the script
        # is simply not there afterwards.
        raise ToolBatchError(f"Reserved batch name: {normalized}")
    if normalized[-1] in {".", " "}:
        # Windows strips these from a path, so `a.` and `a` would collide
        # — a rename that silently overwrites another script.
        raise ToolBatchError(
            f"Batch name cannot end with '.' or a space: {normalized}",
        )
    return normalized


def safe_batch_path(base_dir: Path, name: str) -> Path:
    """Resolve ``base_dir/<name>.json`` and refuse escapes."""
    normalized = normalize_batch_name(name)
    candidate = (base_dir / f"{normalized}.json").resolve()
    base_resolved = base_dir.resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ToolBatchError(f"Unsafe batch path outside root: {name}")
    return candidate


def iter_batch_files(root: Path) -> Iterator[Path]:
    """Yield ``*.json`` files directly under ``root`` in stable order."""
    if not root.exists():
        return
    for path in sorted(root.glob("*.json")):
        if not path.is_file() or is_ignored_skill_entry(path.name):
            continue
        yield path


def resolve_batch_file(name: str) -> Path | None:
    """Locate a pool script by name, or ``None`` if absent."""
    pool = get_tool_batch_dir()
    try:
        path = safe_batch_path(pool, name)
    except ToolBatchError:
        return None
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# Content: validate, describe, read, write
# ---------------------------------------------------------------------------


def extract_actions(content: Any) -> list[Any] | None:
    """Return the actions array from batch content, or ``None``.

    Mirrors ``run_tool_batch._load_batch_file``: a bare array, or an
    object carrying an ``actions`` array.
    """
    if isinstance(content, list):
        return content
    if isinstance(content, dict):
        actions = content.get("actions")
        if isinstance(actions, list):
            return actions
    return None


def _reject_oversized(content: Any) -> None:
    """Refuse a batch larger than the security scanner will look at.

    The scanner silently skips any file over its own 5 MB
    ``max_file_size_bytes``. Without a cap here, padding a script past
    that limit is a way to walk a shell payload straight past
    ``scan_batch_dir_or_raise`` — a skipped file yields no finding, which
    is indistinguishable from a clean one. A batch is a recipe, so a cap
    well under the scanner's is no real constraint.
    """
    try:
        size = len(
            json.dumps(content, ensure_ascii=False).encode("utf-8"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ToolBatchError("Batch content is not serializable") from exc
    if size > MAX_BATCH_FILE_BYTES:
        raise ToolBatchError(
            f"Batch content is too large ({size} bytes). "
            f"Maximum allowed is {MAX_BATCH_FILE_BYTES} bytes.",
        )


def validate_batch_content(content: Any) -> list[dict[str, Any]]:
    """Validate batch content before it is written to the pool.

    Accepts the two shapes ``run_tool_batch._load_batch_file`` accepts
    and rejects everything the executor would trip over at run time —
    reporting it at save time instead, when the error is actionable.
    Label/goto checks reuse ``_build_label_map`` itself so the pool's
    rules cannot drift from the executor's.
    """
    actions = extract_actions(content)
    if actions is None:
        raise ToolBatchError(
            "Batch content must be an array of actions or an object "
            "with an 'actions' array",
        )
    _reject_oversized(content)
    if not actions:
        raise ToolBatchError("Batch must contain at least one action")
    if len(actions) > MAX_BATCH_STEPS:
        raise ToolBatchError(
            f"Too many steps ({len(actions)}). "
            f"Maximum allowed is {MAX_BATCH_STEPS}.",
        )

    for index, action in enumerate(actions):
        _validate_action(index, action)

    try:
        label_map = _build_label_map(actions)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ToolBatchError(f"Invalid label step: {exc}") from exc

    for index, action in enumerate(actions):
        tool_name = _action_tool_name(action)
        if tool_name != "goto":
            continue
        arguments = action.get("arguments") or action.get("args") or {}
        label = str(arguments.get("label") or "").strip()
        if not label:
            raise ToolBatchError(
                f"goto action at index {index} requires arguments.label",
            )
        if label not in label_map:
            raise ToolBatchError(
                f"goto action at index {index} targets unknown label: "
                f"{label}",
            )
    return actions  # type: ignore[return-value]


def _action_tool_name(action: Any) -> str:
    """Resolve the tool name the way the executor does."""
    if not isinstance(action, dict):
        return ""
    return str(action.get("tool_name") or action.get("tool") or "").strip()


def _validate_action(index: int, action: Any) -> None:
    if not isinstance(action, dict):
        raise ToolBatchError(f"Action at index {index} must be an object")
    tool_name = _action_tool_name(action)
    if not tool_name:
        raise ToolBatchError(
            f"Action at index {index} is missing tool_name",
        )
    if tool_name == "run_tool_batch":
        raise ToolBatchError(
            f"Action at index {index} calls run_tool_batch; nested "
            f"batches are not allowed",
        )
    for key in ("arguments", "args"):
        if key in action and not isinstance(action.get(key), dict):
            raise ToolBatchError(
                f"Action at index {index} has a non-object '{key}'",
            )


def extract_arg_names(content: Any) -> list[str]:
    """Collect ``${args.*}`` placeholder names referenced in the content.

    Uses the executor's own inline pattern
    (``run_tool_batch._ARG_REF_INLINE_PATTERN``) so the UI and the runner
    cannot disagree about what counts as a placeholder. Returns a sorted,
    de-duplicated list.
    """
    found: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, str):
            for match in _ARG_REF_INLINE_PATTERN.finditer(value):
                found.add(match.group(1))
        elif isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(content)
    return sorted(found)


def extract_description(content: Any) -> str:
    """Read the optional top-level ``description`` of object-form content.

    Array-form content has nowhere to keep one, so it is always "".
    """
    if isinstance(content, dict):
        return str(content.get("description") or "")
    return ""


def apply_description(content: Any, description: str) -> Any:
    """Return content carrying ``description``.

    Object-form content keeps it in the top-level ``description`` key
    (``_load_batch_file`` ignores extra keys, so this is safe). Array-form
    content is wrapped into an object when a description is wanted — both
    shapes load identically.
    """
    if isinstance(content, dict):
        updated = dict(content)
        if description:
            updated["description"] = description
        else:
            updated.pop("description", None)
        return updated
    if description:
        return {"actions": content, "description": description}
    return content


def read_batch_content(path: Path) -> Any:
    """Parse one pool file, raising :class:`ToolBatchError` on failure.

    ``RecursionError`` is caught alongside the JSON errors: deeply nested
    input (``"[" * 20000``) exceeds the interpreter's recursion limit
    inside ``json.loads``, and that is neither an ``OSError`` nor a
    ``JSONDecodeError`` — uncaught it surfaces as a 500 for what is
    plainly a bad request.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolBatchError(
            f"Cannot read batch '{path.name}': {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ToolBatchError(
            f"Batch '{path.name}' is not valid JSON: {exc}",
        ) from exc
    except RecursionError as exc:
        raise ToolBatchError(
            f"Batch '{path.name}' is nested too deeply to parse",
        ) from exc


def write_batch_file(path: Path, content: Any) -> None:
    """Serialize batch content to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_batch_info(name: str, content: Any, path: Path) -> ToolBatchInfo:
    """Build a ``ToolBatchInfo`` for a file and its parsed content."""
    try:
        mtime = datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except OSError:
        mtime = ""
    actions = extract_actions(content) or []
    return ToolBatchInfo(
        name=name,
        description=extract_description(content),
        arg_names=extract_arg_names(content),
        action_count=len(actions),
        preview_actions=actions[:PREVIEW_ACTION_LIMIT],
        updated_at=mtime,
    )


# ---------------------------------------------------------------------------
# Security scan
# ---------------------------------------------------------------------------


#: Argument keys whose value is executed as a command or program text.
#: These are what the scanner needs to see; everything else in a batch is
#: inert data as far as the signature rules are concerned.
_COMMAND_ARG_KEYS = ("command", "commands", "script", "code", "shell")

#: Name of the surrogate the scanner actually inspects. Leading underscore
#: and a `.sh` suffix so `iter_batch_files` never picks it up as a script.
_SCAN_SURROGATE_NAME = "_batch_commands.sh"


def collect_command_strings(content: Any) -> list[str]:
    """Pull the executable strings out of batch content, in step order.

    Only the argument keys that a tool actually runs — a `${args.*}`
    placeholder in a `file_path` is not a command and dumping the whole
    JSON in would just add noise for the pattern rules to trip over.
    """
    commands: list[str] = []
    for action in extract_actions(content) or []:
        if not isinstance(action, dict):
            continue
        raw_args = action.get("arguments")
        if not isinstance(raw_args, dict):
            raw_args = action.get("args")
        if not isinstance(raw_args, dict):
            continue
        for key in _COMMAND_ARG_KEYS:
            value = raw_args.get(key)
            if isinstance(value, str) and value.strip():
                commands.append(value)
            elif isinstance(value, list):
                commands.extend(
                    item for item in value if isinstance(item, str)
                )
    return commands


def scan_batch_dir_or_raise(dir_path: Path, label: str) -> None:
    """Run the skill security scanner over staged batch files.

    A batch's shell payload has to be handed to the scanner **as shell**.
    Being absent from the scanner's skip set only means a ``.json`` file
    gets read; every signature rule is then filtered by ``file_types``
    (``analyzers/pattern_analyzer.py``), and no shipped rule lists
    ``json`` — so scanning the raw file finds nothing no matter what is in
    ``arguments.command``. Verified: the same ``chmod 777 /etc/passwd``
    payload scores CRITICAL as ``.sh`` and SAFE as ``.json``.

    So write the command strings to a ``.sh`` surrogate beside the batch
    and let the bash rules do their job. The surrogate is staged only for
    the scan — it is never copied into the pool, because callers scan a
    staging directory and copy the ``.json`` alone.

    Note this reports rather than blocks: ``scan_skill_directory``
    defaults to ``warn`` mode, so a finding is logged and the import
    proceeds. Turning that into a hard gate is a policy choice that would
    also need to apply to skills and template packages.
    """
    surrogate: Path | None = None
    try:
        commands: list[str] = []
        for path in sorted(dir_path.glob("*.json")):
            try:
                commands.extend(
                    collect_command_strings(
                        json.loads(path.read_text(encoding="utf-8")),
                    ),
                )
            except (OSError, ValueError, RecursionError):
                # Unparseable content is rejected by validation with a
                # precise message; the scan should not pre-empt that.
                continue
        if commands:
            surrogate = dir_path / _SCAN_SURROGATE_NAME
            surrogate.write_text("\n".join(commands), encoding="utf-8")
        scan_skill_directory(dir_path, skill_name=label)
    finally:
        if surrogate is not None:
            surrogate.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Zip pack / unpack
# ---------------------------------------------------------------------------


def pack_batch_to_zip(name: str, data: bytes) -> bytes:
    """Zip a single ``<name>.json`` entry.

    The exported file can be fed straight back into
    ``POST /tool-batches/upload``, so export → import is lossless.
    """
    normalized = normalize_batch_name(name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{normalized}.json", data)
    return buffer.getvalue()


def extract_upload_zip(data: bytes, dest_dir: Path) -> None:
    """Extract an uploaded batch zip with the shared archive defenses.

    Same helper as skills and cron templates, so traversal / symlink /
    bomb protection cannot drift between upload paths.
    """
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ToolBatchError("Uploaded file is not a valid zip archive")
    extract_zip_safely(
        data,
        dest_dir,
        max_bytes=MAX_BATCH_ZIP_BYTES,
        max_entries=MAX_BATCH_ZIP_ENTRIES,
        error_factory=ToolBatchError,
    )


def discover_zip_batch_files(root: Path) -> list[tuple[Path, str]]:
    """Find candidate ``*.json`` batch files under an extracted zip.

    Returns ``(path, zip-relative posix name)`` pairs in stable order.
    """
    found: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.json")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(is_ignored_skill_entry(part) for part in relative.parts):
            continue
        found.append((path, relative.as_posix()))
    return found


def name_from_file_name(file_name: str) -> str:
    """Derive a pool name from a source file name.

    Only ``.json`` files can become pool scripts
    (``_load_batch_file`` hard-requires the extension), so anything else
    is rejected here instead of producing an unloadable script.
    """
    base = str(file_name or "").replace("\\", "/").strip().rsplit("/", 1)[-1]
    if not base.endswith(".json"):
        raise ToolBatchError(
            f"Batch file must have a .json extension: {file_name}",
        )
    return normalize_batch_name(base[: -len(".json")])


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


def build_import_conflict(
    name: str,
    file_name: str,
    existing: set[str],
) -> dict[str, Any]:
    """Describe a name collision plus a free alternative to rename to."""
    return {
        "name": name,
        "file_name": file_name,
        "suggested_name": suggest_conflict_name(name, existing),
    }


def suggest_conflict_name(name: str, existing: set[str] | None = None) -> str:
    """Return ``<name>-2``, ``<name>-3``, … skipping taken names."""
    taken = set(existing or ())
    if not taken:
        pool = get_tool_batch_dir()
        if pool.exists():
            taken = {path.stem for path in iter_batch_files(pool)}
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"


__all__ = [
    "MAX_BATCH_ZIP_BYTES",
    "MAX_BATCH_ZIP_ENTRIES",
    "PREVIEW_ACTION_LIMIT",
    "apply_description",
    "build_batch_info",
    "build_import_conflict",
    "discover_zip_batch_files",
    "ensure_batch_pool_initialized",
    "extract_actions",
    "extract_arg_names",
    "extract_description",
    "extract_upload_zip",
    "get_tool_batch_dir",
    "iter_batch_files",
    "name_from_file_name",
    "normalize_batch_name",
    "pack_batch_to_zip",
    "read_batch_content",
    "resolve_batch_file",
    "safe_batch_path",
    "scan_batch_dir_or_raise",
    "suggest_conflict_name",
    "validate_batch_content",
    "write_batch_file",
]
