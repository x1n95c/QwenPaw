# -*- coding: utf-8 -*-
"""Tests for the cron preprocess runner.

Two properties matter most and are asserted throughout: the runner never
raises (the executor's contract is "continue and report"), and the same
result is rendered two ways — machine-readable JSON for the model, prose
for a human.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from qwenpaw.app.crons import preprocess as pre
from qwenpaw.app.crons.models import PreprocessSpec
from tests.unit.app.conftest import make_cron_job_spec


# ---------------------------------------------------------------------------
# resolve_batch_script
# ---------------------------------------------------------------------------


@pytest.fixture
def pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "tool_batches"
    directory.mkdir()
    monkeypatch.setattr(pre, "get_batch_pool_dir", lambda: directory)
    return directory


def write_script(pool: Path, name: str, actions: list[dict]) -> Path:
    path = pool / name
    path.write_text(
        json.dumps({"actions": actions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_resolves_a_pool_script(pool: Path):
    write_script(pool, "collect.json", [{"tool_name": "a"}])
    assert pre.resolve_batch_script("collect") == pool / "collect.json"
    assert pre.resolve_batch_script("collect.json") == pool / "collect.json"


def test_missing_script_resolves_to_none(pool: Path):
    assert pre.resolve_batch_script("ghost") is None


@pytest.mark.parametrize(
    "name",
    ["../escape", "sub/dir", "a\\b", "", "   ", ".", ".."],
)
def test_refuses_to_leave_the_pool(pool: Path, name: str):
    """A job spec must not be able to point the runner at any file."""
    assert pre.resolve_batch_script(name) is None


def test_traversal_cannot_reach_a_real_file_outside(pool: Path):
    outside = pool.parent / "secret.json"
    outside.write_text("[]", encoding="utf-8")
    assert pre.resolve_batch_script("../secret") is None


# ---------------------------------------------------------------------------
# render_user_text
# ---------------------------------------------------------------------------


def test_renders_last_only_payload():
    payload = {
        "ok": True,
        "last_step_result": {"step": 1, "text": "disk 42% full"},
    }
    assert pre.render_user_text(payload) == "disk 42% full"


def test_renders_full_results_payload():
    payload = {
        "ok": True,
        "results": [
            {"step": 0, "text": "line one"},
            {"step": 1, "text": "line two"},
        ],
    }
    assert pre.render_user_text(payload) == "line one\n\nline two"


def test_skips_control_flow_steps():
    """label/goto/set_var contribute nothing a person wants to read."""
    payload = {
        "ok": True,
        "results": [
            {"step": 0, "tool_name": "set_var", "text": "i=0"},
            {"step": 1, "tool_name": "execute_shell_command", "text": "real"},
            {"step": 2, "tool_name": "goto", "text": "jumped"},
        ],
    }
    assert pre.render_user_text(payload) == "real"


def test_renders_a_structured_value():
    payload = {"ok": True, "last_step_result": {"value": {"n": 1}}}
    assert pre.render_user_text(payload) == '{"n": 1}'


def test_appends_a_notice_when_not_ok():
    payload = {
        "ok": False,
        "error": "step 2 failed",
        "results": [{"step": 0, "text": "partial"}],
    }
    out = pre.render_user_text(payload)
    assert "partial" in out
    assert "step 2 failed" in out


def test_falls_back_to_the_summary_when_nothing_is_quotable():
    """Better to send the raw summary than an empty message."""
    payload = {"ok": True, "total": 1, "completed": 1}
    out = pre.render_user_text(payload)
    assert "total" in out


def test_truncates_a_huge_result():
    payload = {"ok": True, "last_step_result": {"text": "x" * 20000}}
    out = pre.render_user_text(payload)
    assert len(out) < 20000
    assert "truncated" in out


def test_tolerates_a_non_dict_payload():
    assert pre.render_user_text("plain") == "plain"


# ---------------------------------------------------------------------------
# inject_preprocess_block
# ---------------------------------------------------------------------------


def make_result(ok: bool = True, **overrides: Any) -> pre.PreprocessResult:
    payload = {
        "ok": ok,
        "status": "ok" if ok else "failed",
        "script_label": "collect.json",
        "call_text": '{"tool_name": "run_tool_batch"}',
        "result_json": '{"ok": true}',
        "user_text": "data",
        "error": "" if ok else "boom",
    }
    payload.update(overrides)
    return pre.PreprocessResult(**payload)


def last_user_text(messages: list[dict]) -> str:
    user = [m for m in messages if m.get("role") == "user"][-1]
    return "".join(
        block["text"] for block in user["content"] if block.get("text")
    )


def test_appends_to_the_existing_last_user_message():
    """A second consecutive user turn is rejected by some formatters."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "ask"}]},
    ]
    out = pre.inject_preprocess_block(messages, make_result())

    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert len(out[0]["content"]) == 2
    assert "ask" in last_user_text(out)
    assert "<preprocess_result>" in last_user_text(out)


def test_does_not_mutate_the_input_list():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "ask"}]},
    ]
    pre.inject_preprocess_block(messages, make_result())
    assert len(messages[0]["content"]) == 1


def test_wraps_a_bare_string_input():
    """The console's drawer can produce this via JSON.parse('"hi"')."""
    out = pre.inject_preprocess_block("hi", make_result())
    assert out[0]["role"] == "user"
    assert "hi" in last_user_text(out)
    assert "<preprocess_result>" in last_user_text(out)


@pytest.mark.parametrize("empty", [None, [], ""])
def test_synthesizes_a_message_when_input_is_empty(empty: Any):
    out = pre.inject_preprocess_block(empty, make_result())
    assert out[0]["role"] == "user"
    assert "<preprocess_result>" in last_user_text(out)


def test_targets_the_last_user_turn_not_the_first():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "first"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        {"role": "user", "content": [{"type": "text", "text": "second"}]},
    ]
    out = pre.inject_preprocess_block(messages, make_result())
    assert "<preprocess_result>" not in json.dumps(out[0])
    assert "<preprocess_result>" in json.dumps(out[2])


def test_adds_a_turn_when_there_is_no_user_message():
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    out = pre.inject_preprocess_block(messages, make_result())
    assert out[-1]["role"] == "user"
    assert "<preprocess_result>" in last_user_text(out)


def test_handles_string_content_on_a_user_message():
    messages = [{"role": "user", "content": "plain string"}]
    out = pre.inject_preprocess_block(messages, make_result())
    assert "plain string" in last_user_text(out)


# ---------------------------------------------------------------------------
# build_prompt_block wording — both instructions are load-bearing
# ---------------------------------------------------------------------------


def test_success_block_tells_the_model_not_to_rerun():
    block = pre.build_prompt_block(make_result(ok=True))
    assert "ALREADY RUN" in block
    assert "do not call run_tool_batch again" in block


def test_failure_block_forbids_retry_and_invention():
    block = pre.build_prompt_block(make_result(ok=False))
    assert "FAILED" in block
    assert "Do not retry" in block
    assert "do not invent" in block
    assert "boom" in block


# ---------------------------------------------------------------------------
# run_preprocess
# ---------------------------------------------------------------------------


class FakeWorkspace:
    agent_id = "default"
    app_services = None

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.local_workspace = None


@pytest.fixture
def stub_toolkit(monkeypatch: pytest.MonkeyPatch):
    """Make build_standalone_toolkit cheap and _call_tool observable."""
    from qwenpaw.runtime.builder import AgentBuilder

    async def _toolkit(self, **_kwargs):
        return object()

    monkeypatch.setattr(
        AgentBuilder,
        "build_standalone_toolkit",
        _toolkit,
        raising=False,
    )

    rtb = pre._run_tool_batch_module()
    calls: list[dict[str, Any]] = []

    async def _stub(tool_name: str, arguments: dict[str, Any]):
        calls.append({"tool_name": tool_name, **arguments})
        return rtb._json_tool_response({"ok": True, "text": "collected"})

    monkeypatch.setattr(rtb, "_call_tool", _stub)
    return calls


@pytest.mark.asyncio
async def test_returns_none_without_a_preprocess(tmp_path: Path):
    job = make_cron_job_spec()
    assert (
        await pre.run_preprocess(
            workspace=FakeWorkspace(tmp_path),
            job=job,
            session_id="s",
            user_id="u",
            channel="console",
        )
        is None
    )


@pytest.mark.asyncio
async def test_returns_none_when_disabled(tmp_path: Path):
    job = make_cron_job_spec(
        preprocess={"script": "collect", "enabled": False},
    )
    assert (
        await pre.run_preprocess(
            workspace=FakeWorkspace(tmp_path),
            job=job,
            session_id="s",
            user_id="u",
            channel="console",
        )
        is None
    )


@pytest.mark.asyncio
async def test_missing_script_reports_instead_of_raising(
    tmp_path: Path,
    pool: Path,
):
    job = make_cron_job_spec(preprocess={"script": "ghost"})
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert result.ok is False
    assert result.status == "failed"
    assert "not found" in result.error
    assert "采集失败" in result.user_text


@pytest.mark.asyncio
async def test_runs_a_pool_script(tmp_path: Path, pool: Path, stub_toolkit):
    write_script(
        pool,
        "collect.json",
        [
            {
                "tool_name": "execute_shell_command",
                "arguments": {"command": "d"},
            }
        ],
    )
    job = make_cron_job_spec(preprocess={"script": "collect"})

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )

    assert result is not None and result.ok is True
    assert result.status == "ok"
    assert result.script_label == "collect"
    assert "collected" in result.user_text
    assert [c["tool_name"] for c in stub_toolkit] == [
        "execute_shell_command",
    ]


@pytest.mark.asyncio
async def test_runs_inline_actions(tmp_path: Path, stub_toolkit):
    job = make_cron_job_spec(
        preprocess={
            "actions": [
                {"tool_name": "execute_shell_command", "arguments": {}},
            ],
        },
    )
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None and result.ok is True
    assert "inline" in result.script_label


@pytest.mark.asyncio
async def test_args_are_substituted_for_inline_actions(
    tmp_path: Path,
    stub_toolkit,
):
    """Inline actions are staged to a file precisely so this works.

    ``run_tool_batch`` only resolves ``${args.*}`` when loading from a
    file — passing actions inline leaves the placeholders verbatim.
    """
    job = make_cron_job_spec(
        preprocess={
            "actions": [
                {
                    "tool_name": "execute_shell_command",
                    "arguments": {"command": "echo ${args.name}"},
                },
            ],
            "args": {"name": "QwenPaw"},
        },
    )
    await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert stub_toolkit[0]["command"] == "echo QwenPaw"


@pytest.mark.asyncio
async def test_args_are_substituted_for_pool_scripts(
    tmp_path: Path,
    pool: Path,
    stub_toolkit,
):
    write_script(
        pool,
        "collect.json",
        [
            {
                "tool_name": "execute_shell_command",
                "arguments": {"command": "ls ${args.path}"},
            },
        ],
    )
    job = make_cron_job_spec(
        preprocess={"script": "collect", "args": {"path": "/tmp"}},
    )
    await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert stub_toolkit[0]["command"] == "ls /tmp"


@pytest.mark.asyncio
async def test_a_failing_step_is_reported_not_raised(
    tmp_path: Path,
    pool: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from qwenpaw.runtime.builder import AgentBuilder

    monkeypatch.setattr(
        AgentBuilder,
        "build_standalone_toolkit",
        lambda self, **_kw: _async_value(object()),
        raising=False,
    )
    rtb = pre._run_tool_batch_module()

    async def _boom(_tool_name, _arguments):
        return rtb._json_tool_response({"ok": False, "error": "exploded"})

    monkeypatch.setattr(rtb, "_call_tool", _boom)

    write_script(pool, "collect.json", [{"tool_name": "a", "arguments": {}}])
    job = make_cron_job_spec(preprocess={"script": "collect"})

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert result.ok is False
    assert "exploded" in result.error


def _async_value(value: Any):
    async def _inner():
        return value

    return _inner()


@pytest.mark.asyncio
async def test_timeout_cancels_the_batch(
    tmp_path: Path,
    pool: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A hung step must not hold the job's concurrency slot."""
    from qwenpaw.runtime.builder import AgentBuilder

    monkeypatch.setattr(
        AgentBuilder,
        "build_standalone_toolkit",
        lambda self, **_kw: _async_value(object()),
        raising=False,
    )
    rtb = pre._run_tool_batch_module()
    cancelled = asyncio.Event()

    async def _hang(_tool_name, _arguments):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(rtb, "_call_tool", _hang)

    write_script(pool, "collect.json", [{"tool_name": "a", "arguments": {}}])
    job = make_cron_job_spec(
        preprocess={"script": "collect", "timeout_seconds": 1},
    )

    result = await asyncio.wait_for(
        pre.run_preprocess(
            workspace=FakeWorkspace(tmp_path),
            job=job,
            session_id="s",
            user_id="u",
            channel="console",
        ),
        timeout=10,
    )

    assert result is not None
    assert result.status == "timeout"
    assert "timed out after 1s" in result.error
    await asyncio.wait_for(cancelled.wait(), timeout=2)


@pytest.mark.asyncio
async def test_toolkit_build_failure_is_reported(
    tmp_path: Path,
    pool: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from qwenpaw.runtime.builder import AgentBuilder

    async def _explode(self, **_kwargs):
        raise RuntimeError("no governor for you")

    monkeypatch.setattr(
        AgentBuilder,
        "build_standalone_toolkit",
        _explode,
        raising=False,
    )
    write_script(pool, "collect.json", [{"tool_name": "a"}])
    job = make_cron_job_spec(preprocess={"script": "collect"})

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert result.ok is False
    assert "could not prepare tools" in result.error


@pytest.mark.asyncio
async def test_context_is_restored_after_a_run(
    tmp_path: Path,
    pool: Path,
    stub_toolkit,
):
    """The preprocess toolkit must not outlive the preprocess."""
    from qwenpaw.config.context import get_current_toolkit

    write_script(pool, "collect.json", [{"tool_name": "a", "arguments": {}}])
    job = make_cron_job_spec(preprocess={"script": "collect"})

    before = get_current_toolkit()
    await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert get_current_toolkit() is before


@pytest.mark.asyncio
async def test_records_a_duration(tmp_path: Path, pool: Path, stub_toolkit):
    write_script(pool, "collect.json", [{"tool_name": "a", "arguments": {}}])
    job = make_cron_job_spec(preprocess={"script": "collect"})
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(tmp_path),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert result.duration_ms >= 0


def test_spec_defaults_are_carried_into_the_call_text():
    spec = PreprocessSpec(script="collect", args={"a": 1})
    text = pre._render_call_text(spec, "collect")
    assert '"last_only": true' in text
    assert '"a": 1' in text
