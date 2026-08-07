# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional

import click

from .http import client, print_json
from ..app.channels.schema import DEFAULT_CHANNEL


def _base_url(ctx: click.Context, base_url: Optional[str]) -> str:
    """Resolve base_url with priority:
    1) command --base-url
    2) global --host/--port
        (already resolved in main.py, may come from config.json)
    """
    if base_url:
        return base_url.rstrip("/")
    host = (ctx.obj or {}).get("host", "127.0.0.1")
    port = (ctx.obj or {}).get("port", 8088)
    return f"http://{host}:{port}"


@click.group("cron")
def cron_group() -> None:
    """Manage scheduled cron jobs via the HTTP API (/cron).

    Use list/get/state to inspect jobs; create/update/delete to
    add, modify, or remove; pause/resume to toggle execution;
    run to trigger a one-off run.
    """


@cron_group.command("list")
@click.option(
    "--base-url",
    default=None,
    help=(
        "Override the API base URL (e.g. http://127.0.0.1:8088). "
        "If omitted, uses global --host and --port from config."
    ),
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def list_jobs(
    ctx: click.Context,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """List all cron jobs. Output is JSON from GET /cron/jobs."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.get("/cron/jobs", headers=headers)
        r.raise_for_status()
        print_json(r.json())


@cron_group.command("get")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def get_job(
    ctx: click.Context,
    job_id: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Fetch a cron job by ID. Returns JSON from GET /cron/jobs/<id>."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.get(f"/cron/jobs/{job_id}", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        print_json(r.json())


@cron_group.command("state")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def job_state(
    ctx: click.Context,
    job_id: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Get the runtime state of a cron job (e.g. next run time, paused)."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.get(f"/cron/jobs/{job_id}/state", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        print_json(r.json())


def _validate_and_apply_scheduled_repeat(
    schedule: dict,
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
) -> None:
    if repeat_end_type and repeat_every_days is None:
        raise click.UsageError(
            "--repeat-end-type requires --repeat-every-days",
        )
    if repeat_until and (
        repeat_end_type != "until" or repeat_every_days is None
    ):
        raise click.UsageError(
            "--repeat-until requires --repeat-every-days and "
            "--repeat-end-type until",
        )
    if repeat_count is not None and (
        repeat_end_type != "count" or repeat_every_days is None
    ):
        raise click.UsageError(
            "--repeat-count requires --repeat-every-days and "
            "--repeat-end-type count",
        )
    if repeat_every_days is None:
        return

    schedule["repeat_every_days"] = repeat_every_days
    end_type = repeat_end_type or "never"
    schedule["repeat_end_type"] = end_type
    if end_type == "until":
        if not (repeat_until and repeat_until.strip()):
            raise click.UsageError(
                "--repeat-until is required when --repeat-end-type is 'until'",
            )
        schedule["repeat_until"] = repeat_until.strip()
    elif end_type == "count":
        if repeat_count is None:
            raise click.UsageError(
                "--repeat-count is required when --repeat-end-type is 'count'",
            )
        schedule["repeat_count"] = repeat_count


def _build_schedule_from_cli(
    schedule_type: str,
    cron: str,
    run_at: Optional[str],
    timezone: str,
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
) -> dict:
    if schedule_type == "scheduled":
        if not (run_at and run_at.strip()):
            raise click.UsageError(
                "--run-at is required when schedule type is 'scheduled'",
            )
        schedule = {
            "type": "once",
            "run_at": run_at.strip(),
            "timezone": timezone,
        }
        _validate_and_apply_scheduled_repeat(
            schedule=schedule,
            repeat_every_days=repeat_every_days,
            repeat_end_type=repeat_end_type,
            repeat_until=repeat_until,
            repeat_count=repeat_count,
        )
        return schedule

    if not (cron and cron.strip()):
        raise click.UsageError(
            "--cron is required when schedule type is 'cron'",
        )
    if (
        repeat_every_days is not None
        or repeat_end_type is not None
        or repeat_until is not None
        or repeat_count is not None
    ):
        raise click.UsageError(
            "--repeat-* options are only supported when "
            "--schedule-type is 'scheduled'",
        )
    return {"type": "cron", "cron": cron, "timezone": timezone}


def _parse_key_value_pairs(
    pairs: tuple[str, ...],
    option_name: str,
) -> Dict[str, str]:
    """Parse repeatable ``KEY=VALUE`` CLI options into a dict."""
    values: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.UsageError(
                f"{option_name} expects KEY=VALUE, got: {pair}",
            )
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise click.UsageError(
                f"{option_name} expects KEY=VALUE, got: {pair}",
            )
        values[key] = value
    return values


def _expand_dotted_args(values: Dict[str, Any]) -> Dict[str, Any]:
    """Expand dotted keys into nested dicts.

    The executor resolves ``${args.a.b}`` by walking ``args["a"]["b"]``
    (``run_tool_batch._lookup_arg``), so ``--preprocess-arg a.b=v`` must
    land as a nested mapping — a flat ``"a.b"`` key would fail with
    "Missing arg" at run time.
    """
    expanded: Dict[str, Any] = {}
    for key, value in values.items():
        if "." not in key:
            expanded[key] = value
            continue
        node = expanded
        parts = key.split(".")
        for part in parts[:-1]:
            existing = node.get(part)
            if not isinstance(existing, dict):
                existing = {}
                node[part] = existing
            node = existing
        node[parts[-1]] = value
    return expanded


def _build_preprocess(
    preprocess_script: Optional[str],
    preprocess_args: Optional[Dict[str, Any]],
) -> Optional[dict]:
    """Build the ``preprocess`` block for a CLI-created job spec."""
    if not preprocess_script or not preprocess_script.strip():
        return None
    return {
        "enabled": True,
        "script": preprocess_script.strip(),
        "args": _expand_dotted_args(dict(preprocess_args or {})),
    }


def _build_spec_from_cli(
    task_type: str,
    schedule_type: str,
    name: str,
    cron: str,
    run_at: Optional[str],
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
    channel: str,
    target_user: str,
    target_session: str,
    text: Optional[str],
    timezone: str,
    enabled: bool,
    mode: str,
    silent: bool,
    save_result_to_inbox: Optional[bool] = None,
    share_session: bool = True,
    timeout_seconds: int = 120,
    tool_safety: bool = False,
    preprocess_script: Optional[str] = None,
    preprocess_args: Optional[Dict[str, Any]] = None,
) -> dict:
    """Build CronJobSpec JSON payload from CLI args (no id)."""
    if preprocess_args and not (
        preprocess_script and preprocess_script.strip()
    ):
        raise click.UsageError(
            "--preprocess-arg requires --preprocess-script",
        )
    preprocess = _build_preprocess(preprocess_script, preprocess_args)
    schedule = _build_schedule_from_cli(
        schedule_type=schedule_type,
        cron=cron,
        run_at=run_at,
        timezone=timezone,
        repeat_every_days=repeat_every_days,
        repeat_end_type=repeat_end_type,
        repeat_until=repeat_until,
        repeat_count=repeat_count,
    )
    dispatch = {
        "type": "channel",
        "channel": channel,
        "target": {"user_id": target_user, "session_id": target_session},
        "mode": mode,
        "silent": silent,
        "meta": {},
    }
    runtime = {
        "share_session": share_session,
        "max_concurrency": 1,
        "timeout_seconds": timeout_seconds,
        "misfire_grace_seconds": 600,
        "tool_safety": tool_safety,
    }
    if task_type == "text":
        if silent:
            raise click.UsageError(
                "--silent is only supported when task type is 'agent'",
            )
        clean_text = text.strip() if text and text.strip() else None
        if clean_text is None and preprocess is None:
            raise click.UsageError(
                "--text is required when task type is 'text' "
                "and no --preprocess-script is given",
            )
        payload = {
            "id": "",
            "name": name,
            "enabled": enabled,
            "schedule": schedule,
            "task_type": "text",
            "text": clean_text,
            "dispatch": dispatch,
            "runtime": runtime,
            "meta": {},
        }
        if preprocess is not None:
            payload["preprocess"] = preprocess
        if save_result_to_inbox is not None:
            payload["save_result_to_inbox"] = save_result_to_inbox
        return payload
    if task_type == "agent":
        if not (text and text.strip()):
            raise click.UsageError(
                "--text is required when task type is 'agent' "
                "(the question/prompt sent to the agent)",
            )
        payload = {
            "id": "",
            "name": name,
            "enabled": enabled,
            "schedule": schedule,
            "task_type": "agent",
            "request": {
                "input": [
                    {
                        "role": "user",
                        "type": "message",
                        "content": [{"type": "text", "text": text.strip()}],
                    },
                ],
            },
            "dispatch": dispatch,
            "runtime": runtime,
            "meta": {},
        }
        if preprocess is not None:
            payload["preprocess"] = preprocess
        if save_result_to_inbox is not None:
            payload["save_result_to_inbox"] = save_result_to_inbox
        return payload
    raise click.UsageError(f"Unsupported task type: {task_type}")


@cron_group.command("create")
@click.option(
    "-f",
    "--file",
    "file_",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a JSON file containing the full cron job spec. "
        "Mutually exclusive with inline options (--type, --name, etc.)."
    ),
)
@click.option(
    "--type",
    "task_type",
    type=click.Choice(["text", "agent"], case_sensitive=False),
    default=None,
    help=(
        "Task type: 'text' sends fixed content to the channel; "
        "'agent' sends a question to the agent and delivers the reply to the "
        "channel. Required when not using -f/--file."
    ),
)
@click.option(
    "--schedule-type",
    type=click.Choice(["cron", "scheduled"], case_sensitive=False),
    default="cron",
    show_default=True,
    help=(
        "Schedule type: 'cron' for recurring jobs, "
        "'scheduled' for calendar-style jobs."
    ),
)
@click.option(
    "--name",
    default=None,
    help="Display name for the job. Required when not using -f/--file.",
)
@click.option(
    "--cron",
    default=None,
    help=(
        "Cron expression (5 fields: minute hour day month weekday). "
        "Example: '0 9 * * *' for daily at 09:00. "
        "Required when --schedule-type is cron."
    ),
)
@click.option(
    "--run-at",
    default=None,
    help=(
        "Run time for one-time jobs in ISO 8601 format, e.g. "
        "'2026-04-21T15:30:00+08:00'. "
        "Required when --schedule-type is scheduled."
    ),
)
@click.option(
    "--repeat-every-days",
    type=click.IntRange(min=1),
    default=None,
    help=(
        "For --schedule-type scheduled only. "
        "Repeat every N days (>=1). "
        "If omitted, the job runs once."
    ),
)
@click.option(
    "--repeat-end-type",
    type=click.Choice(["never", "until", "count"], case_sensitive=False),
    default=None,
    help=(
        "For repeated scheduled jobs only. End condition: "
        "'never', 'until', or 'count'. Defaults to 'never' "
        "when --repeat-every-days is set."
    ),
)
@click.option(
    "--repeat-until",
    default=None,
    help=(
        "For repeated scheduled jobs. End date-time in ISO 8601 format. "
        "Required when --repeat-end-type is until."
    ),
)
@click.option(
    "--repeat-count",
    type=click.IntRange(min=1),
    default=None,
    help=(
        "For repeated scheduled jobs. Max run count (>=1). "
        "Required when --repeat-end-type is count."
    ),
)
@click.option(
    "--channel",
    default=None,
    help=(
        "Delivery channel: e.g. imessage, dingtalk, discord, qq, console. "
        "Required when not using -f/--file."
    ),
)
@click.option(
    "--target-user",
    default=None,
    help=(
        "Target user_id for the channel (recipient identifier). "
        "Required when not using -f/--file."
    ),
)
@click.option(
    "--target-session",
    default=None,
    help=(
        "Target session_id for the channel. "
        "Required when not using -f/--file."
    ),
)
@click.option(
    "--text",
    default=None,
    help=(
        "Content: for 'text' tasks this is the message sent to the channel; "
        "for 'agent' tasks this is the prompt/question sent to the agent. "
        "Required for both task types."
    ),
)
@click.option(
    "--timezone",
    default=None,
    help=(
        "Timezone for the cron schedule (e.g. UTC, America/New_York). "
        "Defaults to the user timezone from config."
    ),
)
@click.option(
    "--enabled/--no-enabled",
    default=True,
    help="Create the job as enabled (--enabled) or disabled (--no-enabled).",
)
@click.option(
    "--mode",
    type=click.Choice(["stream", "final"], case_sensitive=False),
    default="stream",
    help=(
        "Delivery mode: 'stream' sends incremental updates; "
        "'final' sends only the final result."
    ),
)
@click.option(
    "--silent/--no-silent",
    default=False,
    help=(
        "Run an agent task without delivering its response to the channel. "
        "Session, trace, and optional Inbox records are still preserved."
    ),
)
@click.option(
    "--save-result-to-inbox/--no-save-result-to-inbox",
    default=None,
    help=(
        "Whether to save execution results to Inbox. "
        "If omitted, server-side defaults are applied."
    ),
)
@click.option(
    "--share-session/--no-share-session",
    default=True,
    help=(
        "Share session with target user. "
        "When disabled, creates isolated context for each run."
    ),
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=click.IntRange(min=1),
    default=120,
    show_default=True,
    help=(
        "Maximum execution time in seconds for agent tasks. "
        "If the task takes longer, it will be cancelled. "
        "Increase for complex tasks (e.g. --timeout 1800)."
    ),
)
@click.option(
    "--tool-safety/--no-tool-safety",
    default=False,
    show_default=True,
    help=(
        "Tool execution safety check. When enabled, risky tool calls "
        "require approval (may block unattended jobs). "
        "When disabled, all tools execute without approval."
    ),
)
@click.option(
    "--preprocess-script",
    default=None,
    help=(
        "Batch script to run before every fire, by name. The script must "
        "belong to this job (see the job's scripts in the console); a "
        "template's script gets copied in when the template is applied. "
        "For 'text' tasks the collected result is delivered directly; for "
        "'agent' tasks it is injected into the prompt."
    ),
)
@click.option(
    "--preprocess-arg",
    "preprocess_pairs",
    multiple=True,
    metavar="KEY=VALUE",
    help=(
        "Value for the preprocess script's ${args.KEY} placeholders; "
        "repeatable. Example: --preprocess-arg project=QwenPaw"
    ),
)
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def create_job(
    ctx: click.Context,
    file_: Optional[Path],
    task_type: Optional[str],
    schedule_type: str,
    name: Optional[str],
    cron: Optional[str],
    run_at: Optional[str],
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
    channel: Optional[str],
    target_user: Optional[str],
    target_session: Optional[str],
    text: Optional[str],
    timezone: Optional[str],
    enabled: bool,
    mode: str,
    silent: bool,
    save_result_to_inbox: Optional[bool],
    share_session: bool,
    timeout_seconds: int,
    tool_safety: bool,
    preprocess_script: Optional[str],
    preprocess_pairs: tuple[str, ...],
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Create a cron job.

    Either pass -f/--file with a JSON spec, or use --type, --name, --cron,
    --channel, --target-user, --target-session and --text to define the job
    inline.
    """
    if timezone is None:
        from ..config import load_config

        timezone = load_config().user_timezone or "UTC"
    base_url = _base_url(ctx, base_url)
    if file_ is not None:
        payload = json.loads(file_.read_text(encoding="utf-8"))
    else:
        for value, label in [
            (task_type, "--type"),
            (name, "--name"),
            (channel, "--channel"),
            (target_user, "--target-user"),
            (target_session, "--target-session"),
        ]:
            if not value or (isinstance(value, str) and not value.strip()):
                raise click.UsageError(
                    f"When creating without -f/--file, {label} is required",
                )
        if schedule_type == "cron":
            if not (cron and cron.strip()):
                raise click.UsageError(
                    "When --schedule-type is cron, --cron is required",
                )
        elif not (run_at and run_at.strip()):
            raise click.UsageError(
                "When --schedule-type is scheduled, --run-at is required",
            )
        preprocess_args: Optional[Dict[str, str]] = None
        if preprocess_pairs:
            preprocess_args = _parse_key_value_pairs(
                preprocess_pairs,
                "--preprocess-arg",
            )
        payload = _build_spec_from_cli(
            task_type=task_type or "agent",
            schedule_type=schedule_type,
            name=name or "",
            cron=cron or "",
            run_at=run_at,
            repeat_every_days=repeat_every_days,
            repeat_end_type=repeat_end_type,
            repeat_until=repeat_until,
            repeat_count=repeat_count,
            channel=channel or DEFAULT_CHANNEL,
            target_user=target_user or "",
            target_session=target_session or "",
            text=text,
            timezone=timezone,
            enabled=enabled,
            mode=mode,
            silent=silent,
            save_result_to_inbox=save_result_to_inbox,
            share_session=share_session,
            timeout_seconds=timeout_seconds,
            tool_safety=tool_safety,
            preprocess_script=preprocess_script,
            preprocess_args=preprocess_args,
        )
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.post("/cron/jobs", json=payload, headers=headers)
        r.raise_for_status()
        print_json(r.json())


def _resolve_update_spec(
    spec: Dict[str, Any],
    task_type: Optional[str],
    schedule_type: Optional[str],
    name: Optional[str],
    cron: Optional[str],
    run_at: Optional[str],
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
    channel: Optional[str],
    target_user: Optional[str],
    target_session: Optional[str],
    text: Optional[str],
    timezone: Optional[str],
    enabled: Optional[bool],
    mode: Optional[str],
    silent: Optional[bool],
    save_result_to_inbox: Optional[bool],
    share_session: Optional[bool],
    timeout_seconds: Optional[int],
    tool_safety: Optional[bool] = None,
    preprocess_script: Optional[str] = None,
    preprocess_args: Optional[Dict[str, str]] = None,
    remove_preprocess: bool = False,
) -> Dict[str, Any]:
    # pylint: disable=too-many-branches,too-many-statements
    """Merge CLI overrides with an existing cron-job spec.

    Deep-copies the existing spec and only patches fields explicitly
    provided by the CLI.  Unspecified fields — including advanced
    runtime settings (``max_concurrency``, ``misfire_grace_seconds``)
    and request extensions (``model``, ``request_context``, …) — are
    preserved as-is.  Returns a payload dict suitable for
    PUT /cron/jobs/{id}.
    """
    payload = copy.deepcopy(spec)

    if name is not None:
        payload["name"] = name
    if enabled is not None:
        payload["enabled"] = enabled
    if task_type is not None:
        payload["task_type"] = task_type

    # --- schedule ---
    sch = payload.setdefault("schedule", {})
    if schedule_type is not None:
        # CLI exposes "scheduled"; the API model stores it as "once".
        sch["type"] = (
            "once" if schedule_type in ("scheduled", "once") else schedule_type
        )
    if cron is not None:
        sch["cron"] = cron
    if run_at is not None:
        sch["run_at"] = run_at
    if timezone is not None:
        sch["timezone"] = timezone
    if repeat_every_days is not None:
        sch["repeat_every_days"] = repeat_every_days
    if repeat_end_type is not None:
        sch["repeat_end_type"] = repeat_end_type
    if repeat_until is not None:
        sch["repeat_until"] = repeat_until
    if repeat_count is not None:
        sch["repeat_count"] = repeat_count

    # --- dispatch ---
    dsp = payload.setdefault("dispatch", {})
    if channel is not None:
        dsp["channel"] = channel
    if mode is not None:
        dsp["mode"] = mode
    if silent is not None:
        dsp["silent"] = silent
    target = dsp.setdefault("target", {})
    if target_user is not None:
        target["user_id"] = target_user
    if target_session is not None:
        target["session_id"] = target_session

    # --- runtime ---
    run = payload.setdefault("runtime", {})
    if share_session is not None:
        run["share_session"] = share_session
    if timeout_seconds is not None:
        run["timeout_seconds"] = timeout_seconds
    if tool_safety is not None:
        run["tool_safety"] = tool_safety

    # --- text / request ---
    if text is not None:
        if payload.get("task_type") == "agent":
            req = payload.setdefault("request", {})
            try:
                req["input"][0]["content"][0]["text"] = text.strip()
            except (KeyError, IndexError, TypeError):
                req["input"] = [
                    {
                        "role": "user",
                        "type": "message",
                        "content": [{"type": "text", "text": text.strip()}],
                    },
                ]
        else:
            payload["text"] = text.strip()

    # --- preprocess ---
    if remove_preprocess and preprocess_script is not None:
        raise click.UsageError(
            "--remove-preprocess cannot be combined with "
            "--preprocess-script",
        )
    if remove_preprocess:
        payload.pop("preprocess", None)
    elif preprocess_script is not None or preprocess_args is not None:
        block = payload.get("preprocess")
        if not isinstance(block, dict):
            block = {}
        block = dict(block)
        if preprocess_script is not None:
            if not preprocess_script.strip():
                raise click.UsageError(
                    "--preprocess-script must not be empty",
                )
            block["script"] = preprocess_script.strip()
            # script and inline actions are mutually exclusive
            block.pop("actions", None)
        if preprocess_args is not None:
            merged = dict(block.get("args") or {})
            merged.update(preprocess_args)
            block["args"] = _expand_dotted_args(merged)
        if not block.get("script") and block.get("actions") is None:
            raise click.UsageError(
                "--preprocess-arg requires the job to already have a "
                "preprocess script (or pass --preprocess-script)",
            )
        block.setdefault("enabled", True)
        payload["preprocess"] = block

    if save_result_to_inbox is not None:
        payload["save_result_to_inbox"] = save_result_to_inbox

    return payload


@cron_group.command("update")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "-f",
    "--file",
    "file_",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a JSON file containing the full cron job spec. "
        "Mutually exclusive with inline options (--type, --name, etc.)."
    ),
)
@click.option(
    "--type",
    "task_type",
    type=click.Choice(["text", "agent"], case_sensitive=False),
    default=None,
    help="Task type: 'text' or 'agent'.",
)
@click.option(
    "--schedule-type",
    type=click.Choice(["cron", "scheduled"], case_sensitive=False),
    default=None,
    help="Schedule type: 'cron' or 'scheduled'.",
)
@click.option(
    "--name",
    default=None,
    help="Display name for the job.",
)
@click.option(
    "--cron",
    default=None,
    help="Cron expression (5 fields). Example: '0 9 * * *'.",
)
@click.option(
    "--run-at",
    default=None,
    help="Run time for scheduled jobs in ISO 8601 format.",
)
@click.option(
    "--repeat-every-days",
    type=click.IntRange(min=1),
    default=None,
    help="For scheduled: repeat every N days.",
)
@click.option(
    "--repeat-end-type",
    type=click.Choice(["never", "until", "count"], case_sensitive=False),
    default=None,
    help="For scheduled: end condition.",
)
@click.option(
    "--repeat-until",
    default=None,
    help="For scheduled: end date-time (ISO 8601).",
)
@click.option(
    "--repeat-count",
    type=click.IntRange(min=1),
    default=None,
    help="For scheduled: max run count.",
)
@click.option(
    "--channel",
    default=None,
    help="Delivery channel.",
)
@click.option(
    "--target-user",
    default=None,
    help="Target user_id.",
)
@click.option(
    "--target-session",
    default=None,
    help="Target session_id.",
)
@click.option(
    "--text",
    default=None,
    help="Text content or agent prompt.",
)
@click.option(
    "--timezone",
    default=None,
    help="Timezone for the schedule.",
)
@click.option(
    "--enabled/--no-enabled",
    default=None,
    help="Enable or disable the job.",
)
@click.option(
    "--mode",
    type=click.Choice(["stream", "final"], case_sensitive=False),
    default=None,
    help="Delivery mode: 'stream' or 'final'.",
)
@click.option(
    "--silent/--no-silent",
    default=None,
    help="Run an agent task without channel delivery.",
)
@click.option(
    "--save-result-to-inbox/--no-save-result-to-inbox",
    default=None,
    help="Save execution results to Inbox.",
)
@click.option(
    "--share-session/--no-share-session",
    default=None,
    help="Share session with target user.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum execution time in seconds.",
)
@click.option(
    "--tool-safety/--no-tool-safety",
    default=None,
    help=(
        "Tool execution safety check. When enabled, risky tool calls "
        "require approval. When disabled, all tools execute without approval."
    ),
)
@click.option(
    "--preprocess-script",
    default=None,
    help=(
        "Set the batch script run before every fire, by name. The script "
        "must belong to this job. Replaces any previously configured one."
    ),
)
@click.option(
    "--preprocess-arg",
    "preprocess_pairs",
    multiple=True,
    metavar="KEY=VALUE",
    help=(
        "Value for the preprocess script's ${args.KEY} placeholders; "
        "repeatable. Merged over the existing args."
    ),
)
@click.option(
    "--remove-preprocess",
    is_flag=True,
    default=False,
    help="Remove the preprocess block from the job.",
)
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def update_job(
    ctx: click.Context,
    job_id: str,
    file_: Optional[Path],
    task_type: Optional[str],
    schedule_type: Optional[str],
    name: Optional[str],
    cron: Optional[str],
    run_at: Optional[str],
    repeat_every_days: Optional[int],
    repeat_end_type: Optional[str],
    repeat_until: Optional[str],
    repeat_count: Optional[int],
    channel: Optional[str],
    target_user: Optional[str],
    target_session: Optional[str],
    text: Optional[str],
    timezone: Optional[str],
    enabled: Optional[bool],
    mode: Optional[str],
    silent: Optional[bool],
    save_result_to_inbox: Optional[bool],
    share_session: Optional[bool],
    timeout_seconds: Optional[int],
    tool_safety: Optional[bool],
    preprocess_script: Optional[str],
    preprocess_pairs: tuple[str, ...],
    remove_preprocess: bool,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Update an existing cron job.

    Either pass -f/--file with a complete JSON spec to replace the
    job entirely, or specify individual options to override specific
    fields.  Unspecified options keep their current values.
    """
    base_url = _base_url(ctx, base_url)

    # Fetch the existing job first so we can merge partial updates
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.get(f"/cron/jobs/{job_id}", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        existing = r.json()

    if file_ is not None:
        payload = json.loads(file_.read_text(encoding="utf-8"))
    else:
        preprocess_args: Optional[Dict[str, str]] = None
        if preprocess_pairs:
            preprocess_args = _parse_key_value_pairs(
                preprocess_pairs,
                "--preprocess-arg",
            )
        payload = _resolve_update_spec(
            spec=existing.get("spec", existing),
            task_type=task_type,
            schedule_type=schedule_type,
            name=name,
            cron=cron,
            run_at=run_at,
            repeat_every_days=repeat_every_days,
            repeat_end_type=repeat_end_type,
            repeat_until=repeat_until,
            repeat_count=repeat_count,
            channel=channel,
            target_user=target_user,
            target_session=target_session,
            text=text,
            timezone=timezone,
            enabled=enabled,
            mode=mode,
            silent=silent,
            save_result_to_inbox=save_result_to_inbox,
            share_session=share_session,
            timeout_seconds=timeout_seconds,
            tool_safety=tool_safety,
            preprocess_script=preprocess_script,
            preprocess_args=preprocess_args,
            remove_preprocess=remove_preprocess,
        )

    payload["id"] = job_id

    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.put(f"/cron/jobs/{job_id}", json=payload, headers=headers)
        r.raise_for_status()
        print_json(r.json())


@cron_group.command("delete")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def delete_job(
    ctx: click.Context,
    job_id: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Permanently delete a cron job. The job is removed from the server."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.delete(f"/cron/jobs/{job_id}", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        print_json(r.json())


@cron_group.command("pause")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def pause_job(
    ctx: click.Context,
    job_id: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Pause a cron job so it no longer runs on schedule.
    Use 'resume' to re-enable.
    """
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.post(f"/cron/jobs/{job_id}/pause", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        print_json(r.json())


@cron_group.command("resume")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def resume_job(
    ctx: click.Context,
    job_id: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Resume a paused cron job so it runs again on its schedule."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.post(f"/cron/jobs/{job_id}/resume", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        print_json(r.json())


@cron_group.command("run")
@click.argument("job_id", metavar="JOB_ID")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def run_job(
    ctx: click.Context,
    job_id: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Trigger a one-off run of a cron job immediately (ignores schedule)."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        headers = {"X-Agent-Id": agent_id}
        r = c.post(f"/cron/jobs/{job_id}/run", headers=headers)
        if r.status_code == 404:
            raise click.ClickException("Job not found.")
        r.raise_for_status()
        print_json(r.json())


# ---------------------------------------------------------------------------
# Template packages
# ---------------------------------------------------------------------------


def _template_error(response: Any) -> str:
    """Render an API error body as a single human-readable line."""
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        return response.text
    if isinstance(detail, dict):
        if detail.get("conflicts"):
            parts = [
                f"{c.get('name')} (try --rename-to {c.get('suggested_name')})"
                for c in detail["conflicts"]
            ]
            return "Name already taken: " + "; ".join(parts)
        if detail.get("suggested_name"):
            return (
                f"{detail.get('message') or 'conflict'} "
                f"(try --name {detail['suggested_name']})"
            )
        return json.dumps(detail, ensure_ascii=False)
    if isinstance(detail, list):
        return json.dumps(detail, ensure_ascii=False)
    return str(detail)


@cron_group.group("template")
def template_group() -> None:
    """Manage cron job template packages (folder-based, importable).

    A template package is a directory holding TEMPLATE.md (docs +
    metadata), template.json (the job payload), optional batch/*.json
    run_tool_batch scripts, and optional skills/ shipped with it.
    Use export/import to move packages between machines.
    """


@template_group.command("list")
@click.option(
    "--no-builtin",
    is_flag=True,
    default=False,
    help="Only list user templates, hiding packaged builtins.",
)
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def template_list(
    ctx: click.Context,
    no_builtin: bool,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """List template packages. Output is JSON from GET /cron-templates."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        r = c.get(
            "/cron-templates",
            params={"include_builtin": "false" if no_builtin else "true"},
            headers={"X-Agent-Id": agent_id},
        )
        r.raise_for_status()
        print_json(r.json())


@template_group.command("get")
@click.argument("name", metavar="NAME")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def template_get(
    ctx: click.Context,
    name: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Show one template package, including its docs and file list."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        r = c.get(
            f"/cron-templates/{name}",
            headers={"X-Agent-Id": agent_id},
        )
        if r.status_code == 404:
            raise click.ClickException("Template not found.")
        r.raise_for_status()
        print_json(r.json())


@template_group.command("export")
@click.argument("name", metavar="NAME")
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Where to write the zip. Defaults to ./<name>.zip in the "
        "current directory."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite the output file if it already exists.",
)
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def template_export(
    ctx: click.Context,
    name: str,
    output: Optional[Path],
    force: bool,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Export a template package as a zip.

    The archive is rooted at <name>/ so it can be imported as-is with
    'qwenpaw cron template import'.
    """
    base_url = _base_url(ctx, base_url)
    target = output or Path(f"{name}.zip")
    if target.exists() and not force:
        raise click.ClickException(
            f"{target} already exists; pass --force to overwrite.",
        )
    with client(base_url) as c:
        r = c.get(
            f"/cron-templates/{name}/export",
            headers={"X-Agent-Id": agent_id},
        )
        if r.status_code == 404:
            raise click.ClickException("Template not found.")
        if r.status_code >= 400:
            raise click.ClickException(_template_error(r))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
    click.echo(f"Exported {name} -> {target} ({len(r.content)} bytes)")


@template_group.command("import")
@click.argument(
    "zip_path",
    metavar="ZIP_PATH",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--name",
    "target_name",
    default=None,
    help=(
        "Import a single-template zip under this name instead of the "
        "name recorded in the package."
    ),
)
@click.option(
    "--rename-to",
    "rename_pairs",
    multiple=True,
    metavar="OLD=NEW",
    help=(
        "Rename one template while importing; repeatable. Use this to "
        "resolve the conflicts reported by a failed import."
    ),
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Replace existing templates with the same name.",
)
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def template_import(
    ctx: click.Context,
    zip_path: Path,
    target_name: Optional[str],
    rename_pairs: tuple[str, ...],
    overwrite: bool,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Import template packages from a zip file."""
    base_url = _base_url(ctx, base_url)
    rename_map: Dict[str, str] = {}
    for pair in rename_pairs:
        if "=" not in pair:
            raise click.UsageError(
                f"--rename-to expects OLD=NEW, got: {pair}",
            )
        old, new = pair.split("=", 1)
        if not old.strip() or not new.strip():
            raise click.UsageError(
                f"--rename-to expects OLD=NEW, got: {pair}",
            )
        rename_map[old.strip()] = new.strip()

    params: Dict[str, Any] = {"overwrite": str(overwrite).lower()}
    if target_name:
        params["target_name"] = target_name
    if rename_map:
        params["rename_map"] = json.dumps(rename_map)

    with client(base_url) as c:
        with zip_path.open("rb") as handle:
            r = c.post(
                "/cron-templates/upload",
                params=params,
                files={
                    "file": (
                        zip_path.name,
                        handle,
                        "application/zip",
                    ),
                },
                headers={"X-Agent-Id": agent_id},
            )
        if r.status_code >= 400:
            raise click.ClickException(_template_error(r))
        print_json(r.json())


@template_group.command("update")
@click.argument("name", metavar="NAME")
@click.option("--title", default=None, help="New display title.")
@click.option("--description", default=None, help="New description.")
@click.option(
    "--category",
    type=click.Choice(["cron", "once"], case_sensitive=False),
    default=None,
    help="Schedule category the template represents.",
)
@click.option(
    "--frequency",
    default=None,
    help="Display-only frequency label (e.g. 'Weekdays at 09:30').",
)
@click.option("--emoji", default=None, help="Display emoji.")
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help=("Replace the tag list; repeatable. Pass --clear-tags to empty it."),
)
@click.option(
    "--clear-tags",
    is_flag=True,
    default=False,
    help="Remove all tags.",
)
@click.option(
    "--version-text",
    default=None,
    help="Version string recorded in the package metadata.",
)
@click.option(
    "--batch-entry",
    default=None,
    help=(
        "Package-relative path of the run_tool_batch entry file. "
        "Pass an empty string to clear it."
    ),
)
@click.option(
    "--add-batch",
    "add_batch",
    multiple=True,
    metavar="NAME=PATH",
    help=(
        "Add or replace a batch file from a local JSON file; repeatable. "
        "Example: --add-batch collect=./collect.json"
    ),
)
@click.option(
    "--remove-batch",
    "remove_batch",
    multiple=True,
    metavar="NAME",
    help="Delete a batch file by name; repeatable.",
)
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
# pylint: disable-next=too-many-locals
def template_update(
    ctx: click.Context,
    name: str,
    title: Optional[str],
    description: Optional[str],
    category: Optional[str],
    frequency: Optional[str],
    emoji: Optional[str],
    tags: tuple[str, ...],
    clear_tags: bool,
    version_text: Optional[str],
    batch_entry: Optional[str],
    add_batch: tuple[str, ...],
    remove_batch: tuple[str, ...],
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Update an existing template package.

    Only the options you pass are changed; batch scripts and bundled
    skills you do not mention are left in place. Builtin packages are
    read-only — run 'fork' first.
    """
    base_url = _base_url(ctx, base_url)
    payload: Dict[str, Any] = {}
    for key, value in (
        ("title", title),
        ("description", description),
        ("category", category),
        ("frequency", frequency),
        ("emoji", emoji),
        ("version_text", version_text),
        ("batch_entry", batch_entry),
    ):
        if value is not None:
            payload[key] = value
    if clear_tags:
        payload["tags"] = []
    elif tags:
        payload["tags"] = list(tags)

    if add_batch:
        batch_files: Dict[str, str] = {}
        for pair in add_batch:
            if "=" not in pair:
                raise click.UsageError(
                    f"--add-batch expects NAME=PATH, got: {pair}",
                )
            batch_name, file_path = pair.split("=", 1)
            batch_name = batch_name.strip()
            source = Path(file_path.strip()).expanduser()
            if not batch_name or not source.is_file():
                raise click.UsageError(
                    f"--add-batch expects NAME=PATH with an existing file, "
                    f"got: {pair}",
                )
            batch_files[batch_name] = source.read_text(encoding="utf-8")
        payload["batch_files"] = batch_files
    if remove_batch:
        payload["remove_batch_files"] = list(remove_batch)

    if not payload:
        raise click.UsageError(
            "Nothing to update; pass at least one field option.",
        )

    with client(base_url) as c:
        r = c.put(
            f"/cron-templates/{name}",
            json=payload,
            headers={"X-Agent-Id": agent_id},
        )
        if r.status_code == 404:
            raise click.ClickException("Template not found.")
        if r.status_code >= 400:
            raise click.ClickException(_template_error(r))
        print_json(r.json())


@template_group.command("delete")
@click.argument("name", metavar="NAME")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def template_delete(
    ctx: click.Context,
    name: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Delete a user template package. Builtins cannot be deleted."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        r = c.delete(
            f"/cron-templates/{name}",
            headers={"X-Agent-Id": agent_id},
        )
        if r.status_code == 404:
            raise click.ClickException("Template not found.")
        if r.status_code >= 400:
            raise click.ClickException(_template_error(r))
        print_json(r.json())


@template_group.command("fork")
@click.argument("name", metavar="NAME")
@click.option(
    "--base-url",
    default=None,
    help="Override the API base URL. Defaults to global --host/--port.",
)
@click.option(
    "--agent-id",
    default="default",
    help="Agent ID (defaults to 'default')",
)
@click.pass_context
def template_fork(
    ctx: click.Context,
    name: str,
    base_url: Optional[str],
    agent_id: str,
) -> None:
    """Copy a packaged builtin into the user pool so it becomes editable."""
    base_url = _base_url(ctx, base_url)
    with client(base_url) as c:
        r = c.post(
            f"/cron-templates/{name}/fork",
            headers={"X-Agent-Id": agent_id},
        )
        if r.status_code == 404:
            raise click.ClickException("Builtin template not found.")
        if r.status_code >= 400:
            raise click.ClickException(_template_error(r))
        print_json(r.json())
