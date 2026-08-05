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

from pydantic import BaseModel, Field

from .models import CronJobSpec, PreprocessSpec, PreprocessStepSpec

logger = logging.getLogger(__name__)

#: Ceiling on the batch summary injected into a prompt or sent to a user.
#: One ``execute_shell_command`` returning a large file would otherwise
#: blow up the prompt (and the channel message) on every single tick.
MAX_INJECTED_RESULT_CHARS = 8000

#: Control-flow pseudo-tools. They produce no output a human wants to read,
#: so the prose renderer skips them.
_CONTROL_FLOW_TOOLS = frozenset({"label", "goto", "set_var"})

PreprocessStatus = Literal["ok", "failed", "timeout"]


class PreprocessStepResult(BaseModel):
    """Outcome of one script in the chain, rendered for both consumers."""

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


class PreprocessResult(BaseModel):
    """Outcome of a whole preprocess chain.

    Per-script results are kept separate rather than merged into one blob:
    a consumer has to be able to say *which* collection step produced a
    value, and more importantly which one failed — one merged ``error``
    string cannot express "script 2 of 3 failed".
    """

    ok: bool
    status: PreprocessStatus
    steps: list[PreprocessStepResult] = Field(default_factory=list)
    duration_ms: int = 0

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def script_label(self) -> str:
        """Every label in run order, for logs and the trace."""
        return " → ".join(step.script_label for step in self.steps)

    @property
    def error(self) -> str:
        """Only the failures, each named so the culprit is identifiable."""
        named = len(self.steps) > 1
        parts = [
            f"{step.script_label}: {step.error}" if named else step.error
            for step in self.steps
            if step.failed and step.error
        ]
        return "; ".join(parts)

    @property
    def user_text(self) -> str:
        """Prose for a human, in run order.

        Labelled per script only when there is more than one: on a
        single-script chain the label is noise in what is often the whole
        message body.
        """
        blocks: list[str] = []
        for step in self.steps:
            text = (step.user_text or "").strip()
            if not text:
                continue
            if len(self.steps) > 1:
                blocks.append(f"【{step.script_label}】\n{text}")
            else:
                blocks.append(text)
        return "\n\n".join(blocks)


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


def _render_call_text(
    spec: PreprocessSpec,
    step: PreprocessStepSpec,
    script_label: str,
) -> str:
    """Describe the call that already happened, for the model."""
    return json.dumps(
        {
            "tool_name": "run_tool_batch",
            "arguments": {
                "file_path": script_label,
                "args": step.args,
                "last_only": spec.last_only,
            },
        },
        ensure_ascii=False,
        default=str,
    )


def build_prompt_block(result: PreprocessResult) -> str:
    """Render the block appended to the user prompt.

    Two instructions carry real weight here. "Do not run them again" exists
    because the scripts are real files the agent can see and
    ``run_tool_batch`` is in its toolkit — a helpful model will absolutely
    retry them. And for a failure, "do not invent the missing data" exists
    because a model told only that collection failed will otherwise fill
    the gap with plausible numbers.

    With several scripts the two states can coexist, so each script gets
    its own section and the header states the mixed outcome plainly rather
    than picking one story.
    """
    total = len(result.steps)
    failed = sum(1 for step in result.steps if step.failed)

    if failed == 0:
        header = (
            f"{total} data-collection script(s) were executed automatically "
            "before this task.\n"
            "They have ALREADY RUN — do not call run_tool_batch again."
        )
    elif failed == total:
        header = (
            f"{total} data-collection script(s) were executed automatically "
            "before this task, and they ALL FAILED.\n"
            "Do not retry them. Continue with the information you have, and "
            "say plainly in your reply that data collection failed — do not "
            "invent or assume the missing data."
        )
    else:
        header = (
            f"{total} data-collection script(s) were executed automatically "
            f"before this task; {failed} of them FAILED.\n"
            "They have ALREADY RUN — do not call run_tool_batch again and do "
            "not retry the failed ones. Use the results that succeeded, say "
            "plainly which data could not be collected, and do not invent or "
            "assume the missing values."
        )

    sections: list[str] = []
    for index, step in enumerate(result.steps, start=1):
        prefix = f"[{index}/{total}] {step.script_label}" if total > 1 else ""
        body = (
            f"Result:\n{step.result_json}"
            if step.ok
            else f"Error:\n{step.error or 'unknown error'}"
        )
        sections.append(
            (f"{prefix}\n" if prefix else "")
            + f"Call:\n{step.call_text}\n\n"
            + body,
        )

    joined = "\n\n".join(sections)
    return f"<preprocess_result>\n{header}\n\n{joined}\n</preprocess_result>"


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


def _step_failure(
    spec: PreprocessSpec,
    step: PreprocessStepSpec,
    error: str,
    status: PreprocessStatus = "failed",
    duration_ms: int = 0,
) -> PreprocessStepResult:
    label = step.label
    return PreprocessStepResult(
        ok=False,
        status=status,
        script_label=label,
        call_text=_render_call_text(spec, step, label),
        result_json=json.dumps({"ok": False, "error": error}),
        user_text=f"⚠️ 数据采集失败：{error}",
        error=error,
        duration_ms=duration_ms,
    )


def _collect(
    steps: list[PreprocessStepResult],
    started: float,
) -> PreprocessResult:
    """Fold per-script results into the chain result."""
    ok = all(step.ok for step in steps)
    if ok:
        status: PreprocessStatus = "ok"
    elif any(step.status == "timeout" for step in steps):
        # Surfaced ahead of "failed" because a timeout is the one outcome
        # that says something about the *budget* rather than the script.
        status = "timeout"
    else:
        status = "failed"
    return PreprocessResult(
        ok=ok,
        status=status,
        steps=steps,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


async def run_preprocess(
    *,
    workspace: Any,
    job: CronJobSpec,
    session_id: str,
    user_id: str,
    channel: str,
) -> Optional[PreprocessResult]:
    """Run the job's preprocess chain, if it has one.

    Returns ``None`` when there is nothing to run, so the executor can tell
    "no preprocess configured" from "preprocess ran and failed".

    Scripts run in order under one shared wall-clock budget and one shared
    toolkit. A failing script does not stop the ones after it — the
    contract with the executor is "continue and report", and
    ``on_failure="abort"`` is applied by the executor once it can see the
    whole picture.
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

    # Built once for the whole chain: `set_governor` mutates workspace-level
    # shared state, so rebuilding per script would swap the governor out
    # from under any concurrent request several times per run.
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
        error = f"could not prepare tools: {exc}"
        elapsed = _elapsed_ms()
        return _collect(
            [
                _step_failure(spec, step, error, duration_ms=elapsed)
                for step in spec.steps
            ],
            started,
        )

    results: list[PreprocessStepResult] = []
    # `${args.*}` is only substituted for file-backed batches, so inline
    # actions are staged to a file rather than passed through as a list —
    # one code path, and args behave identically either way.
    with staged_dir("preprocess", prefix="qwenpaw_cron_pre_") as stage:
        for index, step in enumerate(spec.steps):
            remaining = spec.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                # The budget belongs to the chain, so a script that never
                # got a chance to start is reported rather than skipped
                # silently — otherwise its absence from the output looks
                # like it returned nothing.
                results.append(
                    _step_failure(
                        spec,
                        step,
                        "preprocess budget of "
                        f"{spec.timeout_seconds}s was exhausted before this "
                        "script started",
                        status="timeout",
                    ),
                )
                continue

            script_path = _resolve_step_path(step, stage, index)
            if isinstance(script_path, str):
                results.append(
                    _step_failure(spec, step, script_path),
                )
                continue

            results.append(
                await _execute_step(
                    spec=spec,
                    step=step,
                    job=job,
                    script_path=script_path,
                    toolkit=toolkit,
                    session_id=session_id,
                    user_id=user_id,
                    channel=channel,
                    agent_id=agent_id,
                    workspace_dir=getattr(workspace, "workspace_dir", None),
                    timeout=remaining,
                ),
            )

    return _collect(results, started)


def _resolve_step_path(
    step: PreprocessStepSpec,
    stage: Path,
    index: int,
) -> Path | str:
    """Resolve one step to a file, or return an error message.

    Returning the message instead of raising keeps the caller's "one result
    row per configured script" invariant: a bad script becomes a reported
    failure, never a gap in the list.
    """
    if step.script:
        resolved = resolve_batch_script(step.script)
        if resolved is None:
            return f"batch script not found in pool: {step.script}"
        return resolved
    try:
        stage.mkdir(parents=True, exist_ok=True)
        # Indexed so chained inline steps cannot overwrite each other.
        path = stage / f"inline_{index}.json"
        path.write_text(
            json.dumps({"actions": step.actions}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path
    except OSError as exc:
        return f"could not stage inline actions: {exc}"


async def _execute_step(
    *,
    spec: PreprocessSpec,
    step: PreprocessStepSpec,
    job: CronJobSpec,
    script_path: Path,
    toolkit: Any,
    session_id: str,
    user_id: str,
    channel: str,
    agent_id: str,
    workspace_dir: Path | None,
    timeout: float,
) -> PreprocessStepResult:
    """Run one script under a scoped tool context, bounded by a timeout."""
    from agentscope.state import AgentState

    from ...runtime.tool_context import (
        config_derived_tool_values,
        scoped_tool_context,
    )

    rtb = _run_tool_batch_module()
    config_values = config_derived_tool_values(agent_id)
    label = step.label
    step_started = time.monotonic()

    def _step_ms() -> int:
        return int((time.monotonic() - step_started) * 1000)

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
                args=step.args or None,
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
        chunk = await asyncio.wait_for(task, timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        logger.warning(
            "cron preprocess timed out: job_id=%s script=%s budget=%ss",
            job.id,
            label,
            spec.timeout_seconds,
        )
        return _step_failure(
            spec,
            step,
            f"preprocess timed out after {spec.timeout_seconds}s",
            status="timeout",
            duration_ms=_step_ms(),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "cron preprocess raised: job_id=%s script=%s error=%s",
            job.id,
            label,
            repr(exc),
        )
        return _step_failure(
            spec,
            step,
            f"{type(exc).__name__}: {exc}",
            duration_ms=_step_ms(),
        )

    return _normalize(
        spec=spec,
        step=step,
        chunk=chunk,
        duration_ms=_step_ms(),
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
    step: PreprocessStepSpec,
    chunk: Any,
    duration_ms: int,
) -> PreprocessStepResult:
    """Turn a ToolChunk into a PreprocessStepResult."""
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

    label = step.label
    return PreprocessStepResult(
        ok=ok,
        status="ok" if ok else "failed",
        script_label=label,
        call_text=_render_call_text(spec, step, label),
        result_json=_truncate(raw),
        user_text=render_user_text(payload),
        error=error,
        duration_ms=duration_ms,
    )


__all__ = [
    "MAX_INJECTED_RESULT_CHARS",
    "PreprocessResult",
    "PreprocessStepResult",
    "build_prompt_block",
    "get_batch_pool_dir",
    "inject_preprocess_block",
    "render_user_text",
    "resolve_batch_script",
    "run_preprocess",
]
