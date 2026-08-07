# -*- coding: utf-8 -*-
"""Management for ``run_tool_batch`` scripts.

Cron preprocesses run a batch script before every fire to collect data
deterministically. A script belongs to the job that runs it and lives in
``<workspace_dir>/cron_jobs/<job_id>/batch/`` as a flat ``<name>.json``
file; this package provides the CRUD / zip-import / export machinery over
one such directory, with staged-write validation and security scanning.

``ToolBatchService`` takes the directory as a constructor argument and is
otherwise root-agnostic, so the same code serves any owner. Build the root
with :func:`store.job_batch_dir`.
"""

from .models import (
    CreateToolBatchRequest,
    ToolBatchDetail,
    ToolBatchInfo,
    UpdateToolBatchRequest,
)
from .service import ToolBatchService
from .store import job_batch_dir

__all__ = [
    "CreateToolBatchRequest",
    "ToolBatchDetail",
    "ToolBatchInfo",
    "ToolBatchService",
    "UpdateToolBatchRequest",
    "job_batch_dir",
]
