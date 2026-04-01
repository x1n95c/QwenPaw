# -*- coding: utf-8 -*-
"""Built-in tool for delegating tasks to external agent runners via ACP protocol.

This module provides the spawn_agent tool which uses ACP protocol with
session persistence and permission handling.
"""

import logging
from pathlib import Path
from typing import Any, List, Literal, Optional

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from ...config import load_config
from ...config.context import get_current_workspace_dir
from ...constant import WORKING_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def _response_text(text: str) -> ToolResponse:
    """Create a text ToolResponse."""
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _validate_spawn_agent_inputs(
    task_text: str,
    runner_name: str,
    cwd_text: str,
    timeout: int,
) -> str | None:
    """Return a user-facing validation error for invalid tool inputs."""
    if not task_text:
        return "Error: task is empty."
    if not runner_name:
        return "Error: runner is empty."
    if not cwd_text:
        return "Error: cwd is empty."
    if timeout <= 0:
        return "Error: timeout must be greater than 0."
    return None


def _current_workspace_dir() -> Path:
    return (get_current_workspace_dir() or Path(WORKING_DIR)).expanduser()


def _resolve_execution_cwd(
    cwd: str,
    workspace_dir: Path,
) -> Path:
    """Resolve the execution working directory."""
    candidate = Path(cwd.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_dir / candidate
    return candidate.resolve()


def _get_acp_service_and_config() -> tuple[Any, Any]:
    """Get or initialize ACP service and config.

    Returns:
        Tuple of (service, acp_config).
    """
    from ..acp import get_acp_service, init_acp_service
    from ..acp.config import ACPConfig

    config = load_config()
    acp_config = getattr(config, "acp", None)
    if acp_config is None:
        acp_config = ACPConfig(enabled=True)

    service = get_acp_service()
    if service is None:
        service = init_acp_service(acp_config)

    return service, acp_config


# ---------------------------------------------------------------------------
# ACP Session Management Helpers
# ---------------------------------------------------------------------------


async def _handle_list_sessions(runner: str) -> ToolResponse:
    """Handle the list_sessions action."""
    try:
        service, acp_config = _get_acp_service_and_config()

        if not acp_config.enabled:
            return _response_text(
                "ACP is disabled in config. Set 'acp.enabled: true' to enable."
            )

        sessions = await service.list_sessions(harness=runner)

        if not sessions:
            return _response_text(f"No active ACP sessions for runner '{runner}'.")

        lines = [f"Active ACP sessions for '{runner}':", ""]
        for i, session in enumerate(sessions, 1):
            status = "🟢 active" if session.get("has_active_runtime") else "⚪ inactive"
            lines.append(
                f"{i}. Session ID: {session.get('acp_session_id', 'unknown')}\n"
                f"   Chat: {session.get('chat_id', 'unknown')}\n"
                f"   CWD: {session.get('cwd', 'unknown')}\n"
                f"   Status: {status}\n"
                f"   Updated: {session.get('updated_at', 'unknown')}"
            )

        return _response_text("\n".join(lines))

    except Exception as e:
        logger.exception("Failed to list ACP sessions")
        return _response_text(f"Error listing ACP sessions: {e}")


async def _handle_session_info(session_id: str) -> ToolResponse:
    """Handle the session_info action."""
    try:
        service, _ = _get_acp_service_and_config()

        session = await service.load_session_by_acp_id(session_id)

        if session is None:
            return _response_text(f"Session '{session_id}' not found.")

        runtime_status = "active" if (
            session.runtime is not None
            and session.runtime.transport.is_running()
        ) else "inactive"

        info = [
            f"ACP Session: {session.acp_session_id}",
            f"Harness: {session.harness}",
            f"Chat ID: {session.chat_id}",
            f"Working Directory: {session.cwd}",
            f"Keep Session: {session.keep_session}",
            f"Runtime Status: {runtime_status}",
            f"Updated: {session.updated_at.isoformat()}",
        ]

        if session.capabilities:
            info.append(f"Capabilities: {session.capabilities}")

        return _response_text("\n".join(info))

    except Exception as e:
        logger.exception("Failed to get ACP session info")
        return _response_text(f"Error getting session info: {e}")


async def _handle_close_session(
    session_id: str,
    runner: str,
    chat_id: str,
) -> ToolResponse:
    """Handle the close_session action."""
    try:
        service, _ = _get_acp_service_and_config()

        # Resolve the real (chat_id, harness) from the acp_session_id
        resolved_chat_id = chat_id
        resolved_runner = runner
        if session_id:
            existing = await service.load_session_by_acp_id(session_id)
            if existing is not None:
                resolved_chat_id = existing.chat_id
                resolved_runner = existing.harness

        await service.close_chat_session(
            chat_id=resolved_chat_id,
            harness=resolved_runner,
            reason="user requested close",
        )

        return _response_text(f"Session '{session_id}' closed successfully.")

    except Exception as e:
        logger.exception("Failed to close ACP session")
        return _response_text(f"Error closing session: {e}")


# ---------------------------------------------------------------------------
# Main Tool Function
# ---------------------------------------------------------------------------


async def spawn_agent(
    action: Literal["run", "list_sessions", "session_info", "close_session"] = "run",
    task: str = "",
    runner: str = "",
    cwd: str = "",
    timeout: int = 900,
    session_id: Optional[str] = None,
    keep_session: bool = True,
    require_approval: bool = True,
    permission_decision: Optional[str] = None,
    chat_id: str = "tool_call",
) -> ToolResponse:
    """Delegate tasks to external agent runners via ACP protocol.

    This tool supports multiple actions through the `action` parameter:

    - **"run"** (default): Execute a task with an external agent runner
    - **"list_sessions"**: List active ACP sessions for a runner
    - **"session_info"**: Get detailed info about a specific session
    - **"close_session"**: Close and cleanup a session

    **Session Management:**
    - Use `keep_session=True` to keep sessions alive for later use
    - Use `session_id` to resume previous sessions
    - Use `action="list_sessions"` to find available sessions
    - Use `action="close_session"` to cleanup when done

    Args:
        action (`str`, defaults to `"run"`):
            The action to perform. Options:
            - `"run"`: Execute a task (default)
            - `"list_sessions"`: List active sessions for a runner
            - `"session_info"`: Get info about a specific session
            - `"close_session"`: Close a session

        task (`str`, defaults to `""`):
            The task description to send to the external agent.
            Required when `action="run"`.
            Example: "Analyze the code in src/main.py and suggest improvements"

        runner (`str`, defaults to `""`):
            The external agent runner to use.
            Required for `action="run"` and `action="list_sessions"`.
            Common runners: "opencode", "qwen", "gemini", "claude".
            Example: "qwen"

        cwd (`str`, defaults to `""`):
            The working directory for task execution.
            Required when `action="run"`.
            Can be absolute or relative to the current workspace.
            Example: "/home/user/project" or "./src"

        timeout (`int`, defaults to `900`):
            Maximum execution time in seconds for `action="run"`.
            Default is 15 minutes (900 seconds).

        session_id (`Optional[str]`, defaults to `None`):
            ACP session ID. Used for:
            - `action="run"`: Resume a previous session
            - `action="session_info"`: Query this session
            - `action="close_session"`: Close this session

        keep_session (`bool`, defaults to `True`):
            Whether to keep the session alive after execution.
            Only used when `action="run"`.

        require_approval (`bool`, defaults to `True`):
            Whether to require user approval for dangerous operations.
            Only used when `action="run"`.

        permission_decision (`Optional[str]`, defaults to `None`):
            Resume a suspended session after a permission request.
            Only used when `action="run"` with a suspended `session_id`.

            Accepted values:
            - Exact `optionId` from the permission message (preferred)
            - Shorthand: "allow"/"approve"/"yes" → allow; "deny"/"reject"/"no" → reject

        chat_id (`str`, defaults to `"tool_call"`):
            The chat ID associated with the session.
            Only used when `action="close_session"` as a fallback.

    Returns:
        `ToolResponse`: The result of the operation.

    Examples:
        ```python
        # Execute a task (default action)
        result = await spawn_agent(
            task="Implement the new feature",
            runner="qwen",
            cwd="./project",
        )

        # List active sessions
        await spawn_agent(action="list_sessions", runner="qwen")

        # Get session info
        await spawn_agent(action="session_info", session_id="sess_abc123")

        # Close a session
        await spawn_agent(
            action="close_session",
            session_id="sess_abc123",
            runner="qwen",
        )

        # Resume a previous session
        await spawn_agent(
            task="Continue with tests",
            runner="qwen",
            cwd="./project",
            session_id="sess_abc123",
        )
        ```
    """
    # --- Dispatch to action handlers ---
    action_normalized = (action or "run").strip().lower()

    if action_normalized == "list_sessions":
        runner_name = (runner or "").strip()
        if not runner_name:
            return _response_text("Error: runner is required for list_sessions action.")
        return await _handle_list_sessions(runner_name)

    if action_normalized == "session_info":
        session_id_text = (session_id or "").strip()
        if not session_id_text:
            return _response_text("Error: session_id is required for session_info action.")
        return await _handle_session_info(session_id_text)

    if action_normalized == "close_session":
        session_id_text = (session_id or "").strip()
        if not session_id_text:
            return _response_text("Error: session_id is required for close_session action.")
        return await _handle_close_session(
            session_id=session_id_text,
            runner=(runner or "").strip(),
            chat_id=(chat_id or "tool_call").strip(),
        )

    # --- Default: action="run" ---
    task_text = (task or "").strip()
    runner_name = (runner or "").strip()
    cwd_text = (cwd or "").strip()

    # Validate inputs for run action
    validation_error = _validate_spawn_agent_inputs(
        task_text,
        runner_name,
        cwd_text,
        timeout,
    )
    if validation_error is not None:
        return _response_text(validation_error)

    # Resolve execution cwd
    workspace_dir = _current_workspace_dir()
    execution_cwd = _resolve_execution_cwd(cwd_text, workspace_dir)

    try:
        service, acp_config = _get_acp_service_and_config()

        if not acp_config.enabled:
            return _response_text(
                "ACP mode is disabled in config. Set 'acp.enabled: true' to enable."
            )

        # Get real session context from contextvar
        from ...app.agent_context import get_request_context
        request_ctx = get_request_context() or {}
        real_session_id = request_ctx.get("session_id", "")
        real_user_id = request_ctx.get("user_id", "")
        real_channel = request_ctx.get("channel", "")

        chat_id_for_run = real_session_id if real_session_id else "tool_call"

        # Collect results
        result_parts: List[str] = []

        async def on_message(message: Any, is_last: bool) -> None:
            """Handle streaming messages from ACP."""
            if isinstance(message, dict):
                if message.get("type") == "text":
                    text = message.get("text", "")
                    if text:
                        result_parts.append(text)

        # --- Permission resume path ---
        if permission_decision is not None:
            if not session_id:
                return _response_text(
                    "Error: session_id is required when using permission_decision."
                )
            run_result = await service.resume_permission(
                acp_session_id=session_id,
                option_id=permission_decision,
                on_message=on_message,
            )
        else:
            # --- Normal execution path ---
            prompt_blocks = [{"type": "text", "text": task_text}]
            run_result = await service.run_turn(
                chat_id=chat_id_for_run,
                session_id=real_session_id if real_session_id else "spawn_agent_tool",
                user_id=real_user_id if real_user_id else "system",
                channel=real_channel if real_channel else "tool",
                harness=runner_name,
                prompt_blocks=prompt_blocks,
                cwd=str(execution_cwd),
                keep_session=keep_session,
                preapproved=not require_approval,
                existing_session_id=session_id,
                on_message=on_message,
            )

        session_id_result = run_result.session_id

        # --- Permission suspended: present options to user ---
        if run_result.suspended_permission is not None:
            sp = run_result.suspended_permission
            header = [
                f"spawn_agent runner: {runner_name}",
                f"working directory: {execution_cwd}",
                f"session_id: {session_id_result}",
                "",
            ]
            perm_message = sp.format_chat_message()
            option_ids = [
                opt.get("optionId") or opt.get("id", "?")
                for opt in sp.options
                if isinstance(opt, dict)
            ]
            resume_hint = (
                f"\nTo resume, call spawn_agent again with:\n"
                f"  session_id='{session_id_result}', "
                f"permission_decision='<optionId>'\n"
                f"  Available optionIds: {option_ids}"
            )
            return _response_text(
                "\n".join(header) + perm_message + resume_hint
            )

        # --- Normal completion ---
        header = [
            f"spawn_agent runner: {runner_name}",
            f"working directory: {execution_cwd}",
            f"session_id: {session_id_result}",
            f"keep_session: {keep_session}",
            "",
        ]

        output = "\n".join(result_parts).strip()
        if output:
            return _response_text("\n".join(header + ["Output:", output]))
        else:
            return _response_text("\n".join(header + ["(No output)"]))

    except ImportError as e:
        return _response_text(f"ACP mode not available: {e}.")
    except Exception as e:
        logger.exception("ACP execution failed")
        return _response_text(f"ACP execution error: {e}")