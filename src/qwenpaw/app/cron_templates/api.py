# -*- coding: utf-8 -*-
"""HTTP API for folder-based cron job template packages.

Endpoint shape mirrors ``routers/skills.py``: list / get / create / delete
plus zip ``upload`` and ``export``, with security-scan failures surfaced as
structured 422 payloads and name collisions as 409 so the console can offer
a rename instead of silently overwriting.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi import File as FileParam
from fastapi.responses import JSONResponse, Response

from ...exceptions import (
    AppBaseException,
    CronTemplateConflictError,
    CronTemplateError,
    SkillScanError,
)
from ...utils.http import content_disposition_attachment
from ..utils import check_upload_size
from .models import (
    CreateCronTemplateRequest,
    CronTemplateInfo,
    TemplateBatchScriptInfo,
    UpdateCronTemplateRequest,
)
from .service import CronTemplateService

logger = logging.getLogger(__name__)


async def _workspace_dir_for_request(request: Request) -> Path:
    from ..agent_context import get_agent_for_request

    workspace = await get_agent_for_request(request)
    return Path(workspace.workspace_dir)


async def get_template_service(request: Request) -> CronTemplateService:
    """Build the service for the request's workspace.

    Templates are per workspace, so every endpoint needs one bound to the
    active agent. A dependency rather than a line in each handler: one
    place to get the resolution right, and FastAPI injects the ``Request``
    so the handlers need not carry it themselves.
    """
    return CronTemplateService(await _workspace_dir_for_request(request))


router = APIRouter(prefix="/cron-templates", tags=["cron-templates"])

#: Batch scripts bundled inside template packages, listed across all of
#: them. Its own prefix rather than a static sibling under
#: ``/cron-templates``: such a sibling only works while it is declared
#: before ``GET /{name}``, which is a trap for whoever reorders the routes.
batch_script_router = APIRouter(
    prefix="/cron-template-batches",
    tags=["cron-templates"],
)


@batch_script_router.get("", response_model=list[TemplateBatchScriptInfo])
async def list_template_batch_scripts(
    include_builtin: bool = Query(
        default=True,
        description="Include scripts bundled with builtin templates",
    ),
    service: CronTemplateService = Depends(get_template_service),
) -> list[TemplateBatchScriptInfo]:
    """List every ``batch/*.json`` bundled across template packages.

    A cron preprocess copies one of these into the job that will run it, so
    the console needs them all with their describing metadata in one
    request.
    """
    return await asyncio.to_thread(
        service.list_batch_scripts,
        include_builtin=include_builtin,
    )


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


def _not_found(exc: CronTemplateError) -> HTTPException:
    """Map "not found" phrasing onto 404, everything else onto 400."""
    message = exc.message or str(exc)
    status = 404 if "not found" in message.lower() else 400
    return HTTPException(status_code=status, detail=message)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CronTemplateInfo])
async def list_templates(
    include_builtin: bool = Query(
        default=True,
        description="Include packaged builtin template folders",
    ),
    service: CronTemplateService = Depends(get_template_service),
) -> list[CronTemplateInfo]:
    return await asyncio.to_thread(
        service.list_templates,
        include_builtin=include_builtin,
    )


@router.get("/{name}", response_model=CronTemplateInfo)
async def get_template(
    name: str,
    service: CronTemplateService = Depends(get_template_service),
) -> CronTemplateInfo:
    try:
        return await asyncio.to_thread(service.get_template, name)
    except CronTemplateError as exc:
        raise _not_found(exc) from exc


@router.get("/{name}/files/{file_path:path}")
async def read_template_file(
    name: str,
    file_path: str,
    service: CronTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    """Read one text file from a package (batch JSON preview, docs)."""
    try:
        content = await asyncio.to_thread(
            service.read_package_file,
            name,
            file_path,
        )
    except CronTemplateError as exc:
        raise _not_found(exc) from exc
    return {"name": name, "path": file_path, "content": content}


@router.get("/{name}/export")
async def export_template(
    name: str,
    service: CronTemplateService = Depends(get_template_service),
) -> Response:
    """Download a template package as a zip.

    The archive is rooted at ``<name>/`` so the downloaded file can be fed
    straight back into ``POST /cron-templates/upload``.
    """
    try:
        filename, blob = await asyncio.to_thread(
            service.export_to_zip,
            name,
        )
    except CronTemplateError as exc:
        raise _not_found(exc) from exc
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition_attachment(filename),
            "Content-Length": str(len(blob)),
        },
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@router.post("", response_model=CronTemplateInfo)
async def create_template(
    body: CreateCronTemplateRequest,
    service: CronTemplateService = Depends(get_template_service),
) -> CronTemplateInfo:
    try:
        return await asyncio.to_thread(
            service.create_template,
            body,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except CronTemplateConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except (CronTemplateError, ValueError, AppBaseException) as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "message", None) or str(exc),
        ) from exc


@router.put("/{name}", response_model=CronTemplateInfo)
async def update_template(
    name: str,
    body: UpdateCronTemplateRequest,
    service: CronTemplateService = Depends(get_template_service),
) -> CronTemplateInfo:
    """Patch an existing pool template.

    Omitted fields keep their current values and unmentioned package files
    (bundled skills, assets, other batch scripts) are preserved.
    """
    try:
        return await asyncio.to_thread(
            service.update_template,
            name,
            body,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except CronTemplateError as exc:
        raise _not_found(exc) from exc


@router.delete("/{name}")
async def delete_template(
    name: str,
    service: CronTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    try:
        deleted = await asyncio.to_thread(
            service.delete_template,
            name,
        )
    except CronTemplateError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message or str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="template not found")
    return {"deleted": True, "name": name}


@router.post("/{name}/fork", response_model=CronTemplateInfo)
async def fork_builtin_template(
    name: str,
    service: CronTemplateService = Depends(get_template_service),
) -> CronTemplateInfo:
    """Copy a packaged builtin into the pool so it can be edited."""
    try:
        return await asyncio.to_thread(
            service.fork_builtin,
            name,
        )
    except CronTemplateConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except CronTemplateError as exc:
        raise _not_found(exc) from exc


@router.post("/upload")
async def upload_template_zip(
    file: UploadFile = FileParam(...),
    target_name: str = "",
    rename_map: str = "",
    overwrite: bool = False,
    service: CronTemplateService = Depends(get_template_service),
) -> dict[str, Any]:
    """Import template packages from an uploaded zip.

    ``rename_map`` is a JSON object (``{"old": "new"}``) letting the client
    resolve the conflicts reported by a previous 409 without re-uploading
    under a different filename.
    """
    data = await _read_validated_zip_upload(file)
    parsed_rename: dict[str, str] | None = None
    if rename_map.strip():
        try:
            parsed_rename = json.loads(rename_map)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="rename_map must be valid JSON",
            ) from exc
        if not isinstance(parsed_rename, dict):
            raise HTTPException(
                status_code=400,
                detail="rename_map must be a JSON object",
            )
    try:
        result = await asyncio.to_thread(
            service.import_from_zip,
            data,
            target_name=target_name,
            rename_map=parsed_rename,
            overwrite=overwrite,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except (CronTemplateError, ValueError, AppBaseException) as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "message", None) or str(exc),
        ) from exc
    if result.get("conflicts"):
        raise HTTPException(status_code=409, detail=result)
    return result
