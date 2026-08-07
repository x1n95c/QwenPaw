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
from qwenpaw.app.crons.models import PreprocessSpec, PreprocessStepSpec
from tests.unit.app.conftest import make_cron_job_spec


# ---------------------------------------------------------------------------
# Script resolution
#
# One path only: a script belongs to the job that runs it. The shared pool
# and the `<template>/batch/x.json` reference form are both gone — a
# template's script reaches a job by being copied in.
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


#: `make_cron_job_spec` mints this by default, so the scripts the run
#: tests write have to live under it.
JOB_ID = "job-1"


def write_script(workspace: Path, name: str, actions: list[dict]) -> Path:
    """Put a script in JOB_ID's own directory."""
    from qwenpaw.app.crons.script_paths import job_scripts_dir

    directory = job_scripts_dir(workspace, JOB_ID)
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        json.dumps({"actions": actions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_resolves_one_of_the_jobs_own_scripts(workspace: Path):
    path = write_script(workspace, "collect.json", [{"tool_name": "a"}])
    step = PreprocessStepSpec(script="collect")
    assert (
        pre._resolve_step_path(
            step,
            workspace / "stage",
            0,
            workspace_dir=workspace,
            job_id=JOB_ID,
        )
        == path
    )


def test_a_missing_script_names_the_job_not_a_pool(workspace: Path):
    """The message is the user's only diagnostic; pointing at a global
    location they cannot find is worse than useless."""
    message = pre._resolve_step_path(
        PreprocessStepSpec(script="ghost"),
        workspace / "stage",
        0,
        workspace_dir=workspace,
        job_id=JOB_ID,
    )
    assert isinstance(message, str)
    assert "this job's scripts" in message


def test_a_template_reference_no_longer_resolves(workspace: Path):
    """Left over from before scripts moved under their job.

    Templates hand a script to a job by *copy* now, so a stored reference
    is stale data — it must fail loudly rather than reach into a package.
    """
    message = pre._resolve_step_path(
        PreprocessStepSpec(script="weather-report/batch/weather.json"),
        workspace / "stage",
        0,
        workspace_dir=workspace,
        job_id=JOB_ID,
    )
    assert isinstance(message, str)


def test_resolution_without_a_job_fails_closed(workspace: Path):
    """A script cannot be resolved at all without knowing whose it is."""
    message = pre._resolve_step_path(
        PreprocessStepSpec(script="collect"),
        workspace / "stage",
        0,
        workspace_dir=workspace,
        job_id=None,
    )
    assert isinstance(message, str)
    assert "without a job" in message


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


def test_structured_tool_output_is_readable_not_an_envelope_dump():
    """The regression this rendering was rewritten for.

    A tool that emits JSON has it merged flat into the step result, so there
    is no `text` key to quote. The old fallback dumped the whole envelope —
    `ok`/`total`/`completed` and all — on one line, which is what a weather
    job actually sent its user.
    """
    payload = {
        "ok": True,
        "total": 1,
        "completed": 1,
        "last_step_result": {
            "step": 0,
            "tool_name": "execute_shell_command",
            "current_condition": [{"temp_C": "34"}],
        },
    }
    out = pre.render_user_text(payload)
    assert "current_condition" in out
    # The envelope is bookkeeping, not something a person asked for.
    assert '"completed"' not in out
    assert '"tool_name"' not in out
    assert "\n" in out, "must be pretty-printed"


def test_user_text_is_capped_far_below_the_prompt_budget():
    """A channel message is read by a person, not skimmed by a model."""
    payload = {
        "ok": True,
        "last_step_result": {
            "step": 0,
            "tool_name": "execute_shell_command",
            "rows": [{"n": i, "pad": "x" * 40} for i in range(500)],
        },
    }
    out = pre.render_user_text(payload)
    assert len(out) < pre.MAX_USER_TEXT_CHARS + 100
    assert "truncated" in out


def test_renders_a_structured_value():
    payload = {"ok": True, "last_step_result": {"value": {"n": 1}}}
    # Pretty-printed: this goes to a person in a chat message, and a
    # single-line dump of anything real is unreadable.
    assert pre.render_user_text(payload) == '{\n  "n": 1\n}'


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
# build_prompt_block
#
# Getting the rendered block *into* the message is `prompt_blocks`, tested
# in `test_prompt_blocks.py`: that shape handling is shared with the skill
# block, so it is asserted with plain sentinels and no preprocess at all.
# ---------------------------------------------------------------------------


def make_step_result(
    ok: bool = True,
    label: str = "collect.json",
    **overrides: Any,
) -> pre.PreprocessStepResult:
    payload: dict[str, Any] = {
        "ok": ok,
        "status": "ok" if ok else "failed",
        "script_label": label,
        "call_text": '{"tool_name": "run_tool_batch"}',
        "result_json": '{"ok": true}',
        "user_text": "data",
        "error": "" if ok else "boom",
    }
    payload.update(overrides)
    return pre.PreprocessStepResult(**payload)


def make_result(
    ok: bool = True,
    steps: list[pre.PreprocessStepResult] | None = None,
) -> pre.PreprocessResult:
    resolved = steps if steps is not None else [make_step_result(ok=ok)]
    all_ok = all(step.ok for step in resolved)
    return pre.PreprocessResult(
        ok=all_ok,
        status="ok" if all_ok else "failed",
        steps=resolved,
    )


# ---------------------------------------------------------------------------
# build_prompt_block wording — both instructions are load-bearing
# ---------------------------------------------------------------------------


def test_success_block_tells_the_model_not_to_rerun():
    """A script that succeeded must not be repeated.

    The scripts are real files the model can see and `run_tool_batch` is in
    its toolkit, so a helpful model will absolutely re-issue the call unless
    told plainly not to.
    """
    block = pre.build_prompt_block(make_result(ok=True))
    assert "succeeded" in block
    assert "do not run them again" in block


def test_failure_block_allows_a_retry_but_forbids_invention():
    """A *failed* collection is the one case where retrying is right.

    Blanket "never retry" wording made a transient curl failure permanent
    for that run; the model is given the exact call instead. What it must
    still not do is fill the gap with plausible numbers.
    """
    block = pre.build_prompt_block(make_result(ok=False))
    assert "FAILED" in block
    assert "may re-run" in block
    assert "do not invent" in block
    assert "boom" in block


def test_mixed_block_states_both_outcomes():
    """With a chain, "all fine" and "all broken" are not the only cases.

    Telling the model only one of the two stories is how it ends up either
    re-running the script that worked or inventing the data that did not
    arrive.
    """
    block = pre.build_prompt_block(
        make_result(
            steps=[
                make_step_result(ok=True, label="good"),
                make_step_result(ok=False, label="bad"),
            ],
        ),
    )
    assert "1 of them FAILED" in block
    # Both halves of the instruction, because both apply at once.
    assert "Do not re-run the ones that succeeded" in block
    assert "may re-run a failed script" in block
    assert "do not invent" in block
    # Each script is identified, so the model can tell them apart.
    assert "[1/2] good" in block
    assert "[2/2] bad" in block


def test_call_text_shows_the_absolute_path_not_the_short_name():
    """The model is shown a call it could re-issue.

    `run_tool_batch` requires an absolute path, so printing the step's short
    name made the example uncopyable — a retry with it fails on "Batch file
    not found".
    """
    from pathlib import Path as _Path

    text = pre._render_call_text(
        PreprocessSpec(script="weather"),
        PreprocessStepSpec(script="weather", args={"city": "杭州"}),
        "weather",
        _Path("/ws/cron_jobs/abc/batch/weather.json"),
    )
    assert '"file_path": "/ws/cron_jobs/abc/batch/weather.json"' in text
    assert '"city": "杭州"' in text


def test_call_text_falls_back_to_the_label_without_a_path():
    """A script that never resolved has no path to print."""
    text = pre._render_call_text(
        PreprocessSpec(script="ghost"),
        PreprocessStepSpec(script="ghost"),
        "ghost",
    )
    assert '"file_path": "ghost"' in text


def test_single_script_block_omits_the_index_prefix():
    block = pre.build_prompt_block(make_result(ok=True))
    assert "[1/1]" not in block


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
async def test_returns_none_without_a_preprocess(workspace: Path):
    job = make_cron_job_spec()
    assert (
        await pre.run_preprocess(
            workspace=FakeWorkspace(workspace),
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
            workspace=FakeWorkspace(workspace),
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
    workspace: Path,
):
    job = make_cron_job_spec(preprocess={"script": "ghost"})
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
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
async def test_runs_a_pool_script(workspace: Path, stub_toolkit):
    write_script(
        workspace,
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
        workspace=FakeWorkspace(workspace),
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
async def test_a_missing_script_fails_only_its_own_step(
    tmp_path: Path,
    workspace: Path,
    stub_toolkit,
):
    """A deleted script must not take the rest of the chain with it."""
    write_script(
        workspace,
        "collect.json",
        [{"tool_name": "execute_shell_command", "arguments": {}}],
    )
    job = make_cron_job_spec(
        preprocess={
            "steps": [
                {"script": "gone"},
                {"script": "collect"},
            ],
        },
    )

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )

    assert result is not None
    assert [step.ok for step in result.steps] == [False, True]
    assert "this job's scripts" in result.error
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
        workspace=FakeWorkspace(workspace),
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
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert stub_toolkit[0]["command"] == "echo QwenPaw"


@pytest.mark.asyncio
async def test_args_are_substituted_for_pool_scripts(
    tmp_path: Path,
    workspace: Path,
    stub_toolkit,
):
    write_script(
        workspace,
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
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert stub_toolkit[0]["command"] == "ls /tmp"


@pytest.mark.asyncio
async def test_a_failing_step_is_reported_not_raised(
    tmp_path: Path,
    workspace: Path,
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

    write_script(
        workspace, "collect.json", [{"tool_name": "a", "arguments": {}}]
    )
    job = make_cron_job_spec(preprocess={"script": "collect"})

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
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
    workspace: Path,
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

    write_script(
        workspace, "collect.json", [{"tool_name": "a", "arguments": {}}]
    )
    job = make_cron_job_spec(
        preprocess={"script": "collect", "timeout_seconds": 1},
    )

    result = await asyncio.wait_for(
        pre.run_preprocess(
            workspace=FakeWorkspace(workspace),
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
    workspace: Path,
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
    write_script(workspace, "collect.json", [{"tool_name": "a"}])
    job = make_cron_job_spec(preprocess={"script": "collect"})

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
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
    workspace: Path,
    stub_toolkit,
):
    """The preprocess toolkit must not outlive the preprocess."""
    from qwenpaw.config.context import get_current_toolkit

    write_script(
        workspace, "collect.json", [{"tool_name": "a", "arguments": {}}]
    )
    job = make_cron_job_spec(preprocess={"script": "collect"})

    before = get_current_toolkit()
    await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert get_current_toolkit() is before


@pytest.mark.asyncio
async def test_records_a_duration(workspace: Path, stub_toolkit):
    write_script(
        workspace, "collect.json", [{"tool_name": "a", "arguments": {}}]
    )
    job = make_cron_job_spec(preprocess={"script": "collect"})
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert result.duration_ms >= 0


def test_spec_defaults_are_carried_into_the_call_text():
    spec = PreprocessSpec(script="collect", args={"a": 1})
    text = pre._render_call_text(spec, spec.steps[0], "collect")
    assert '"last_only": true' in text
    # Args come from the step, not the chain: two scripts have their own.
    assert '"a": 1' in text


# ---------------------------------------------------------------------------
# Chained scripts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_several_scripts_in_order(
    tmp_path: Path,
    workspace: Path,
    stub_toolkit,
):
    write_script(
        workspace,
        "first.json",
        [{"tool_name": "tool_a", "arguments": {"v": "${args.x}"}}],
    )
    write_script(
        workspace, "second.json", [{"tool_name": "tool_b", "arguments": {}}]
    )
    job = make_cron_job_spec(
        preprocess={
            "steps": [
                {"script": "first", "args": {"x": "1"}},
                {"script": "second"},
            ],
        },
    )

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )

    assert result is not None and result.ok is True
    assert [s.script_label for s in result.steps] == ["first", "second"]
    # Execution order, and each step's own args were applied to its script.
    assert [c["tool_name"] for c in stub_toolkit] == ["tool_a", "tool_b"]
    assert stub_toolkit[0]["v"] == "1"


@pytest.mark.asyncio
async def test_a_failing_script_does_not_stop_the_rest(
    tmp_path: Path,
    workspace: Path,
    stub_toolkit,
):
    """ "Continue and report" has to hold per script, not just per chain."""
    write_script(
        workspace, "second.json", [{"tool_name": "tool_b", "arguments": {}}]
    )
    job = make_cron_job_spec(
        preprocess={
            "steps": [{"script": "ghost"}, {"script": "second"}],
        },
    )

    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )

    assert result is not None
    assert result.ok is False
    assert len(result.steps) == 2
    assert result.steps[0].failed and "not found" in result.steps[0].error
    assert result.steps[1].ok
    # The second script really ran despite the first failing.
    assert [c["tool_name"] for c in stub_toolkit] == ["tool_b"]
    # The chain error names the culprit rather than blaming the chain.
    assert "ghost" in result.error


@pytest.mark.asyncio
async def test_chain_user_text_labels_each_script(
    tmp_path: Path,
    workspace: Path,
    stub_toolkit,
):
    write_script(workspace, "a.json", [{"tool_name": "t", "arguments": {}}])
    write_script(workspace, "b.json", [{"tool_name": "t", "arguments": {}}])
    job = make_cron_job_spec(
        preprocess={"steps": [{"script": "a"}, {"script": "b"}]},
    )
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert "【a】" in result.user_text
    assert "【b】" in result.user_text


@pytest.mark.asyncio
async def test_single_script_user_text_has_no_label(
    tmp_path: Path,
    workspace: Path,
    stub_toolkit,
):
    """The label would be noise in what is often the whole message body."""
    write_script(workspace, "a.json", [{"tool_name": "t", "arguments": {}}])
    job = make_cron_job_spec(preprocess={"script": "a"})
    result = await pre.run_preprocess(
        workspace=FakeWorkspace(workspace),
        job=job,
        session_id="s",
        user_id="u",
        channel="console",
    )
    assert result is not None
    assert result.user_text == "collected"


@pytest.mark.asyncio
async def test_budget_is_shared_across_the_chain(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """timeout_seconds bounds the chain, not each script.

    Otherwise adding scripts would multiply how long one job can hold its
    concurrency slot — the exact thing the timeout exists to prevent.
    """
    from qwenpaw.runtime.builder import AgentBuilder

    monkeypatch.setattr(
        AgentBuilder,
        "build_standalone_toolkit",
        lambda self, **_kw: _async_value(object()),
        raising=False,
    )
    rtb = pre._run_tool_batch_module()

    async def _hang(_tool_name, _arguments):
        await asyncio.Event().wait()

    monkeypatch.setattr(rtb, "_call_tool", _hang)

    write_script(workspace, "a.json", [{"tool_name": "t", "arguments": {}}])
    write_script(workspace, "b.json", [{"tool_name": "t", "arguments": {}}])
    job = make_cron_job_spec(
        preprocess={
            "steps": [{"script": "a"}, {"script": "b"}],
            "timeout_seconds": 1,
        },
    )

    result = await asyncio.wait_for(
        pre.run_preprocess(
            workspace=FakeWorkspace(workspace),
            job=job,
            session_id="s",
            user_id="u",
            channel="console",
        ),
        # Comfortably under 2x the budget: a per-script timeout would need
        # ~2s here, a shared one finishes in ~1s.
        timeout=1.8,
    )

    assert result is not None
    assert result.status == "timeout"
    # Both scripts are still accounted for; the second never got to start.
    assert len(result.steps) == 2
    assert "exhausted" in result.steps[1].error
