# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from ..channels.schema import DEFAULT_CHANNEL

# ---------------------------------------------------------------------------
# APScheduler v3 uses ISO 8601 weekday numbering (0=Mon … 6=Sun) for
# CronTrigger(day_of_week=...), while standard crontab uses 0=Sun … 6=Sat.
# from_crontab() does NOT convert either.  Three-letter English abbreviations
# (mon, tue, …, sun) are unambiguous in both systems, so we normalise the
# 5th cron field to abbreviations at validation time.
# ---------------------------------------------------------------------------

_CRONTAB_NUM_TO_NAME: dict[str, str] = {
    "0": "sun",
    "1": "mon",
    "2": "tue",
    "3": "wed",
    "4": "thu",
    "5": "fri",
    "6": "sat",
    "7": "sun",
}


def _crontab_dow_to_name(field: str) -> str:
    """Convert the day-of-week field from crontab numbers to abbreviations.

    Handles: ``*``, single values, comma-separated lists, and ranges.
    Already-named values (``mon``, ``tue``, …) are passed through unchanged.
    """
    if field == "*":
        return field

    def _convert_token(tok: str) -> str:
        if "/" in tok:
            base, step = tok.rsplit("/", 1)
            return f"{_convert_token(base)}/{step}"
        if "-" in tok:
            parts = tok.split("-", 1)
            return "-".join(_CRONTAB_NUM_TO_NAME.get(p, p) for p in parts)
        return _CRONTAB_NUM_TO_NAME.get(tok, tok)

    return ",".join(_convert_token(t) for t in field.split(","))


class ScheduleSpec(BaseModel):
    type: Literal["cron", "once"] = "cron"
    cron: Optional[str] = None
    run_at: Optional[datetime] = None
    timezone: str = "UTC"
    repeat_every_days: Optional[int] = Field(default=None, ge=1)
    repeat_end_type: Optional[Literal["never", "until", "count"]] = None
    repeat_until: Optional[datetime] = None
    repeat_count: Optional[int] = Field(default=None, ge=1)

    @classmethod
    def normalize_cron_5_fields(cls, v: str) -> str:
        parts = [p for p in v.split() if p]
        if len(parts) == 5:
            parts[4] = _crontab_dow_to_name(parts[4])
            return " ".join(parts)

        if len(parts) == 4:
            # treat as: hour dom month dow
            hour, dom, month, dow = parts
            return f"0 {hour} {dom} {month} {_crontab_dow_to_name(dow)}"

        if len(parts) == 3:
            # treat as: dom month dow
            dom, month, dow = parts
            return f"0 0 {dom} {month} {_crontab_dow_to_name(dow)}"

        # 6 fields (seconds) or too short: reject
        raise ValueError(
            "cron must have 5 fields (or 4/3 fields that can be "
            "normalized); seconds not supported",
        )

    @model_validator(mode="after")
    def _validate_schedule_type(self) -> "ScheduleSpec":
        if self.type == "cron":
            if not (self.cron and self.cron.strip()):
                raise ValueError("schedule.type is cron but cron is empty")
            self.cron = self.normalize_cron_5_fields(self.cron)
            self.run_at = None
            self.repeat_every_days = None
            self.repeat_end_type = None
            self.repeat_until = None
            self.repeat_count = None
            return self

        if self.run_at is None:
            raise ValueError("schedule.type is once but run_at is missing")
        self.cron = None
        if self.repeat_every_days is None:
            self.repeat_end_type = None
            self.repeat_until = None
            self.repeat_count = None
            return self

        if self.repeat_end_type is None:
            self.repeat_end_type = "never"

        if self.repeat_end_type == "never":
            self.repeat_until = None
            self.repeat_count = None
            return self

        if self.repeat_end_type == "until":
            if self.repeat_until is None:
                raise ValueError(
                    "repeat_end_type is until but repeat_until is missing",
                )
            if self.repeat_until <= self.run_at:
                raise ValueError(
                    "repeat_until must be later than run_at "
                    "(deadline must be after execution time)",
                )
            self.repeat_count = None
            return self

        if self.repeat_count is None:
            raise ValueError(
                "repeat_end_type is count but repeat_count is missing",
            )
        self.repeat_until = None
        return self


class DispatchTarget(BaseModel):
    user_id: str
    session_id: str


class DispatchSpec(BaseModel):
    type: Literal["channel"] = "channel"
    channel: str = Field(default=DEFAULT_CHANNEL)
    target: DispatchTarget
    mode: Literal["stream", "final"] = Field(default="stream")
    silent: bool = Field(
        default=False,
        description=(
            "Run an agent task without delivering its events to the channel."
        ),
    )
    meta: Dict[str, Any] = Field(default_factory=dict)


class JobRuntimeSpec(BaseModel):
    max_concurrency: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=120, ge=1)
    misfire_grace_seconds: int = Field(default=600, ge=0)
    share_session: bool = Field(
        default=True,
        description=(
            "Whether to share session with target user. "
            "If False, creates isolated context with unique run ID."
        ),
    )
    tool_safety: bool = Field(
        default=False,
        description=(
            "Tool execution safety for this cron job. "
            "When enabled (True), uses AUTO mode — risky tools require "
            "approval (may block unattended execution). "
            "When disabled (False), uses OFF mode — all tools execute "
            "without approval checks, suitable for trusted automated tasks."
        ),
    )


#: Mirrors ``run_tool_batch``'s own caps. Re-declared rather than imported
#: because importing that module pulls in the whole ``agents.tools``
#: package (psutil, html2text, browser control) and this module is loaded
#: every time ``jobs.json`` is read. ``test_models.py`` asserts the two stay
#: equal, so a drift fails loudly instead of silently accepting a batch the
#: executor will reject.
MAX_PREPROCESS_STEPS = 50
DEFAULT_PREPROCESS_MAX_EXECUTION_STEPS = 500


class PreprocessSpec(BaseModel):
    """A ``run_tool_batch`` script run deterministically before each fire.

    Not a tool the model may choose: the batch runs first, every time,
    unconditionally. That is the point — collecting data needs no reasoning,
    so making the LLM decide to collect it wastes a whole turn.

    Applies to both task types, with different destinations for the result:

    * ``agent`` — the call and its result are appended to the user prompt,
      so the model starts with the data already in hand.
    * ``text`` — the result is delivered to the user directly; no LLM is
      involved at any point.
    """

    enabled: bool = True
    #: Script name in the shared batch pool, with or without ``.json``.
    #: Mutually exclusive with ``actions``.
    script: Optional[str] = None
    #: Inline actions, for a one-off not worth pooling.
    actions: Optional[list[dict[str, Any]]] = None
    #: Values for the script's ``${args.<name>}`` placeholders.
    args: Dict[str, Any] = Field(default_factory=dict)
    #: Default True, unlike ``run_tool_batch``'s own False: this output goes
    #: into a prompt on a schedule, so full per-step results for a 20-step
    #: batch would compound in token cost every single run.
    last_only: bool = True
    stop_on_error: bool = True
    maxstep: int = Field(
        default=DEFAULT_PREPROCESS_MAX_EXECUTION_STEPS,
        ge=1,
    )
    #: Wall-clock bound. ``run_tool_batch`` caps step *count*, not time, and
    #: a step may carry an arbitrary ``wait`` — so without this a hung
    #: command would hold the job's concurrency slot forever.
    timeout_seconds: int = Field(default=120, ge=1)
    #: ``continue`` reports the failure to the agent / user and carries on;
    #: ``abort`` skips delivery entirely.
    on_failure: Literal["continue", "abort"] = "continue"

    @model_validator(mode="after")
    def _validate_source(self) -> "PreprocessSpec":
        # `is None` rather than truthiness: `actions=[]` means "provided but
        # empty", which deserves its own message instead of being reported
        # as "you gave me neither source".
        has_script = bool(self.script and self.script.strip())
        has_actions = self.actions is not None
        if has_script == has_actions:
            raise ValueError(
                "preprocess requires exactly one of 'script' or 'actions'",
            )
        if self.actions is not None:
            if not self.actions:
                raise ValueError("preprocess.actions must be non-empty")
            if len(self.actions) > MAX_PREPROCESS_STEPS:
                raise ValueError(
                    f"preprocess.actions has {len(self.actions)} steps; "
                    f"maximum is {MAX_PREPROCESS_STEPS}",
                )
        return self


class CronJobRequest(BaseModel):
    """Passthrough payload to workspace.stream_query(request=...).

    This is aligned with AgentRequest(extra="allow"). We keep it permissive.
    """

    model_config = ConfigDict(extra="allow")

    input: Optional[Any] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None


TaskType = Literal["text", "agent"]


class CronJobSpec(BaseModel):
    id: Optional[str] = None
    name: str
    enabled: bool = True

    schedule: ScheduleSpec
    task_type: TaskType = "agent"
    text: Optional[str] = None
    request: Optional[CronJobRequest] = None
    dispatch: DispatchSpec
    save_result_to_inbox: Optional[bool] = None

    runtime: JobRuntimeSpec = Field(default_factory=JobRuntimeSpec)
    #: Optional batch script run before every fire. Applies to both task
    #: types; part of the task definition rather than a runtime knob, hence
    #: top-level and not inside ``runtime``.
    preprocess: Optional[PreprocessSpec] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

    @property
    def has_preprocess(self) -> bool:
        """Whether a preprocess batch will actually run."""
        return self.preprocess is not None and self.preprocess.enabled

    @model_validator(mode="after")
    def _validate_task_type_fields(self) -> "CronJobSpec":
        if self.task_type == "text":
            # A preprocess makes the *result* the payload, so fixed text
            # becomes an optional lead-in rather than the whole message.
            if (
                not (self.text and self.text.strip())
                and not self.has_preprocess
            ):
                raise ValueError(
                    "task_type is text but both text and preprocess are empty",
                )
            if self.dispatch.silent:
                raise ValueError(
                    "silent delivery is only supported for agent tasks",
                )
            self.request = None
        elif self.task_type == "agent":
            if self.request is None:
                raise ValueError("task_type is agent but request is missing")
            # Keep request.user_id and request.session_id in sync with target
            target = self.dispatch.target
            self.request = self.request.model_copy(
                update={
                    "user_id": target.user_id,
                    "session_id": target.session_id,
                },
            )
        if self.save_result_to_inbox is None:
            # Product rule:
            # - text + recurring(cron) => default OFF, because a fixed
            #   reminder repeats verbatim and is not worth archiving...
            # - ...unless a preprocess is attached, in which case every run
            #   produces fresh collected data that is worth keeping
            # - all other combinations => default ON
            self.save_result_to_inbox = not (
                self.task_type == "text"
                and self.schedule.type == "cron"
                and not self.has_preprocess
            )
        return self


class JobsFile(BaseModel):
    version: int = 2
    jobs: list[CronJobSpec] = Field(default_factory=list)


class CronJobState(BaseModel):
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[
        Literal["success", "error", "running", "skipped", "cancelled"]
    ] = None
    last_error: Optional[str] = None


class CronExecutionRecord(BaseModel):
    run_at: datetime
    status: Literal["success", "error", "running", "skipped", "cancelled"]
    error: Optional[str] = None
    trigger: Literal["scheduled", "manual"] = "scheduled"


class CronJobView(BaseModel):
    spec: CronJobSpec
    state: CronJobState = Field(default_factory=CronJobState)


class CronDispatchTargetItem(BaseModel):
    channel: str
    user_id: str
    session_id: str


class CronDispatchTargetsResponse(BaseModel):
    channels: list[str] = Field(default_factory=list)
    items: list[CronDispatchTargetItem] = Field(default_factory=list)
