# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from qwenpaw.app.crons.models import (
    CronJobSpec,
    DispatchSpec,
    DispatchTarget,
    ScheduleSpec,
    _crontab_dow_to_name,
)
from tests.unit.app.conftest import make_cron_job_spec


# ---------------------------------------------------------------------------
# _crontab_dow_to_name — crontab numeric DOW → abbreviation
# ---------------------------------------------------------------------------


def test_dow_wildcard_passthrough():
    assert _crontab_dow_to_name("*") == "*"


def test_dow_single_numeric_to_name():
    assert _crontab_dow_to_name("0") == "sun"
    assert _crontab_dow_to_name("1") == "mon"
    assert _crontab_dow_to_name("7") == "sun"  # crontab 7 = Sunday


def test_dow_named_passthrough():
    # Already-named values must not be mutated.
    assert _crontab_dow_to_name("mon") == "mon"
    assert _crontab_dow_to_name("fri") == "fri"


def test_dow_comma_list():
    assert _crontab_dow_to_name("1,3,5") == "mon,wed,fri"


def test_dow_range():
    assert _crontab_dow_to_name("1-5") == "mon-fri"


def test_dow_step():
    # */2 on DOW field: wildcard base with step
    assert _crontab_dow_to_name("*/2") == "*/2"


# ---------------------------------------------------------------------------
# ScheduleSpec — cron type
# ---------------------------------------------------------------------------


def test_schedule_cron_normalizes_5_fields():
    spec = ScheduleSpec(type="cron", cron="0 9 * * 1")
    assert spec.cron == "0 9 * * mon"


def test_schedule_cron_normalizes_4_fields():
    spec = ScheduleSpec(type="cron", cron="9 * * 1")
    assert spec.cron == "0 9 * * mon"


def test_schedule_cron_named_dow_unchanged():
    spec = ScheduleSpec(type="cron", cron="0 9 * * mon")
    assert spec.cron == "0 9 * * mon"


def test_schedule_cron_rejects_empty():
    with pytest.raises(ValidationError, match="cron is empty"):
        ScheduleSpec(type="cron", cron="")


def test_schedule_cron_rejects_6_fields():
    with pytest.raises(ValidationError):
        ScheduleSpec(type="cron", cron="0 0 9 * * mon")


def test_schedule_once_requires_run_at():
    with pytest.raises(ValidationError, match="run_at is missing"):
        ScheduleSpec(type="once")


# ---------------------------------------------------------------------------
# CronJobSpec validation
# ---------------------------------------------------------------------------


def test_cron_job_spec_agent_syncs_request_with_target():
    spec = make_cron_job_spec(user_id="alice", session_id="console:alice")
    assert spec.request is not None
    assert spec.request.user_id == "alice"
    assert spec.request.session_id == "console:alice"


def test_cron_job_spec_text_rejects_empty_text():
    # Message widened when preprocess became an alternative payload source;
    # a text job with neither is still rejected.
    with pytest.raises(
        ValidationError,
        match="text and preprocess are empty",
    ):
        CronJobSpec(
            name="Bad",
            schedule=ScheduleSpec(type="cron", cron="0 9 * * mon"),
            task_type="text",
            text="",
            dispatch=DispatchSpec(
                target=DispatchTarget(user_id="u1", session_id="console:u1"),
            ),
        )


def test_dispatch_silent_defaults_to_false():
    dispatch = DispatchSpec(
        target=DispatchTarget(user_id="u1", session_id="console:u1"),
    )

    assert dispatch.silent is False


def test_cron_job_spec_agent_accepts_silent_delivery():
    payload = make_cron_job_spec().model_dump(mode="json")
    payload["dispatch"]["silent"] = True

    spec = CronJobSpec.model_validate(payload)

    assert spec.dispatch.silent is True


def test_cron_job_spec_text_rejects_silent_delivery():
    with pytest.raises(
        ValidationError,
        match="silent delivery is only supported for agent tasks",
    ):
        CronJobSpec(
            name="Silent text",
            schedule=ScheduleSpec(type="cron", cron="0 9 * * mon"),
            task_type="text",
            text="Hello",
            dispatch=DispatchSpec(
                target=DispatchTarget(
                    user_id="u1",
                    session_id="console:u1",
                ),
                silent=True,
            ),
        )


# ---------------------------------------------------------------------------
# PreprocessSpec
# ---------------------------------------------------------------------------


def test_preprocess_step_caps_match_run_tool_batch():
    """Guards the re-declared constants.

    ``models.py`` re-declares these instead of importing them, to keep
    reading ``jobs.json`` from pulling in the whole ``agents.tools``
    package. That trade-off is only safe while the numbers agree — a drift
    would let a job be saved that the executor then rejects at run time.
    """
    from importlib import import_module

    from qwenpaw.app.crons.models import (
        DEFAULT_PREPROCESS_MAX_EXECUTION_STEPS,
        MAX_PREPROCESS_STEPS,
    )

    rtb = import_module("qwenpaw.agents.tools.run_tool_batch")
    assert MAX_PREPROCESS_STEPS == rtb.MAX_BATCH_STEPS
    assert (
        DEFAULT_PREPROCESS_MAX_EXECUTION_STEPS
        == rtb.DEFAULT_MAX_EXECUTION_STEPS
    )


def test_preprocess_requires_at_least_one_script():
    from qwenpaw.app.crons.models import PreprocessSpec

    with pytest.raises(ValidationError, match="at least one script"):
        PreprocessSpec()


def test_preprocess_step_requires_exactly_one_source():
    from qwenpaw.app.crons.models import PreprocessSpec

    with pytest.raises(ValidationError, match="exactly one of"):
        PreprocessSpec(script="a", actions=[{"tool_name": "x"}])
    with pytest.raises(ValidationError, match="exactly one of"):
        PreprocessSpec(
            steps=[{"script": "a", "actions": [{"tool_name": "x"}]}],
        )


def test_preprocess_rejects_mixing_steps_with_the_legacy_form():
    """Two sources of truth for the same thing is how one gets ignored."""
    from qwenpaw.app.crons.models import PreprocessSpec

    with pytest.raises(ValidationError, match="not both"):
        PreprocessSpec(script="a", steps=[{"script": "b"}])


def test_preprocess_folds_the_legacy_form_into_one_step():
    """A jobs.json written before chaining existed must still load.

    The legacy fields are cleared afterwards so everything downstream has
    exactly one place to read from.
    """
    from qwenpaw.app.crons.models import PreprocessSpec

    spec = PreprocessSpec(script="collect", args={"path": "/tmp"})
    assert [step.script for step in spec.steps] == ["collect"]
    assert spec.steps[0].args == {"path": "/tmp"}
    assert spec.script is None and spec.args == {}

    inline = PreprocessSpec(actions=[{"tool_name": "x"}])
    assert inline.steps[0].actions == [{"tool_name": "x"}]
    assert inline.actions is None


def test_preprocess_accepts_a_chain_of_scripts():
    from qwenpaw.app.crons.models import PreprocessSpec

    spec = PreprocessSpec(
        steps=[{"script": "a", "args": {"x": "1"}}, {"script": "b"}],
    )
    assert [step.script for step in spec.steps] == ["a", "b"]
    # Args are per step, so one script cannot see another's values.
    assert spec.steps[0].args == {"x": "1"}
    assert spec.steps[1].args == {}


def test_preprocess_enforces_the_script_chain_cap():
    from qwenpaw.app.crons.models import (
        MAX_PREPROCESS_SCRIPTS,
        PreprocessSpec,
    )

    ok = [{"script": f"s{i}"} for i in range(MAX_PREPROCESS_SCRIPTS)]
    assert len(PreprocessSpec(steps=ok).steps) == MAX_PREPROCESS_SCRIPTS
    with pytest.raises(ValidationError, match="maximum is"):
        PreprocessSpec(steps=[*ok, {"script": "one-too-many"}])


def test_preprocess_rejects_empty_actions():
    from qwenpaw.app.crons.models import PreprocessSpec

    with pytest.raises(ValidationError, match="must be non-empty"):
        PreprocessSpec(actions=[])


def test_preprocess_enforces_the_step_cap():
    from qwenpaw.app.crons.models import (
        MAX_PREPROCESS_STEPS,
        PreprocessSpec,
    )

    too_many = [{"tool_name": "x"}] * (MAX_PREPROCESS_STEPS + 1)
    with pytest.raises(ValidationError, match="maximum is"):
        PreprocessSpec(actions=too_many)


def test_preprocess_defaults_favour_prompt_size():
    """last_only defaults True here, unlike run_tool_batch's own default."""
    from qwenpaw.app.crons.models import PreprocessSpec

    spec = PreprocessSpec(script="collect")
    assert spec.last_only is True
    assert spec.enabled is True
    assert spec.stop_on_error is True
    assert spec.on_failure == "continue"
    assert spec.timeout_seconds == 120


def test_preprocess_does_not_validate_script_existence():
    """A deleted script must not make jobs.json unparseable."""
    from qwenpaw.app.crons.models import PreprocessSpec

    spec = PreprocessSpec(script="does-not-exist")
    assert spec.steps[0].script == "does-not-exist"


# ---------------------------------------------------------------------------
# How preprocess changes CronJobSpec's rules
# ---------------------------------------------------------------------------


def text_job(**overrides):
    payload = {
        "name": "T",
        "schedule": ScheduleSpec(type="cron", cron="0 9 * * mon"),
        "task_type": "text",
        "dispatch": DispatchSpec(
            target=DispatchTarget(user_id="u1", session_id="console:u1"),
        ),
    }
    payload.update(overrides)
    return CronJobSpec(**payload)


def test_text_still_required_without_preprocess():
    with pytest.raises(ValidationError, match="text and preprocess are empty"):
        text_job()


def test_text_may_be_empty_when_preprocess_supplies_the_payload():
    spec = text_job(preprocess={"script": "collect"})
    assert spec.text is None
    assert spec.has_preprocess is True


def test_disabled_preprocess_does_not_satisfy_the_text_requirement():
    with pytest.raises(ValidationError, match="text and preprocess are empty"):
        text_job(preprocess={"script": "collect", "enabled": False})


def test_has_preprocess_is_false_without_one():
    assert make_cron_job_spec().has_preprocess is False


def test_inbox_default_off_for_plain_text_cron():
    """Unchanged: a fixed reminder repeats verbatim, not worth archiving."""
    assert text_job(text="hi").save_result_to_inbox is False


def test_inbox_default_on_for_text_cron_with_preprocess():
    """Every run collects fresh data, so the default inverts."""
    spec = text_job(preprocess={"script": "collect"})
    assert spec.save_result_to_inbox is True


def test_inbox_default_respects_an_explicit_choice():
    spec = text_job(
        text="hi",
        preprocess={"script": "collect"},
        save_result_to_inbox=False,
    )
    assert spec.save_result_to_inbox is False


def test_preprocess_survives_a_round_trip():
    spec = make_cron_job_spec(
        preprocess={"script": "collect", "args": {"path": "/tmp"}},
    )
    restored = CronJobSpec.model_validate(spec.model_dump(mode="json"))
    assert restored.preprocess is not None
    assert restored.preprocess.steps[0].script == "collect"
    assert restored.preprocess.steps[0].args == {"path": "/tmp"}


def test_preprocess_chain_survives_a_round_trip():
    """The dump must reload: the legacy fields are cleared on the way in,
    so a chain has to round-trip through `steps` alone."""
    spec = make_cron_job_spec(
        preprocess={
            "steps": [
                {"script": "a", "args": {"x": "1"}},
                {"actions": [{"tool_name": "t"}]},
            ],
        },
    )
    restored = CronJobSpec.model_validate(spec.model_dump(mode="json"))
    assert restored.preprocess is not None
    steps = restored.preprocess.steps
    assert [step.script for step in steps] == ["a", None]
    assert steps[0].args == {"x": "1"}
    assert steps[1].actions == [{"tool_name": "t"}]
