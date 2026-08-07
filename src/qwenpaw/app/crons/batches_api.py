# -*- coding: utf-8 -*-
"""HTTP API for the batch scripts a cron job owns.

Each job keeps its scripts in ``<workspace_dir>/cron_jobs/<job_id>/batch/``
and nothing is shared: two jobs that want the same recipe hold two
independent copies. So every route here is scoped to one job, and the only
way to get at somebody else's script is ``POST .../copy``, which duplicates
it rather than linking to it.

Status codes follow the skills API: a structured 422 for scanner
rejections, a 409 carrying a suggested rename for a name collision.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import File as FileParam
from fastapi import UploadFile
from fastapi.responses import JSONResponse, Response

from ...exceptions import (
    AppBaseException,
    SkillScanError,
    ToolBatchConflictError,
    ToolBatchError,
)
from ...utils.http import content_disposition_attachment
from ..utils import check_upload_size
from ..tool_batches.models import (
    CopyToolBatchRequest,
    CreateToolBatchRequest,
    JobToolBatches,
    ToolBatchDetail,
    ToolBatchInfo,
    UpdateToolBatchRequest,
)
from ..tool_batches.service import ToolBatchService
from ..tool_batches.store import (
    job_batch_dir,
    read_batch_content,
    suggest_conflict_name,
)
from .api import get_cron_manager
from .manager import INTERNAL_JOB_IDS, CronManager
from .script_paths import is_safe_job_id, iter_job_script_dirs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["cron"])

_ALLOWED_ZIP_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
    "multipart/x-zip",
}


def _scan_error_response(exc: SkillScanError) -> JSONResponse:
    """Structured 422 for scanner rejections (same shape as skills)."""
    result = exc.result
    return JSONResponse(
        status_code=422,
        content={
            "type": "security_scan_failed",
            "detail": str(exc),
            "template_name": result.skill_name,
            "max_severity": result.max_severity.value,
            "findings": [
                {
                    "severity": finding.severity.value,
                    "title": finding.title,
                    "description": finding.description,
                    "file_path": finding.file_path,
                    "line_number": finding.line_number,
                    "rule_id": finding.rule_id,
                }
                for finding in result.findings
            ],
        },
    )


async def _read_validated_zip_upload(file: UploadFile) -> bytes:
    if file.content_type and file.content_type not in _ALLOWED_ZIP_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Expected a zip file, got content-type: {file.content_type}"
            ),
        )
    data = await file.read()
    check_upload_size(data)
    return data


def _not_found(exc: ToolBatchError) -> HTTPException:
    """Map "not found" phrasing onto 404, everything else onto 400."""
    message = exc.message or str(exc)
    status = 404 if "not found" in message.lower() else 400
    return HTTPException(status_code=status, detail=message)


def _parse_rename_map(rename_map: str) -> dict[str, str] | None:
    if not rename_map.strip():
        return None
    try:
        parsed = json.loads(rename_map)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="rename_map must be valid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="rename_map must be a JSON object",
        )
    return parsed


async def _workspace_dir(request: Request) -> Path:
    from ..agent_context import get_agent_for_request

    workspace = await get_agent_for_request(request)
    return Path(workspace.workspace_dir)


async def _job_service(request: Request, job_id: str) -> ToolBatchService:
    """Build a service rooted at one job's scripts directory.

    The ``job_id`` gate is the important part: it arrives as a path segment
    and becomes a directory name. ``job_batch_dir`` re-checks it and raises,
    independently of the write-boundary check in
    ``CronManager.create_or_replace_job``.

    The job is deliberately *not* required to exist: the console mints a
    uuid when the create drawer opens and writes scripts under it before
    the job is saved, and an abandoned drawer is cleaned up by the orphan
    reaper at next start.

    ``INTERNAL_JOB_IDS`` is the exception. ``is_safe_job_id`` allows
    ``_heartbeat`` / ``_dream`` because they legitimately own directories,
    but a client must not write into them — and since the reaper
    deliberately *spares* those ids, anything planted there would survive
    every restart while being invisible to every UI.
    """
    if not is_safe_job_id(job_id):
        raise HTTPException(status_code=400, detail=f"Unsafe job id: {job_id}")
    if job_id in INTERNAL_JOB_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"job id is reserved: {job_id}",
        )
    workspace_dir = await _workspace_dir(request)
    try:
        return ToolBatchService(job_batch_dir(workspace_dir, job_id))
    except ToolBatchError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message or str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Read
#
# Static segments are declared before `{name}` so `upload` and `copy` are
# not swallowed as script names.
# ---------------------------------------------------------------------------


@router.get("/job-batches", response_model=list[JobToolBatches])
async def list_all_job_batches(
    request: Request,
    mgr: CronManager = Depends(get_cron_manager),
    exclude_job_id: str = Query(
        default="",
        description="Omit this job, which the caller already lists itself",
    ),
) -> list[JobToolBatches]:
    """Every job's scripts in this workspace, grouped by job.

    Feeds the picker's "other tasks' scripts" browser. Read-only: picking
    one of these copies it, it is never referenced in place.
    """
    workspace_dir = await _workspace_dir(request)
    names = {
        job.id: (job.name or "")
        for job in await mgr.list_jobs()
        if job.id is not None
    }
    groups: list[JobToolBatches] = []
    for job_id, _scripts_dir in iter_job_script_dirs(workspace_dir):
        if job_id == exclude_job_id:
            continue
        service = await _job_service(request, job_id)
        batches = await asyncio.to_thread(service.list_batches)
        if not batches:
            continue
        groups.append(
            JobToolBatches(
                job_id=job_id,
                job_name=names.get(job_id, ""),
                batches=batches,
            ),
        )
    return groups


@router.get("/jobs/{job_id}/batches", response_model=list[ToolBatchInfo])
async def list_job_batches(
    request: Request,
    job_id: str,
) -> list[ToolBatchInfo]:
    service = await _job_service(request, job_id)
    return await asyncio.to_thread(service.list_batches)


@router.post("/jobs/{job_id}/batches", response_model=ToolBatchInfo)
async def create_job_batch(
    request: Request,
    job_id: str,
    body: CreateToolBatchRequest,
) -> ToolBatchInfo:
    service = await _job_service(request, job_id)
    try:
        return await asyncio.to_thread(service.create_batch, body)
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except ToolBatchConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except (ToolBatchError, ValueError, AppBaseException) as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "message", None) or str(exc),
        ) from exc


@router.post("/jobs/{job_id}/batches/copy", response_model=ToolBatchInfo)
async def copy_job_batch(
    request: Request,
    job_id: str,
    body: CopyToolBatchRequest,
) -> ToolBatchInfo:
    """Copy another job's or a template's script into this job.

    Resolved server-side from the source *fields*: the client never holds a
    packed identifier that could end up stored as a step's script.

    A name collision is resolved by picking a free variant rather than
    failing — the caller is mid-gesture in a dropdown, and the response
    carries the name that actually landed.
    """
    service = await _job_service(request, job_id)
    workspace_dir = await _workspace_dir(request)
    source = await asyncio.to_thread(
        _resolve_copy_source,
        workspace_dir,
        body,
    )
    try:
        content = await asyncio.to_thread(read_batch_content, source)
    except ToolBatchError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message or str(exc),
        ) from exc

    taken = {
        info.name for info in await asyncio.to_thread(service.list_batches)
    }
    wanted = (body.name or "").strip() or source.stem
    name = (
        wanted
        if wanted not in taken
        else suggest_conflict_name(
            wanted,
            taken,
        )
    )
    try:
        return await asyncio.to_thread(
            service.create_batch,
            CreateToolBatchRequest(name=name, content=content),
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except (ToolBatchError, ValueError, AppBaseException) as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "message", None) or str(exc),
        ) from exc


def _resolve_copy_source(
    workspace_dir: Path,
    body: CopyToolBatchRequest,
) -> Path:
    """Locate the file a copy request points at, or raise 400/404."""
    from ..cron_templates.store import resolve_bundled_batch_script
    from .models import CronJobSkillRef
    from .script_paths import resolve_job_script
    from .skill_refs import resolve_skill_batch_script

    given = [
        bool(body.from_job_id),
        bool(body.from_template),
        bool(body.from_skill),
    ]
    if sum(given) != 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide exactly one of from_job_id, from_template or "
                "from_skill"
            ),
        )
    # A qualifier with nothing to qualify. Rejected rather than ignored: it
    # would otherwise silently fall through to another source and copy a
    # different file than the caller asked for.
    if body.from_skill_template and not body.from_skill:
        raise HTTPException(
            status_code=400,
            detail="from_skill_template requires from_skill",
        )
    if body.from_skill:
        try:
            ref = CronJobSkillRef(
                name=body.from_skill,
                template=body.from_skill_template,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        found = resolve_skill_batch_script(ref, body.file, workspace_dir)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Script '{body.file}' not found in skill "
                    f"'{body.from_skill}'"
                ),
            )
        return found
    if body.from_job_id:
        found = resolve_job_script(
            workspace_dir,
            body.from_job_id,
            body.file,
        )
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Script '{body.file}' not found in job "
                    f"{body.from_job_id}"
                ),
            )
        return found
    found = resolve_bundled_batch_script(
        body.from_template or "",
        body.file,
        workspace_dir,
    )
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Script '{body.file}' not found in template "
                f"'{body.from_template}'"
            ),
        )
    return found


@router.post("/jobs/{job_id}/batches/upload")
async def upload_job_batch_zip(
    request: Request,
    job_id: str,
    *,
    file: UploadFile = FileParam(...),
    select: str = "",
    rename_map: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import scripts from a zip into this job.

    Two-phase selection: a zip holding several ``.json`` files and no
    ``select`` returns the candidate list and writes nothing.
    """
    service = await _job_service(request, job_id)
    data = await _read_validated_zip_upload(file)
    parsed_rename = _parse_rename_map(rename_map)
    selected = [
        item.strip() for item in select.split(",") if item.strip()
    ] or None
    try:
        result = await asyncio.to_thread(
            service.import_from_zip,
            data,
            select=selected,
            rename_map=parsed_rename,
            overwrite=overwrite,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except (ToolBatchError, ValueError, AppBaseException) as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "message", None) or str(exc),
        ) from exc
    if "candidates" in result:
        return result
    if result.get("conflicts"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Batch import has conflicts",
                "conflicts": result["conflicts"],
            },
        )
    return {"imported": result["imported"], "conflicts": []}


@router.get(
    "/jobs/{job_id}/batches/{name}",
    response_model=ToolBatchDetail,
)
async def get_job_batch(
    request: Request,
    job_id: str,
    name: str,
) -> ToolBatchDetail:
    service = await _job_service(request, job_id)
    try:
        return await asyncio.to_thread(service.get_batch, name)
    except ToolBatchError as exc:
        raise _not_found(exc) from exc


@router.get("/jobs/{job_id}/batches/{name}/export")
async def export_job_batch(
    request: Request,
    job_id: str,
    name: str,
) -> Response:
    """Download one script as a zip, re-importable through ``upload``."""
    service = await _job_service(request, job_id)
    try:
        filename, blob = await asyncio.to_thread(service.export_to_zip, name)
    except ToolBatchError as exc:
        raise _not_found(exc) from exc
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition_attachment(filename),
            "Content-Length": str(len(blob)),
        },
    )


@router.put("/jobs/{job_id}/batches/{name}", response_model=ToolBatchInfo)
async def update_job_batch(
    request: Request,
    job_id: str,
    name: str,
    body: UpdateToolBatchRequest,
) -> ToolBatchInfo:
    service = await _job_service(request, job_id)
    try:
        return await asyncio.to_thread(service.update_batch, name, body)
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except ToolBatchError as exc:
        raise _not_found(exc) from exc


@router.delete("/jobs/{job_id}/batches/{name}")
async def delete_job_batch(
    request: Request,
    job_id: str,
    name: str,
) -> dict[str, Any]:
    service = await _job_service(request, job_id)
    try:
        deleted = await asyncio.to_thread(service.delete_batch, name)
    except ToolBatchError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message or str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="batch not found")
    return {"deleted": True, "name": name}
