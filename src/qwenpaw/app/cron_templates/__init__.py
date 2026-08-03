# -*- coding: utf-8 -*-
"""Folder-based cron job template packages.

Templates used to be hard-coded in the console. They are now shareable
packages on disk — a directory holding docs, the job payload, optional
``run_tool_batch`` scripts, and optional bundled skills — importable and
exportable as zips, following the same conventions as skill packages.
"""

from .models import (
    CreateCronTemplateRequest,
    CronTemplateInfo,
    CronTemplatePayload,
    InstallTemplateSkillsRequest,
    UpdateCronTemplateRequest,
)
from .service import CronTemplateService
from .store import (
    get_builtin_cron_template_dir,
    get_cron_template_dir,
    read_template_manifest,
)

__all__ = [
    "CreateCronTemplateRequest",
    "CronTemplateInfo",
    "CronTemplatePayload",
    "CronTemplateService",
    "InstallTemplateSkillsRequest",
    "UpdateCronTemplateRequest",
    "get_builtin_cron_template_dir",
    "get_cron_template_dir",
    "read_template_manifest",
]
