# -*- coding: utf-8 -*-
"""HTTP API for the shared ``run_tool_batch`` script pool.

Endpoint shape mirrors ``app/cron_templates/api.py``: list / get /
create / update / delete plus zip ``upload`` and ``export``, with
security-scan failures surfaced as structured 422 payloads and name
collisions as 409 carrying every conflict plus a suggested rename, so
the console can resolve them without re-uploading.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi import File as FileParam
from fastapi.responses import JSONResponse, Response

from ...exceptions import (
    AppBaseException,
    SkillScanError,
    ToolBatchConflictError,
    ToolBatchError,
)
from ...utils.http import content_disposition_attachment
from ..utils import check_upload_size
from .models import (
    CreateToolBatchRequest,
    ToolBatchDetail,
    ToolBatchInfo,
    UpdateToolBatchRequest,
)
from .service import ToolBatchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tool-batches", tags=["tool-batches"])

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


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ToolBatchInfo])
async def list_batches() -> list[ToolBatchInfo]:
    return await asyncio.to_thread(ToolBatchService().list_batches)


@router.get("/{name}", response_model=ToolBatchDetail)
async def get_batch(name: str) -> ToolBatchDetail:
    try:
        return await asyncio.to_thread(ToolBatchService().get_batch, name)
    except ToolBatchError as exc:
        raise _not_found(exc) from exc


@router.get("/{name}/export")
async def export_batch(name: str) -> Response:
    """Download a pool script as a zip holding ``<name>.json``.

    The archive can be fed straight back into ``POST /tool-batches/upload``.
    """
    try:
        filename, blob = await asyncio.to_thread(
            ToolBatchService().export_to_zip,
            name,
        )
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


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@router.post("", response_model=ToolBatchInfo)
async def create_batch(body: CreateToolBatchRequest) -> ToolBatchInfo:
    try:
        return await asyncio.to_thread(
            ToolBatchService().create_batch,
            body,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except ToolBatchConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except (ToolBatchError, ValueError, AppBaseException) as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "message", None) or str(exc),
        ) from exc


@router.put("/{name}", response_model=ToolBatchInfo)
async def update_batch(
    name: str,
    body: UpdateToolBatchRequest,
) -> ToolBatchInfo:
    """Patch an existing pool script. Omitted fields keep their values."""
    try:
        return await asyncio.to_thread(
            ToolBatchService().update_batch,
            name,
            body,
        )
    except SkillScanError as exc:
        return _scan_error_response(exc)  # type: ignore[return-value]
    except ToolBatchError as exc:
        raise _not_found(exc) from exc


@router.delete("/{name}")
async def delete_batch(name: str) -> dict[str, Any]:
    try:
        deleted = await asyncio.to_thread(
            ToolBatchService().delete_batch,
            name,
        )
    except ToolBatchError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message or str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="batch not found")
    return {"deleted": True, "name": name}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload_batch_zip(
    file: UploadFile = FileParam(...),
    select: str = "",
    rename_map: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import batch scripts from an uploaded zip.

    With several ``.json`` files and no ``select``, the response is a 200
    carrying the candidate list and nothing is written; the client picks
    and re-uploads with ``select`` (comma-separated file names).
    ``rename_map`` is a JSON object (``{"old": "new"}``) letting the
    client resolve the conflicts reported by a previous 409.
    """
    data = await _read_validated_zip_upload(file)
    parsed_rename = _parse_rename_map(rename_map)
    selected = [
        item.strip() for item in select.split(",") if item.strip()
    ] or None
    try:
        result = await asyncio.to_thread(
            ToolBatchService().import_from_zip,
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
