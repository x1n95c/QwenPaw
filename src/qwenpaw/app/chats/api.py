# -*- coding: utf-8 -*-
"""Chat management API."""
from __future__ import annotations
import logging
from typing import Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agentscope.message import Msg
from agentscope.state import AgentState

from .session import SafeJSONSession
from .manager import ChatManager, MAX_BATCH_SIZE
from .models import (
    BatchArchiveResult,
    ChatSpec,
    ChatUpdate,
    ChatHistory,
)
from .utils import agentscope_msg_to_message, parse_legacy_memory_state

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/chats", tags=["chats"])


async def get_workspace(request: Request):
    """Get the workspace for the active agent."""
    from ..agent_context import get_agent_for_request

    return await get_agent_for_request(request)


async def get_chat_manager(
    request: Request,
) -> ChatManager:
    """Get the chat manager for the active agent.

    Args:
        request: FastAPI request object

    Returns:
        ChatManager instance for the specified agent

    Raises:
        HTTPException: If manager is not initialized
    """
    workspace = await get_workspace(request)
    return workspace.chat_manager


async def get_session(
    request: Request,
) -> SafeJSONSession:
    """Get the session for the active agent.

    Args:
        request: FastAPI request object

    Returns:
        SafeJSONSession instance for the specified agent

    Raises:
        HTTPException: If session is not initialized
    """
    workspace = await get_workspace(request)
    return workspace.session


@router.get("", response_model=list[ChatSpec])
async def list_chats(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    channel: Optional[str] = Query(None, description="Filter by channel"),
    archived: Optional[bool] = Query(
        None,
        description=(
            "Filter by archived status. "
            "false=active only, true=archived only, "
            "null/omit=all (default)"
        ),
    ),
    mgr: ChatManager = Depends(get_chat_manager),
    workspace=Depends(get_workspace),
):
    """List all chats with optional filters.

    When ``archived`` is omitted, returns all chats (both active and archived).
    Pass ``archived=false`` for active only,
    ``archived=true`` for archived only.
    """
    chats = await mgr.list_chats(
        user_id=user_id,
        channel=channel,
        archived=archived,
    )
    tracker = workspace.task_tracker
    result = []
    for spec in chats:
        status = await tracker.get_status(spec.id)
        result.append(spec.model_copy(update={"status": status}))
    return result


@router.post("", response_model=ChatSpec)
async def create_chat(
    request: ChatSpec,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Create a new chat.

    Server generates chat_id (UUID) automatically.

    Args:
        request: Chat creation request
        mgr: Chat manager dependency

    Returns:
        Created chat spec with UUID
    """
    chat_id = str(uuid4())
    spec = ChatSpec(
        id=chat_id,
        name=request.name,
        session_id=request.session_id,
        user_id=request.user_id,
        channel=request.channel,
        meta=request.meta,
    )
    return await mgr.create_chat(spec)


@router.post("/batch-delete", response_model=dict)
async def batch_delete_chats(
    chat_ids: list[str],
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Delete chats by chat IDs.

    Args:
        chat_ids: List of chat IDs
        mgr: Chat manager dependency
    Returns:
        True if deleted, False if failed

    """
    deleted = await mgr.delete_chats(chat_ids=chat_ids)
    return {"deleted": deleted}


# ----- Archive endpoints -----


class BatchChatIds(BaseModel):
    """Request body for batch archive/unarchive."""

    chat_ids: list[str] = Field(
        ...,
        max_length=MAX_BATCH_SIZE,
        description="List of chat IDs to process",
    )


@router.post("/actions/batch-archive", response_model=BatchArchiveResult)
async def batch_archive_chats(
    payload: BatchChatIds,
    mgr: ChatManager = Depends(get_chat_manager),
    workspace=Depends(get_workspace),
):
    """Batch archive chats. Running chats are skipped."""
    tracker = workspace.task_tracker
    return await mgr.batch_archive(
        chat_ids=payload.chat_ids,
        get_status=tracker.get_status,
    )


@router.post("/actions/batch-unarchive", response_model=BatchArchiveResult)
async def batch_unarchive_chats(
    payload: BatchChatIds,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Batch unarchive chats."""
    return await mgr.batch_unarchive(chat_ids=payload.chat_ids)


@router.post("/{chat_id}/archive", response_model=ChatSpec)
async def archive_chat(
    chat_id: str,
    mgr: ChatManager = Depends(get_chat_manager),
    workspace=Depends(get_workspace),
):
    """Archive a single chat. Idempotent.

    Returns 409 if the chat is currently running.
    """
    status = await workspace.task_tracker.get_status(chat_id)
    try:
        result = await mgr.archive_chat(chat_id, check_status=status)
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail="Chat is currently in progress, cannot archive",
        ) from e
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )
    return result


@router.post("/{chat_id}/unarchive", response_model=ChatSpec)
async def unarchive_chat(
    chat_id: str,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Unarchive a single chat. Idempotent."""
    result = await mgr.unarchive_chat(chat_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )
    return result


# ----- Existing CRUD endpoints -----


@router.get("/{chat_id}", response_model=ChatHistory)
async def get_chat(
    chat_id: str,
    mgr: ChatManager = Depends(get_chat_manager),
    session: SafeJSONSession = Depends(get_session),
    workspace=Depends(get_workspace),
):
    """Get detailed information about a specific chat by UUID.

    Args:
        request: FastAPI request (for agent context)
        chat_id: Chat UUID
        mgr: Chat manager dependency
        session: SafeJSONSession dependency

    Returns:
        ChatHistory with messages and status (idle/running)

    Raises:
        HTTPException: If chat not found (404)
    """
    chat_spec = await mgr.get_chat(chat_id)
    if not chat_spec:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )

    state = await session.get_session_state_dict(
        chat_spec.session_id,
        chat_spec.user_id,
        chat_spec.channel,
    )
    backend = workspace.config.backend
    context = ((state.get("agent") or {}).get("state") or {}).get("context")
    if not context and backend != "qwenpaw":
        try:
            await workspace.harness_runtime.hydrate_session(
                backend=backend,
                session_id=chat_spec.session_id,
                user_id=chat_spec.user_id,
                channel=chat_spec.channel,
                settings=dict(workspace.config.backend_settings),
            )
            state = await session.get_session_state_dict(
                chat_spec.session_id,
                chat_spec.user_id,
                chat_spec.channel,
            )
        except Exception:
            logger.debug(
                "Third-party session recovery failed for %s",
                chat_spec.session_id,
                exc_info=True,
            )
    status = await workspace.task_tracker.get_status(chat_id)
    if not state:
        return ChatHistory(messages=[], status=status)

    agent_raw = state.get("agent", {})
    memories: list[Msg] = []

    state_raw = agent_raw.get("state")
    if isinstance(state_raw, dict):
        try:
            agent_state = AgentState.model_validate(state_raw)
            memories = list(agent_state.context)
        except Exception:
            logger.debug(
                "Failed to parse agent.state, falling back to legacy",
                exc_info=True,
            )

    # Legacy fallback: 1.x ``agent.memory`` format.
    if not memories:
        memory_raw = agent_raw.get("memory", {})
        if memory_raw:
            memories, _summary = parse_legacy_memory_state(memory_raw)

    messages = agentscope_msg_to_message(memories)
    return ChatHistory(messages=messages, status=status)


@router.put("/{chat_id}", response_model=ChatSpec)
async def update_chat(
    chat_id: str,
    spec: ChatUpdate,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Update an existing chat.

    Args:
        chat_id: Chat UUID
        spec: Partial chat update payload
        mgr: Chat manager dependency

    Returns:
        Updated chat spec

    Raises:
        HTTPException: If chat not found (404)
    """
    updated = await mgr.patch_chat(chat_id, spec)
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )
    return updated


class ProjectDirRequest(BaseModel):
    """Payload for setting a chat's project-directory override."""

    model_config = ConfigDict(extra="forbid")

    project_dir: str = Field(
        ...,
        min_length=1,
        description="Absolute path to use for this chat",
    )


class ProjectDirResponse(BaseModel):
    """Effective project directory for a chat, plus where it came from."""

    project_dir: str = Field(description="Effective project directory")
    source: str = Field(
        description=(
            "Provenance: 'session' (this chat overrides), 'agent' (agent "
            "default), or 'workspace_fallback' (nothing configured)"
        ),
    )
    agent_project_dir: Optional[str] = Field(
        default=None,
        description="The agent-level default, for showing inheritance",
    )
    exists: bool = Field(
        description=(
            "Whether the path exists. False is surfaced rather than "
            "silently corrected so the UI can flag it as unavailable."
        ),
    )


async def _resolve_chat_project_dir(
    request: Request,
    chat_id: str,
    mgr: ChatManager,
) -> ProjectDirResponse:
    """Build the project-dir view for one chat."""
    from ...config.config import load_agent_config
    from ...config.project_dir import (
        agent_project_dir_from_config,
        resolve_effective_project_dir,
        session_project_dir_from_meta,
    )

    chat = await mgr.get_chat(chat_id)
    if chat is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )

    workspace = await get_workspace(request)
    agent_project_dir = None
    try:
        agent_project_dir = agent_project_dir_from_config(
            load_agent_config(workspace.agent_id),
        )
    except Exception:
        logger.debug("Could not load agent config", exc_info=True)

    resolved = resolve_effective_project_dir(
        workspace_dir=workspace.workspace_dir,
        agent_project_dir=agent_project_dir,
        session_project_dir=session_project_dir_from_meta(chat.meta),
    )
    return ProjectDirResponse(
        project_dir=str(resolved.path),
        source=resolved.source,
        agent_project_dir=agent_project_dir,
        exists=resolved.exists,
    )


@router.get("/{chat_id}/project-dir", response_model=ProjectDirResponse)
async def get_chat_project_dir(
    request: Request,
    chat_id: str,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Return this chat's effective project directory and its provenance."""
    return await _resolve_chat_project_dir(request, chat_id, mgr)


@router.put("/{chat_id}/project-dir", response_model=ProjectDirResponse)
async def set_chat_project_dir(
    request: Request,
    chat_id: str,
    payload: ProjectDirRequest,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Bind this chat to a project directory.

    The override is persisted server-side, so it survives a page reload or
    a different browser. It takes effect on the **next** turn — an
    in-flight turn keeps the directory it started with.

    A path that does not exist is rejected here (rather than stored and
    flagged) because this endpoint is the point where the user picks it and
    can still correct the mistake.
    """
    from ...config.project_dir import normalize_project_dir

    normalized = normalize_project_dir(payload.project_dir)
    if normalized is None:
        raise HTTPException(
            status_code=422,
            detail="project_dir must not be blank",
        )
    if not normalized.is_dir():
        raise HTTPException(
            status_code=422,
            detail=f"Not a directory: {normalized}",
        )

    updated = await mgr.set_session_project_dir(chat_id, str(normalized))
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )
    return await _resolve_chat_project_dir(request, chat_id, mgr)


@router.delete("/{chat_id}/project-dir", response_model=ProjectDirResponse)
async def clear_chat_project_dir(
    request: Request,
    chat_id: str,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Drop this chat's override so it inherits the agent default again."""
    updated = await mgr.set_session_project_dir(chat_id, None)
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )
    return await _resolve_chat_project_dir(request, chat_id, mgr)


@router.delete("/{chat_id}", response_model=dict)
async def delete_chat(
    chat_id: str,
    mgr: ChatManager = Depends(get_chat_manager),
):
    """Delete a chat by UUID.

    Note: This only deletes the chat spec (UUID mapping).
    JSONSession state is NOT deleted.

    Args:
        chat_id: Chat UUID
        mgr: Chat manager dependency

    Returns:
        True if deleted, False if failed

    Raises:
        HTTPException: If chat not found (404)
    """
    deleted = await mgr.delete_chats(chat_ids=[chat_id])
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Chat not found: {chat_id}",
        )
    return {"deleted": True}
