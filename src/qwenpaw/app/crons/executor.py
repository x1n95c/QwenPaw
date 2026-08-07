# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict

from ..inbox_trace_store import (
    append_trace_from_session_delta,
    create_trace,
    finalize_trace,
    read_session_messages,
)
from .models import CronJobSpec
from .preprocess import (
    build_prompt_block,
    run_preprocess,
)
from .prompt_blocks import prepend_text_blocks
from .skill_prompt import build_skill_prompt_block
from ...security.tool_guard.execution_level import ToolExecutionLevel
from ...schemas import RunStatus

logger = logging.getLogger(__name__)


class CronExecutor:
    def __init__(self, *, workspace: Any, channel_manager: Any):
        self._workspace = workspace
        self._channel_manager = channel_manager

    # pylint: disable=too-many-statements,too-many-branches
    @staticmethod
    def _resolve_session_id(job: CronJobSpec) -> str:
        """The session this run belongs to.

        Shared by the preprocess and the agent phase. ``share_session=False``
        derives a dedicated session keyed on ``job.id`` — not on a per-run
        id — so every run of the job accumulates in one place and the user
        gets a complete history.
        """
        target_session_id = job.dispatch.target.session_id
        if job.runtime.share_session:
            return target_session_id or f"cron:{job.id}"
        return (
            f"{target_session_id}:cron:{job.id}"
            if target_session_id
            else f"cron:{job.id}"
        )

    async def execute(self, job: CronJobSpec) -> dict[str, Any]:
        """Execute one job once.

        - task_type text: send fixed text to channel
        - task_type agent + mode stream (default): ask agent with prompt,
            forward every event to channel in real time
            (stream_query + send_event)
        - task_type agent + mode final: consume the full stream, then
            deliver only the last completed message event
        - silent agent task: consume the full agent stream without channel
            delivery, while preserving session and trace state
        """
        target_user_id = job.dispatch.target.user_id
        target_session_id = job.dispatch.target.session_id
        target_channel = job.dispatch.channel
        dispatch_meta: Dict[str, Any] = dict(job.dispatch.meta or {})
        if job.task_type == "agent":
            # Agent cron replies still print to the console channel, but
            # should not raise frontend push bubbles (Inbox remains opt-in).
            dispatch_meta["suppress_console_push"] = True
        logger.info(
            "cron execute: job_id=%s channel=%s task_type=%s "
            "target_user_id=%s target_session_id=%s",
            job.id,
            target_channel,
            job.task_type,
            target_user_id[:40] if target_user_id else "",
            target_session_id[:40] if target_session_id else "",
        )

        # Resolved once, up here: the agent branch used to compute this
        # inline further down, but the preprocess runs before either branch
        # and must see the same session — otherwise a share_session=False
        # job would collect against a different session than it reports to.
        session_id = self._resolve_session_id(job)

        preprocess = await run_preprocess(
            workspace=self._workspace,
            job=job,
            session_id=session_id,
            user_id=target_user_id or "cron",
            channel=target_channel,
        )
        if preprocess is not None:
            logger.info(
                "cron preprocess: job_id=%s script=%s status=%s took=%sms",
                job.id,
                preprocess.script_label,
                preprocess.status,
                preprocess.duration_ms,
            )
            if (
                preprocess.failed
                and job.preprocess is not None
                and job.preprocess.on_failure == "abort"
            ):
                return {
                    "task_type": job.task_type,
                    "run_id": None,
                    "final_text": "",
                    "delivery_status": "skipped",
                    "delivery_error": preprocess.error,
                    "preprocess_status": preprocess.status,
                }

        if job.task_type == "text":
            # Fixed text is the lead-in; a preprocess result is the payload.
            # Either may be absent, so compose rather than assume.
            body_parts = [
                part
                for part in (
                    (job.text or "").strip(),
                    preprocess.user_text.strip() if preprocess else "",
                )
                if part
            ]
            message = "\n\n".join(body_parts)
            if not message:
                logger.warning(
                    "cron text: job_id=%s produced nothing to send",
                    job.id,
                )
                return {
                    "task_type": "text",
                    "run_id": None,
                    "final_text": "",
                    "delivery_status": "no_content",
                    "delivery_error": None,
                    "preprocess_status": (
                        preprocess.status if preprocess else None
                    ),
                }

            logger.info(
                "cron send_text: job_id=%s channel=%s len=%s",
                job.id,
                target_channel,
                len(message),
            )
            text_delivery_error: str | None = None
            try:
                # Bounded on purpose. This branch is the only path that
                # never went through a timeout: a channel send that hangs
                # would leave `execute` awaiting forever, so the per-job
                # semaphore in `_execute_once` is never released and the
                # job (max_concurrency=1 by default) never fires again,
                # sitting in `running` with nothing in the log.
                await asyncio.wait_for(
                    self._channel_manager.send_text(
                        channel=target_channel,
                        user_id=target_user_id,
                        session_id=target_session_id,
                        text=message,
                        meta=dispatch_meta,
                    ),
                    timeout=job.runtime.timeout_seconds,
                )
            except asyncio.TimeoutError:
                text_delivery_error = (
                    f"channel send timed out after "
                    f"{job.runtime.timeout_seconds}s"
                )
                logger.warning(
                    "cron text delivery timed out: job_id=%s channel=%s "
                    "timeout=%ss",
                    job.id,
                    job.dispatch.channel,
                    job.runtime.timeout_seconds,
                )
            except Exception as e:  # pylint: disable=broad-except
                text_delivery_error = repr(e)
                logger.warning(
                    "cron text delivery failed: job_id=%s channel=%s error=%s",
                    job.id,
                    job.dispatch.channel,
                    text_delivery_error,
                )
            return {
                "task_type": "text",
                "run_id": None,
                # What was actually sent, so the Inbox archives the same
                # thing the user received rather than just the lead-in.
                "final_text": message,
                "delivery_status": (
                    "failed" if text_delivery_error else "success"
                ),
                "delivery_error": text_delivery_error,
                "preprocess_status": (
                    preprocess.status if preprocess else None
                ),
            }
        # agent: run request as the dispatch target user so context matches
        logger.info(
            "cron agent: job_id=%s channel=%s stream_query then send_event",
            job.id,
            job.dispatch.channel,
        )
        assert job.request is not None
        req: Dict[str, Any] = job.request.model_dump(mode="json")

        # Into the user prompt, not the agent's context: this is the data
        # the task was created to act on, and it has to survive into
        # session history and the trace like any other user input.
        #
        # One call with the blocks already ordered, rather than one call
        # per block: each prepend goes in front of whatever is there, so
        # two calls would silently invert them. The order is the contract —
        # skill instructions, then the data they are to be applied to, then
        # the request itself.
        skill_block = build_skill_prompt_block(
            job,
            getattr(self._workspace, "workspace_dir", None),
        )
        preprocess_block = (
            build_prompt_block(preprocess) if preprocess is not None else ""
        )
        req["input"] = prepend_text_blocks(
            req.get("input"),
            [skill_block, preprocess_block],
        )

        req["channel"] = target_channel
        req["user_id"] = target_user_id or "cron"
        raw_context = req.get("request_context")
        request_context = (
            dict(raw_context) if isinstance(raw_context, dict) else {}
        )
        request_context["source"] = "cron"
        request_context["cron_job_id"] = job.id or ""
        request_context["approval_level"] = (
            ToolExecutionLevel.AUTO.value
            if job.runtime.tool_safety
            else ToolExecutionLevel.OFF.value
        )
        req["request_context"] = request_context

        # Resolved above so the preprocess and the agent share one session.
        req["session_id"] = session_id
        if not job.runtime.share_session:
            req["session_source"] = "cron"

        # Register a ChatSpec so the session appears in the frontend list.
        chat_manager = getattr(self._workspace, "chat_manager", None)
        _chat_spec = None
        if chat_manager is not None:
            try:
                _chat_spec = await chat_manager.get_or_create_chat(
                    session_id=req["session_id"],
                    user_id=req.get("user_id", "cron"),
                    channel=target_channel,
                    name=job.name or f"Cron: {job.id}",
                    source="cron",
                )
            except Exception:
                logger.debug(
                    "cron: failed to register chat spec for job %s",
                    job.id,
                    exc_info=True,
                )

        delivery_error: str | None = None
        baseline_messages = await read_session_messages(
            runner=self._workspace,
            session_id=req["session_id"],
            user_id=req["user_id"],
            channel=target_channel,
        )
        baseline_count = len(baseline_messages)

        run_id = str(uuid.uuid4())
        await create_trace(
            run_id,
            meta={
                "job_id": job.id,
                "job_name": job.name,
                "task_type": "agent",
                "dispatch_channel": job.dispatch.channel,
                "target_user_id": target_user_id,
                "target_session_id": target_session_id,
                "silent": job.dispatch.silent,
                # Without this, a task whose output looks wrong gives no
                # signal about whether collection even succeeded.
                "preprocess": (
                    {
                        "status": preprocess.status,
                        "duration_ms": preprocess.duration_ms,
                        "script": preprocess.script_label,
                    }
                    if preprocess
                    else None
                ),
            },
        )

        final_no_content = False

        async def _run() -> None:
            nonlocal delivery_error, final_no_content

            async def _deliver(event: Any) -> None:
                nonlocal delivery_error
                try:
                    await self._channel_manager.send_event(
                        channel=target_channel,
                        user_id=target_user_id,
                        session_id=target_session_id,
                        event=event,
                        meta=dispatch_meta,
                    )
                except Exception as e:  # pylint: disable=broad-except
                    if delivery_error is None:
                        delivery_error = repr(e)
                        logger.warning(
                            "cron agent delivery failed: job_id=%s "
                            "channel=%s error=%s",
                            job.id,
                            job.dispatch.channel,
                            delivery_error,
                        )

            final_event: Any | None = None
            async for event in self._workspace.stream_query(req):
                if job.dispatch.silent:
                    continue
                if job.dispatch.mode == "final":
                    if (
                        getattr(event, "object", None) == "message"
                        and getattr(event, "status", None)
                        == RunStatus.Completed
                    ):
                        final_event = event
                    continue
                await _deliver(event)

            if final_event is not None:
                await _deliver(final_event)
            elif job.dispatch.mode == "final" and not job.dispatch.silent:
                final_no_content = True
                logger.warning(
                    "cron final delivery: no completed message in "
                    "stream for job_id=%s",
                    job.id,
                )

        try:
            await asyncio.wait_for(
                _run(),
                timeout=job.runtime.timeout_seconds,
            )
            await append_trace_from_session_delta(
                run_id=run_id,
                runner=self._workspace,
                session_id=req["session_id"],
                user_id=req["user_id"],
                channel=target_channel,
                baseline_count=baseline_count,
            )
            await finalize_trace(run_id, status="success")
            if job.dispatch.silent:
                delivery_status = "suppressed"
            elif delivery_error:
                delivery_status = "failed"
            elif final_no_content:
                delivery_status = "no_content"
            else:
                delivery_status = "success"
            return {
                "task_type": "agent",
                "run_id": run_id,
                "delivery_status": delivery_status,
                "delivery_error": delivery_error,
                "preprocess_status": (
                    preprocess.status if preprocess else None
                ),
            }
        except asyncio.TimeoutError:
            logger.warning(
                "cron execute: job_id=%s timed out after %ss",
                job.id,
                job.runtime.timeout_seconds,
            )
            await append_trace_from_session_delta(
                run_id=run_id,
                runner=self._workspace,
                session_id=req["session_id"],
                user_id=req["user_id"],
                channel=target_channel,
                baseline_count=baseline_count,
            )
            await finalize_trace(
                run_id,
                status="timeout",
                error=f"timed out after {job.runtime.timeout_seconds}s",
            )
            raise
        except asyncio.CancelledError:
            logger.info("cron execute: job_id=%s cancelled", job.id)
            await append_trace_from_session_delta(
                run_id=run_id,
                runner=self._workspace,
                session_id=req["session_id"],
                user_id=req["user_id"],
                channel=target_channel,
                baseline_count=baseline_count,
            )
            await finalize_trace(
                run_id,
                status="cancelled",
                error="execution cancelled",
            )
            raise
        except Exception as e:  # pylint: disable=broad-except
            await append_trace_from_session_delta(
                run_id=run_id,
                runner=self._workspace,
                session_id=req["session_id"],
                user_id=req["user_id"],
                channel=target_channel,
                baseline_count=baseline_count,
            )
            await finalize_trace(
                run_id,
                status="error",
                error=repr(e),
            )
            raise
        finally:
            if _chat_spec is not None and chat_manager is not None:
                try:
                    await chat_manager.touch_chat(_chat_spec.id)
                except Exception:
                    logger.debug(
                        "cron: failed to touch chat for job %s",
                        job.id,
                        exc_info=True,
                    )
