# -*- coding: utf-8 -*-
"""Models and constants for folder-based cron job template packages.

A template package mirrors the skill package layout so the same mental
model (and the same import / export / scan machinery) applies:

    <template_name>/
    ├── TEMPLATE.md      required — frontmatter + human-readable docs
    ├── template.json    required — the job payload (form values / spec)
    ├── batch/*.json     optional — ``run_tool_batch`` scripts
    ├── skills/<name>/   optional — skills shipped with the template
    ├── scripts/         optional — helper scripts referenced by the batch
    └── assets/          optional — anything else the template needs

``TEMPLATE.md`` frontmatter is the single source of truth for display
metadata (mirroring how ``SKILL.md`` frontmatter works)::

    ---
    name: daily-standup-digest
    description: 每天早上汇总昨日进展并推送到群里
    metadata:
      qwenpaw:
        emoji: "📊"
        category: cron          # cron | once
        frequency: 每个工作日 09:30
        tags: [team, reminder]
        version: "1.0"
    ---
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TEMPLATE_DOC_FILE = "TEMPLATE.md"
TEMPLATE_PAYLOAD_FILE = "template.json"
TEMPLATE_BATCH_DIR = "batch"
TEMPLATE_SKILLS_DIR = "skills"

PAYLOAD_SCHEMA_VERSION = "cron-template.v1"
MANIFEST_SCHEMA_VERSION = "cron-template-manifest.v1"

#: Metadata namespaces accepted in ``TEMPLATE.md`` frontmatter, in order.
#: Matches the skill system's tolerance for vendor-prefixed metadata.
METADATA_NAMESPACES = ("qwenpaw", "openclaw", "clawdbot")

TemplateCategory = Literal["cron", "once"]
TemplateSource = Literal["user", "builtin"]

#: Tags the frontend knows how to render. Unknown tags are preserved and
#: shown verbatim rather than rejected, so shared packages stay portable.
KNOWN_TEMPLATE_TAGS = ("personal", "team", "reminder", "calendar")


class CronTemplatePayload(BaseModel):
    """Contents of ``template.json``.

    Two representations of the same recipe, both optional-but-one-required:

    - ``form``: values fed straight into the console's job drawer. This is
      what the UI round-trips, so cron-expression ⇄ form conversion stays
      in one place (the frontend's ``parseCron``).
    - ``job``: a ``CronJobSpec``-shaped dict for headless creation (CLI /
      API). Placeholders are allowed since a template has no real target.

    ``extra`` is permissive on purpose: a template authored by a newer
    QwenPaw must not fail to load on an older one.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: str = PAYLOAD_SCHEMA_VERSION
    form: dict[str, Any] = Field(default_factory=dict)
    job: Optional[dict[str, Any]] = None
    #: ``run_tool_batch`` file (relative to the package root) that the
    #: template's agent prompt is expected to invoke.
    batch_entry: Optional[str] = None


class CronTemplateInfo(BaseModel):
    """A template package as returned to callers."""

    name: str
    title: str = ""
    description: str = ""
    category: TemplateCategory = "cron"
    frequency: str = ""
    emoji: str = ""
    tags: list[str] = Field(default_factory=list)
    version_text: str = ""
    source: TemplateSource = "user"
    #: i18n keys for the three display strings above.
    #:
    #: Packages that ship with QwenPaw set these instead of literal text so
    #: they render in the user's language; a package authored by a user
    #: carries literals and leaves these empty. Clients resolve key-first,
    #: falling back to the literal.
    title_key: str = ""
    description_key: str = ""
    frequency_key: str = ""
    #: ``TEMPLATE.md`` body with the frontmatter stripped.
    content: str = ""
    payload: CronTemplatePayload = Field(default_factory=CronTemplatePayload)
    #: Package-relative paths, e.g. ``batch/collect.json``.
    batch_files: list[str] = Field(default_factory=list)
    #: Bundled skill directory names under ``skills/``.
    skills: list[str] = Field(default_factory=list)
    #: Every regular file in the package, package-relative and sorted.
    files: list[str] = Field(default_factory=list)
    #: Absolute path of the package on disk.
    #:
    #: Needed because a template's agent prompt has to tell
    #: ``run_tool_batch`` where its batch file actually *is* — a
    #: package-relative path is meaningless to a tool running with the
    #: agent workspace as its cwd. Clients substitute this into
    #: ``{{template_dir}}`` when instantiating the template.
    package_dir: str = ""
    #: Absolute path of ``payload.batch_entry``, or "" when unset.
    #: Substituted into ``{{batch_entry}}``.
    batch_entry_path: str = ""
    updated_at: str = ""


class CronTemplateFrontmatter(BaseModel):
    """Display metadata parsed out of ``TEMPLATE.md``."""

    name: str = ""
    description: str = ""
    title: str = ""
    category: TemplateCategory = "cron"
    frequency: str = ""
    emoji: str = ""
    tags: list[str] = Field(default_factory=list)
    version_text: str = ""
    #: See :class:`CronTemplateInfo` — i18n keys instead of literal text.
    title_key: str = ""
    description_key: str = ""
    frequency_key: str = ""


class CreateCronTemplateRequest(BaseModel):
    """Create or replace a template from structured fields.

    Used by the console's "save job as template" flow, where the browser
    already holds the form values and only needs to name the result.
    """

    name: str
    title: str = ""
    description: str = ""
    category: TemplateCategory = "cron"
    frequency: str = ""
    emoji: str = ""
    tags: list[str] = Field(default_factory=list)
    version_text: str = ""
    #: Markdown body for ``TEMPLATE.md``; generated when omitted.
    body: str = ""
    form: dict[str, Any] = Field(default_factory=dict)
    job: Optional[dict[str, Any]] = None
    batch_entry: Optional[str] = None
    #: ``{"collect.json": "<json text>"}`` written under ``batch/``.
    batch_files: dict[str, str] = Field(default_factory=dict)
    #: ``{"report-writer": "<SKILL.md text>"}`` written under ``skills/``.
    skills: dict[str, str] = Field(default_factory=dict)
    #: Nested tree of additional files, same shape the skill system uses.
    extra_files: dict[str, Any] = Field(default_factory=dict)
    overwrite: bool = False


class UpdateCronTemplateRequest(BaseModel):
    """Patch an existing template package.

    Every field is optional and ``None`` means "leave as-is" — a partial
    edit from the console must not silently blank out metadata it did not
    render, nor drop the batch scripts and skills already in the package.
    Same merge semantics as ``qwenpaw cron update`` for jobs.
    """

    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[TemplateCategory] = None
    frequency: Optional[str] = None
    emoji: Optional[str] = None
    tags: Optional[list[str]] = None
    version_text: Optional[str] = None
    body: Optional[str] = None
    form: Optional[dict[str, Any]] = None
    job: Optional[dict[str, Any]] = None
    #: Empty string clears the entry; ``None`` leaves it alone.
    batch_entry: Optional[str] = None
    #: Batch files to add or replace. Files not listed are kept.
    batch_files: Optional[dict[str, str]] = None
    #: Batch files to delete, by name under ``batch/``.
    remove_batch_files: list[str] = Field(default_factory=list)


class InstallTemplateSkillsRequest(BaseModel):
    """Install skills bundled inside a template package."""

    #: Subset of the package's bundled skills; empty means all of them.
    skills: list[str] = Field(default_factory=list)
    target: Literal["pool", "workspace"] = "pool"
    #: Only meaningful for ``target="workspace"``.
    enable: bool = False
    overwrite: bool = False


class InstallTemplateBatchesRequest(BaseModel):
    """Copy a template package's ``batch/*.json`` scripts into the pool.

    The scripts are copied (keeping their base names), never referenced
    in place — a job pointing at a file inside a template package would
    break the moment the package is updated, forked or deleted.
    """

    overwrite: bool = False
    #: ``{"old": "new"}`` pool-name renames, same semantics as the
    #: tool-batches ``upload`` endpoint.
    rename_map: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "KNOWN_TEMPLATE_TAGS",
    "MANIFEST_SCHEMA_VERSION",
    "METADATA_NAMESPACES",
    "PAYLOAD_SCHEMA_VERSION",
    "TEMPLATE_BATCH_DIR",
    "TEMPLATE_DOC_FILE",
    "TEMPLATE_PAYLOAD_FILE",
    "TEMPLATE_SKILLS_DIR",
    "CreateCronTemplateRequest",
    "CronTemplateFrontmatter",
    "CronTemplateInfo",
    "CronTemplatePayload",
    "InstallTemplateBatchesRequest",
    "InstallTemplateSkillsRequest",
    "TemplateCategory",
    "TemplateSource",
    "UpdateCronTemplateRequest",
]
