# -*- coding: utf-8 -*-
"""Run a cron job's preprocess batch and shape the result for its consumer.

The batch runs before every fire, deterministically, with no model involved
in deciding to run it — that decision is what a preprocess exists to remove.
Where the result goes depends on the task type:

* ``agent`` — :func:`inject_preprocess_block` appends the call and its
  result to the user prompt, so the model starts holding the data.
* ``text`` — the rendered prose is delivered to the user as the message
  body; no LLM is involved at any point.

Failures never raise out of here. The contract with the executor is
"continue and report", so a broken script degrades to a result carrying
``ok=False`` plus text explaining what happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from .models import CronJobSpec, PreprocessSpec

logger = logging.getLogger(__name__)

#: Ceiling on the batch summary injected into a prompt or sent to a user.
#: One ``execute_shell_command`` returning a large file would otherwise
#: blow up the prompt (and the channel message) on every single tick.
MAX_INJECTED_RESULT_CHARS = 8000

#: Control-flow pseudo-tools. They produce no output a human wants to read,
#: so the prose renderer skips them.
_CONTROL_FLOW_TOOLS = frozenset({"label", "goto", "set_var"})

PreprocessStatus = Literal["ok", "failed", "timeout"]


class PreprocessResult(BaseModel):
    """Outcome of one preprocess run, rendered for both consumers."""

    ok: bool
    status: PreprocessStatus
    #: Human-readable label of what ran, for logs and the trace.
    script_label: str = ""
    #: The call, rendered for prompt injection so the model can see what
    #: was already done and does not repeat it.
    call_text: str = ""
    #: ``run_tool_batch``'s JSON summary, truncated. For the model.
    result_json: str = ""
    #: Prose extracted from the results. For a human.
    user_text: str = ""
    error: str = ""
    duration_ms: int = 0

    @property
    def failed(self) -> bool:
        return not self.ok


# ---------------------------------------------------------------------------
# Script resolution
# ---------------------------------------------------------------------------


def get_batch_pool_dir() -> Path:
    """Directory holding shared batch scripts."""
    from ...constant import WORKING_DIR

    return Path(WORKING_DIR) / "tool_batches"


def resolve_batch_script(name: str) -> Optional[Path]:
    """Resolve a pool script name to a file, or ``None`` if absent.

    Refuses anything with a path separator: a job spec must not be able to
    point the runner at an arbitrary file on disk.
    """
    candidate = (name or "").strip()
    if not candidate or "/" in candidate or "\\" in candidate:
        return None
    if candidate in {".", ".."}:
        return None
    if not candidate.endswith(".json"):
        candidate = f"{candidate}.json"

    pool = get_batch_pool_dir()
    path = (pool / candidate).resolve()
    if not path.is_relative_to(pool.resolve()):
        return None
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _truncate(text: str) -> str:
    if len(text) <= MAX_INJECTED_RESULT_CHARS:
        return text
    omitted = len(text) - MAX_INJECTED_RESULT_CHARS
    return (
        text[:MAX_INJECTED_RESULT_CHARS]
        + f"\n… [truncated, {omitted} chars omitted]"
    )


def _step_prose(entry: dict[str, Any]) -> str:
    """Best-effort human-readable text for one step result."""
    if entry.get("tool_name") in _CONTROL_FLOW_TOOLS:
        return ""
    for key in ("text", "error"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if "value" in entry:
        return json.dumps(entry["value"], ensure_ascii=False, default=str)
    return ""


def render_user_text(payload: dict[str, Any]) -> str:
    """Turn a batch summary into something worth sending to a person.

    Handles both summary shapes ``run_tool_batch`` produces: ``last_only``
    collapses to ``last_step_result``, otherwise there is a ``results``
    list. Falls back to the raw JSON only when nothing readable is found,
    which is better than sending an empty message.
    """
    if not isinstance(payload, dict):
        return str(payload)

    parts: list[str] = []
    last = payload.get("last_step_result")
    if isinstance(last, dict):
        prose = _step_prose(last)
        if prose:
            parts.append(prose)
    else:
        for entry in payload.get("results") or []:
            if isinstance(entry, dict):
                prose = _step_prose(entry)
                if prose:
                    parts.append(prose)

    if not payload.get("ok", True):
        error = payload.get("error")
        parts.append(
            f"⚠️ 采集未全部成功：{error}" if error else "⚠️ 采集未全部成功。",
        )

    if not parts:
        # Nothing quotable — hand over the summary rather than nothing.
        return _truncate(json.dumps(payload, ensure_ascii=False, default=str))
    return _truncate("\n\n".join(parts))


def _render_call_text(spec: PreprocessSpec, script_label: str) -> str:
    """Describe the call that already happened, for the model."""
    return json.dumps(
        {
            "tool_name": "run_tool_batch",
            "arguments": {
                "file_path": script_label,
                "args": spec.args,
                "last_only": spec.last_only,
            },
        },
        ensure_ascii=False,
        default=str,
    )


def build_prompt_block(result: PreprocessResult) -> str:
    """Render the block appended to the user prompt.

    Two instructions carry real weight here. "Do not run it again" exists
    because the script is a real file the agent can see and
    ``run_tool_batch`` is in its toolkit — a helpful model will absolutely
    retry it. And on failure, "do not invent the missing data" exists
    because a model told only that collection failed will otherwise fill
    the gap with plausible numbers.
    """
    if result.ok:
        return (
            "<preprocess_result>\n"
            "A data-collection script was executed automatically before "
            "this task.\n"
            "It has ALREADY RUN — do not call run_tool_batch again.\n\n"
            f"Call:\n{result.call_text}\n\n"
            f"Result:\n{result.result_json}\n"
            "</preprocess_result>"
        )
    return (
        "<preprocess_result>\n"
        "A data-collection script was executed automatically before this "
        "task, and it FAILED.\n"
        "Do not retry it. Continue with the information you have, and say "
        "plainly in your reply that data collection failed — do not invent "
        "or assume the missing data.\n\n"
        f"Call:\n{result.call_text}\n\n"
        f"Error:\n{result.error or 'unknown error'}\n"
        "</preprocess_result>"
    )


def inject_preprocess_block(
    request_input: Any,
    result: PreprocessResult,
) -> Any:
    """Append the preprocess block to the last user message.

    Appends to the existing last user turn rather than pushing a new one:
    two consecutive user messages are something several formatters
    normalise or reject outright.

    Tolerates the shapes ``CronJobRequest.input`` actually allows — it is
    ``Optional[Any]``, and the console's drawer will happily produce a bare
    string from ``JSON.parse('"hi"')``.
    """
    block = build_prompt_block(result)
    text_block = {"type": "text", "text": block}

    if isinstance(request_input, str):
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": request_input},
                    text_block,
                ],
            },
        ]

    if not isinstance(request_input, list) or not request_input:
        return [{"role": "user", "content": [text_block]}]

    messages = [dict(m) if isinstance(m, dict) else m for m in request_input]
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            message["content"] = [*content, text_block]
        elif isinstance(content, str):
            message["content"] = [
                {"type": "text", "text": content},
                text_block,
            ]
        else:
            message["content"] = [text_block]
        return messages

    # No user turn to attach to; add one rather than losing the data.
    return [*messages, {"role": "user", "content": [text_block]}]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _failure(
    spec: PreprocessSpec,
    script_label: str,
    error: str,
    status: PreprocessStatus = "failed",
    duration_ms: int = 0,
) -> PreprocessResult:
    return PreprocessResult(
        ok=False,
        status=status,
        script_label=script_label,
        call_text=_render_call_text(spec, script_label),
        result_json=json.dumps({"ok": False, "error": error}),
        user_text=f"⚠️ 数据采集失败：{error}",
        error=error,
        duration_ms=duration_ms,
    )


async def run_preprocess(
    *,
    workspace: Any,
    job: CronJobSpec,
    session_id: str,
    user_id: str,
    channel: str,
) -> Optional[PreprocessResult]:
    """Run the job's preprocess batch, if it has one.

    Returns ``None`` when there is nothing to run, so the executor can tell
    "no preprocess configured" from "preprocess ran and failed".
    """
    if not job.has_preprocess:
        return None
    spec = job.preprocess
    assert spec is not None  # has_preprocess guarantees it

    from ...runtime.builder import AgentBuilder
    from ...security.tool_guard.execution_level import ToolExecutionLevel
    from ...utils.io_utils import staged_dir

    agent_id = getattr(workspace, "agent_id", None) or "default"
    started = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    # `${args.*}` is only substituted for file-backed batches, so inline
    # actions are staged to a file rather than passed through as a list —
    # one code path, and args behave identically either way.
    with staged_dir("preprocess", prefix="qwenpaw_cron_pre_") as stage:
        script_path: Path
        if spec.script:
            script_label = spec.script
            resolved = resolve_batch_script(spec.script)
            if resolved is None:
                return _failure(
                    spec,
                    script_label,
                    f"batch script not found in pool: {spec.script}",
                    duration_ms=_elapsed_ms(),
                )
            script_path = resolved
        else:
            script_label = f"<inline:{len(spec.actions or [])} steps>"
            try:
                stage.mkdir(parents=True, exist_ok=True)
                script_path = stage / "inline.json"
                script_path.write_text(
                    json.dumps({"actions": spec.actions}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                return _failure(
                    spec,
                    script_label,
                    f"could not stage inline actions: {exc}",
                    duration_ms=_elapsed_ms(),
                )

        request_context = {
            "source": "cron",
            "cron_job_id": job.id or "",
            # Lets an audit trail tell the deterministic collection phase
            # apart from the model's own tool calls.
            "cron_phase": "preprocess",
            "session_id": session_id,
            "root_session_id": session_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "channel": channel,
            "approval_level": (
                ToolExecutionLevel.AUTO.value
                if job.runtime.tool_safety
                else ToolExecutionLevel.OFF.value
            ),
        }

        try:
            toolkit = await AgentBuilder(
                app_services=getattr(workspace, "app_services", None),
            ).build_standalone_toolkit(
                workspace=workspace,
                request_context=request_context,
                agent_id=agent_id,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "cron preprocess: toolkit build failed job_id=%s error=%s",
                job.id,
                repr(exc),
            )
            return _failure(
                spec,
                script_label,
                f"could not prepare tools: {exc}",
                duration_ms=_elapsed_ms(),
            )

        return await _execute_batch(
            spec=spec,
            job=job,
            script_path=script_path,
            script_label=script_label,
            toolkit=toolkit,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            agent_id=agent_id,
            workspace_dir=getattr(workspace, "workspace_dir", None),
            started=started,
        )


async def _execute_batch(
    *,
    spec: PreprocessSpec,
    job: CronJobSpec,
    script_path: Path,
    script_label: str,
    toolkit: Any,
    session_id: str,
    user_id: str,
    channel: str,
    agent_id: str,
    workspace_dir: Path | None,
    started: float,
) -> PreprocessResult:
    """Run the batch under a scoped tool context, bounded by a timeout."""
    from agentscope.state import AgentState

    from ...runtime.tool_context import (
        config_derived_tool_values,
        scoped_tool_context,
    )

    rtb = _run_tool_batch_module()
    config_values = config_derived_tool_values(agent_id)

    async def _run() -> Any:
        with scoped_tool_context(
            toolkit=toolkit,
            agent_state=AgentState(session_id=session_id),
            workspace_dir=workspace_dir,
            session_id=session_id,
            root_session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            channel=channel,
            approval_route={
                "root_session_id": session_id,
                "user_id": user_id,
                "channel": channel,
                "channel_meta": None,
            },
            config_values=config_values,
        ):
            return await rtb.run_tool_batch(
                file_path=str(script_path),
                args=spec.args or None,
                stop_on_error=spec.stop_on_error,
                last_only=spec.last_only,
                maxstep=spec.maxstep,
            )

    # An explicit task, not `wait_for(coro)`: since 3.12 `wait_for` awaits
    # the awaitable directly instead of wrapping it in a task, so a bare
    # coroutine would no longer get its own context copy — and this project
    # supports 3.11 through 3.13.
    task = asyncio.create_task(_run(), name=f"cron-preprocess-{job.id}")
    try:
        chunk = await asyncio.wait_for(task, timeout=spec.timeout_seconds)
    except asyncio.TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        logger.warning(
            "cron preprocess timed out: job_id=%s script=%s timeout=%ss",
            job.id,
            script_label,
            spec.timeout_seconds,
        )
        return _failure(
            spec,
            script_label,
            f"preprocess timed out after {spec.timeout_seconds}s",
            status="timeout",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "cron preprocess raised: job_id=%s script=%s error=%s",
            job.id,
            script_label,
            repr(exc),
        )
        return _failure(
            spec,
            script_label,
            f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    return _normalize(
        spec=spec,
        script_label=script_label,
        chunk=chunk,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _run_tool_batch_module() -> Any:
    """Import the module, not the same-named function it exports.

    ``agents/tools/__init__.py`` rebinds ``run_tool_batch`` to the function,
    so ``from qwenpaw.agents.tools import run_tool_batch`` yields a callable
    and hides the module's helpers.
    """
    from importlib import import_module

    return import_module("qwenpaw.agents.tools.run_tool_batch")


def _normalize(
    *,
    spec: PreprocessSpec,
    script_label: str,
    chunk: Any,
    duration_ms: int,
) -> PreprocessResult:
    """Turn a ToolChunk into a PreprocessResult."""
    rtb = _run_tool_batch_module()
    raw = rtb._extract_text(chunk)  # pylint: disable=protected-access

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        payload = {"ok": True, "text": raw}
    if not isinstance(payload, dict):
        payload = {"ok": True, "value": payload}

    ok = bool(payload.get("ok", True))
    error = str(payload.get("error") or "")
    if not ok and not error:
        error = "batch reported failure without an error message"

    return PreprocessResult(
        ok=ok,
        status="ok" if ok else "failed",
        script_label=script_label,
        call_text=_render_call_text(spec, script_label),
        result_json=_truncate(raw),
        user_text=render_user_text(payload),
        error=error,
        duration_ms=duration_ms,
    )


__all__ = [
    "MAX_INJECTED_RESULT_CHARS",
    "PreprocessResult",
    "build_prompt_block",
    "get_batch_pool_dir",
    "inject_preprocess_block",
    "render_user_text",
    "resolve_batch_script",
    "run_preprocess",
]
