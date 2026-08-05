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

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi import File as FileParam
from fastapi.responses import JSONResponse, Response

from ...exceptions import (
    AppBaseException,
    CronTemplateConflictError,
    CronTemplateError,
    SkillScanError,
    ToolBatchError,
)
from ...utils.http import content_disposition_attachment
from ..utils import check_upload_size, schedule_agent_reload
from .models import (
    CreateCronTemplateRequest,
    CronTemplateInfo,
    InstallTemplateBatchesRequest,
    InstallTemplateSkillsRequest,
    UpdateCronTemplateRequest,
)
from .service import CronTemplateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron-templates", tags=["cron-templates"])

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


async def _workspace_dir_for_request(request: Request) -> Path:
    from ..agent_context import get_agent_for_request

    workspace = await get_agent_for_request(request)
    return Path(workspace.workspace_dir)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CronTemplateInfo])
async def list_templates(
    include_builtin: bool = Query(
        default=True,
        description="Include packaged builtin template folders",
    ),
) -> list[CronTemplateInfo]:
    return await asyncio.to_thread(
        CronTemplateService().list_templates,
        include_builtin=include_builtin,
    )


@router.get("/{name}", response_model=CronTemplateInfo)
async def get_template(name: str) -> CronTemplateInfo:
    try:
        return await asyncio.to_thread(
            CronTemplateService().get_template, name
        )
    except CronTemplateError as exc:
        raise _not_found(exc) from exc


@router.get("/{name}/files/{file_path:path}")
async def read_template_file(name: str, file_path: str) -> dict[str, Any]:
    """Read one text file from a package (batch JSON preview, docs)."""
    try:
        content = await asyncio.to_thread(
            CronTemplateService().read_package_file,
            name,
            file_path,
        )
    except CronTemplateError as exc:
        raise _not_found(exc) from exc
    return {"name": name, "path": file_path, "content": content}


@router.get("/{name}/export")
async def export_template(name: str) -> Response:
    """Download a template package as a zip.

    The archive is rooted at ``<name>/`` so the downloaded file can be fed
    straight back into ``POST /cron-templates/upload``.
    """
    try:
        filename, blob = await asyncio.to_thread(
            CronTemplateService().export_to_zip,
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
) -> CronTemplateInfo:
    try:
        return await asyncio.to_thread(
            CronTemplateService().create_template,
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
) -> CronTemplateInfo:
    """Patch an existing pool template.

    Omitted fields keep their current values and unmentioned package files
    (bundled skills, assets, other batch scripts) are preserved.
    """
    try:
        return await asyncio.to_thread(
            CronTemplateService().update_template,
            name,
            body,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except CronTemplateError as exc:
        raise _not_found(exc) from exc


@router.delete("/{name}")
async def delete_template(name: str) -> dict[str, Any]:
    try:
        deleted = await asyncio.to_thread(
            CronTemplateService().delete_template,
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
async def fork_builtin_template(name: str) -> CronTemplateInfo:
    """Copy a packaged builtin into the pool so it can be edited."""
    try:
        return await asyncio.to_thread(
            CronTemplateService().fork_builtin,
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
            CronTemplateService().import_from_zip,
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


@router.post("/{name}/install-skills")
async def install_template_skills(
    name: str,
    request: Request,
    body: InstallTemplateSkillsRequest,
) -> dict[str, Any]:
    """Install skills bundled in a template into the pool or a workspace."""
    workspace_dir: Path | None = None
    if body.target == "workspace":
        workspace_dir = await _workspace_dir_for_request(request)
    try:
        result = await asyncio.to_thread(
            CronTemplateService().install_skills,
            name,
            body,
            workspace_dir,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except CronTemplateError as exc:
        raise _not_found(exc) from exc
    if body.target == "workspace" and body.enable and result.get("installed"):
        from ..agent_context import get_agent_for_request

        workspace = await get_agent_for_request(request)
        schedule_agent_reload(request, workspace.agent_id)
    return result


@router.post("/{name}/install-batches")
async def install_template_batches(
    name: str,
    body: InstallTemplateBatchesRequest,
) -> dict[str, Any]:
    """Copy a template package's ``batch/*.json`` scripts into the pool.

    Scripts are copied (base names kept) through the same
    validate + scan + conflict pipeline as ``POST /tool-batches/upload``,
    so jobs only ever resolve scripts from the one pool directory.
    """
    try:
        result = await asyncio.to_thread(
            CronTemplateService().install_batches,
            name,
            body,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except CronTemplateError as exc:
        raise _not_found(exc) from exc
    except ToolBatchError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message or str(exc),
        ) from exc
    if result.get("conflicts"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Batch install has conflicts",
                "conflicts": result["conflicts"],
            },
        )
    return {"installed": result["imported"], "conflicts": []}
