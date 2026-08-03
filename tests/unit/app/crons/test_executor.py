# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.crons.executor import CronExecutor
from qwenpaw.app.crons.models import DispatchSpec, DispatchTarget
from qwenpaw.schemas import Event, RunStatus
from tests.unit.app.conftest import make_cron_job_spec


class _Workspace:
    chat_manager = None

    def __init__(self, events=None) -> None:
        self.events_consumed = 0
        self.events = events if events is not None else ("first", "second")

    async def stream_query(self, _request):
        for event in self.events:
            self.events_consumed += 1
            yield event


def _patch_trace_storage(monkeypatch):
    monkeypatch.setattr(
        "qwenpaw.app.crons.executor.read_session_messages",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "qwenpaw.app.crons.executor.create_trace",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "qwenpaw.app.crons.executor.append_trace_from_session_delta",
        AsyncMock(),
    )
    finalize_trace = AsyncMock()
    monkeypatch.setattr(
        "qwenpaw.app.crons.executor.finalize_trace",
        finalize_trace,
    )
    return finalize_trace


@pytest.mark.asyncio
async def test_silent_agent_job_runs_without_channel_delivery(monkeypatch):
    workspace = _Workspace()
    channel_manager = AsyncMock()
    job = make_cron_job_spec(job_id="silent-job")
    job.dispatch = DispatchSpec(
        target=DispatchTarget(user_id="u1", session_id="console:u1"),
        silent=True,
    )

    finalize_trace = _patch_trace_storage(monkeypatch)

    result = await CronExecutor(
        workspace=workspace,
        channel_manager=channel_manager,
    ).execute(job)

    assert workspace.events_consumed == 2
    channel_manager.send_event.assert_not_awaited()
    assert result["delivery_status"] == "suppressed"
    finalize_trace.assert_awaited_once_with(result["run_id"], status="success")


@pytest.mark.asyncio
async def test_agent_job_still_delivers_by_default(monkeypatch):
    workspace = _Workspace()
    channel_manager = AsyncMock()
    job = make_cron_job_spec(job_id="normal-job")

    _patch_trace_storage(monkeypatch)

    result = await CronExecutor(
        workspace=workspace,
        channel_manager=channel_manager,
    ).execute(job)

    assert workspace.events_consumed == 2
    assert channel_manager.send_event.await_count == 2
    assert result["delivery_status"] == "success"


@pytest.mark.asyncio
async def test_final_mode_delivers_only_last_completed_message(monkeypatch):
    first = Event(
        object="message",
        status=RunStatus.Completed,
        data={"text": "first"},
    )
    progress = Event(object="message", status=RunStatus.InProgress)
    final = Event(
        object="message",
        status=RunStatus.Completed,
        data={"text": "final"},
    )
    workspace = _Workspace([first, progress, final])
    channel_manager = AsyncMock()
    job = make_cron_job_spec(job_id="final-job")
    job.dispatch = DispatchSpec(
        target=DispatchTarget(user_id="u1", session_id="console:u1"),
        mode="final",
    )

    _patch_trace_storage(monkeypatch)

    result = await CronExecutor(
        workspace=workspace,
        channel_manager=channel_manager,
    ).execute(job)

    assert workspace.events_consumed == 3
    channel_manager.send_event.assert_awaited_once()
    assert channel_manager.send_event.await_args.kwargs["event"] is final
    assert result["delivery_status"] == "success"


@pytest.mark.asyncio
async def test_final_mode_reports_delivery_failure(monkeypatch):
    final = Event(object="message", status=RunStatus.Completed)
    workspace = _Workspace([final])
    channel_manager = AsyncMock()
    channel_manager.send_event.side_effect = RuntimeError("channel down")
    job = make_cron_job_spec(job_id="final-failure-job")
    job.dispatch = DispatchSpec(
        target=DispatchTarget(user_id="u1", session_id="console:u1"),
        mode="final",
    )

    _patch_trace_storage(monkeypatch)

    result = await CronExecutor(
        workspace=workspace,
        channel_manager=channel_manager,
    ).execute(job)

    channel_manager.send_event.assert_awaited_once()
    assert result["delivery_status"] == "failed"
    assert "channel down" in result["delivery_error"]


@pytest.mark.asyncio
async def test_final_mode_no_completed_message_returns_no_content(monkeypatch):
    progress = Event(object="message", status=RunStatus.InProgress)
    workspace = _Workspace([progress])
    channel_manager = AsyncMock()
    job = make_cron_job_spec(job_id="final-empty-job")
    job.dispatch = DispatchSpec(
        target=DispatchTarget(user_id="u1", session_id="console:u1"),
        mode="final",
    )

    _patch_trace_storage(monkeypatch)

    result = await CronExecutor(
        workspace=workspace,
        channel_manager=channel_manager,
    ).execute(job)

    assert workspace.events_consumed == 1
    channel_manager.send_event.assert_not_awaited()
    assert result["delivery_status"] == "no_content"


# ---------------------------------------------------------------------------
# Text delivery is bounded.
#
# Regression guard for a real defect: this branch was the only execution
# path with no timeout anywhere. `manager._execute_once` awaits
# `execute()` without one, and only the agent branch wrapped its own work.
# A channel send that never returns would therefore hold the per-job
# semaphore forever, so a job with the default max_concurrency=1 would stop
# firing entirely and sit in `running` with nothing logged.
# ---------------------------------------------------------------------------


class _HangingChannels:
    """A channel whose send never completes."""

    def __init__(self) -> None:
        self.calls = 0

    async def send_text(self, **_kwargs) -> None:
        self.calls += 1
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_text_delivery_times_out_instead_of_hanging():
    job = make_cron_job_spec(task_type="text", text="hi")
    job.runtime.timeout_seconds = 1
    channels = _HangingChannels()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    # The outer bound is generous; the inner timeout is what must fire.
    result = await asyncio.wait_for(executor.execute(job), timeout=10)

    assert channels.calls == 1
    assert result["delivery_status"] == "failed"
    assert "timed out after 1s" in result["delivery_error"]


@pytest.mark.asyncio
async def test_text_timeout_uses_the_jobs_configured_value():
    job = make_cron_job_spec(task_type="text", text="hi")
    job.runtime.timeout_seconds = 2
    executor = CronExecutor(
        workspace=None,
        channel_manager=_HangingChannels(),
    )

    started = asyncio.get_running_loop().time()
    result = await asyncio.wait_for(executor.execute(job), timeout=10)
    elapsed = asyncio.get_running_loop().time() - started

    assert 1.5 <= elapsed < 5
    assert "timed out after 2s" in result["delivery_error"]


@pytest.mark.asyncio
async def test_fast_text_delivery_is_unaffected():
    job = make_cron_job_spec(task_type="text", text="hi")
    channels = AsyncMock()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    result = await executor.execute(job)

    assert result["delivery_status"] == "success"
    assert result["delivery_error"] is None
    assert result["final_text"] == "hi"


# ---------------------------------------------------------------------------
# Preprocess wiring.
#
# The runner itself is covered in test_preprocess.py; these assert the
# executor's half: where the result goes for each task type, that the two
# phases agree on the session, and that a failure still delivers.
# ---------------------------------------------------------------------------


def _preprocess_result(ok: bool = True, user_text: str = "collected"):
    from qwenpaw.app.crons.preprocess import PreprocessResult

    return PreprocessResult(
        ok=ok,
        status="ok" if ok else "failed",
        script_label="collect",
        call_text='{"tool_name": "run_tool_batch"}',
        result_json='{"ok": true, "text": "collected"}',
        user_text=user_text,
        error="" if ok else "boom",
        duration_ms=7,
    )


@pytest.fixture
def fake_preprocess(monkeypatch):
    """Stub the runner; record the arguments the executor passes it."""
    calls: list[dict] = []
    box: dict = {"result": None}

    async def _run(**kwargs):
        calls.append(kwargs)
        return box["result"]

    monkeypatch.setattr(
        "qwenpaw.app.crons.executor.run_preprocess",
        _run,
    )
    return calls, box


@pytest.mark.asyncio
async def test_text_job_appends_the_preprocess_result(fake_preprocess):
    calls, box = fake_preprocess
    box["result"] = _preprocess_result(user_text="disk 42% full")

    job = make_cron_job_spec(
        task_type="text",
        text="Daily check",
        preprocess={"script": "collect"},
    )
    channels = AsyncMock()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    result = await executor.execute(job)

    sent = channels.send_text.await_args.kwargs["text"]
    assert sent == "Daily check\n\ndisk 42% full"
    assert result["final_text"] == sent
    assert result["preprocess_status"] == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_text_job_with_no_lead_in_sends_only_the_result(
    fake_preprocess,
):
    _calls, box = fake_preprocess
    box["result"] = _preprocess_result(user_text="just the data")

    job = make_cron_job_spec(
        task_type="text",
        preprocess={"script": "collect"},
    )
    channels = AsyncMock()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    await executor.execute(job)

    assert channels.send_text.await_args.kwargs["text"] == "just the data"


@pytest.mark.asyncio
async def test_text_job_with_nothing_to_send_is_no_content(fake_preprocess):
    """Never send an empty message."""
    _calls, box = fake_preprocess
    box["result"] = _preprocess_result(user_text="")

    job = make_cron_job_spec(
        task_type="text",
        preprocess={"script": "collect"},
    )
    channels = AsyncMock()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    result = await executor.execute(job)

    assert result["delivery_status"] == "no_content"
    channels.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_job_delivers_even_when_preprocess_failed(
    fake_preprocess,
):
    """on_failure defaults to continue: report, do not swallow."""
    _calls, box = fake_preprocess
    box["result"] = _preprocess_result(ok=False, user_text="⚠️ failed")

    job = make_cron_job_spec(
        task_type="text",
        text="Daily check",
        preprocess={"script": "collect"},
    )
    channels = AsyncMock()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    result = await executor.execute(job)

    assert "⚠️ failed" in channels.send_text.await_args.kwargs["text"]
    assert result["preprocess_status"] == "failed"


@pytest.mark.asyncio
async def test_abort_on_failure_skips_delivery(fake_preprocess):
    _calls, box = fake_preprocess
    box["result"] = _preprocess_result(ok=False)

    job = make_cron_job_spec(
        task_type="text",
        text="Daily check",
        preprocess={"script": "collect", "on_failure": "abort"},
    )
    channels = AsyncMock()
    executor = CronExecutor(workspace=None, channel_manager=channels)

    result = await executor.execute(job)

    assert result["delivery_status"] == "skipped"
    channels.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_job_injects_into_the_prompt(monkeypatch, fake_preprocess):
    _calls, box = fake_preprocess
    box["result"] = _preprocess_result()
    _patch_trace_storage(monkeypatch)

    captured: dict = {}

    class _CapturingWorkspace(_Workspace):
        async def stream_query(self, request):
            captured.update(request)
            for event in ():
                yield event

    job = make_cron_job_spec(preprocess={"script": "collect"})
    executor = CronExecutor(
        workspace=_CapturingWorkspace(),
        channel_manager=AsyncMock(),
    )

    await executor.execute(job)

    blob = json.dumps(captured["input"], ensure_ascii=False)
    assert "<preprocess_result>" in blob
    assert "do not call run_tool_batch again" in blob


@pytest.mark.asyncio
async def test_agent_job_without_preprocess_is_untouched(
    monkeypatch,
    fake_preprocess,
):
    _calls, box = fake_preprocess
    box["result"] = None
    _patch_trace_storage(monkeypatch)

    captured: dict = {}

    class _CapturingWorkspace(_Workspace):
        async def stream_query(self, request):
            captured.update(request)
            for event in ():
                yield event

    job = make_cron_job_spec()
    executor = CronExecutor(
        workspace=_CapturingWorkspace(),
        channel_manager=AsyncMock(),
    )

    await executor.execute(job)

    assert captured["input"] == "ping"
    assert captured.get("request_context", {}).get("source") == "cron"


@pytest.mark.asyncio
async def test_both_phases_share_one_session(monkeypatch, fake_preprocess):
    """A share_session=False job must not collect against another session."""
    calls, box = fake_preprocess
    box["result"] = _preprocess_result()
    _patch_trace_storage(monkeypatch)

    captured: dict = {}

    class _CapturingWorkspace(_Workspace):
        async def stream_query(self, request):
            captured.update(request)
            for event in ():
                yield event

    job = make_cron_job_spec(preprocess={"script": "collect"})
    job.runtime.share_session = False
    executor = CronExecutor(
        workspace=_CapturingWorkspace(),
        channel_manager=AsyncMock(),
    )

    await executor.execute(job)

    assert calls[0]["session_id"] == captured["session_id"]
    assert captured["session_id"].endswith(f"cron:{job.id}")


@pytest.mark.asyncio
async def test_session_id_helper_matches_previous_behaviour():
    """Hoisting the computation must not change what it produces."""
    shared = make_cron_job_spec()
    assert CronExecutor._resolve_session_id(shared) == "console:u1"

    isolated = make_cron_job_spec()
    isolated.runtime.share_session = False
    assert (
        CronExecutor._resolve_session_id(isolated)
        == f"console:u1:cron:{isolated.id}"
    )
