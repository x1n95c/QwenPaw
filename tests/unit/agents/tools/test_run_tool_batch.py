# -*- coding: utf-8 -*-
"""Tests for ``run_tool_batch``'s pure logic.

Written as a safety net before the cron preprocess feature builds on this
module, and before its ``_build_label_map`` / ``_ARG_REF_INLINE_PATTERN``
gain a second consumer in the batch-script pool validator. Everything
asserted here is behaviour the pool and the visual editor now depend on
matching exactly.

Only helpers that need no Toolkit are exercised directly; the step loop is
driven through a patched ``_call_tool``.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

# `agents/tools/__init__.py` rebinds the name `run_tool_batch` to the
# *function*, shadowing the submodule — so both `from ... import
# run_tool_batch` and `import ....run_tool_batch as rtb` hand back the
# function. import_module is the only form that yields the module.
rtb = import_module("qwenpaw.agents.tools.run_tool_batch")


# ---------------------------------------------------------------------------
# _load_batch_file
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


ACTION = {"tool_name": "noop", "arguments": {}}


def test_load_accepts_bare_array(tmp_path: Path):
    path = write_json(tmp_path / "b.json", [ACTION])
    assert rtb._load_batch_file(str(path)) == [ACTION]


def test_load_accepts_actions_object(tmp_path: Path):
    path = write_json(tmp_path / "b.json", {"actions": [ACTION]})
    assert rtb._load_batch_file(str(path)) == [ACTION]


def test_load_ignores_sibling_metadata_keys(tmp_path: Path):
    """The pool stores title/description alongside ``actions``."""
    path = write_json(
        tmp_path / "b.json",
        {"title": "t", "description": "d", "actions": [ACTION]},
    )
    assert rtb._load_batch_file(str(path)) == [ACTION]


def test_load_rejects_non_json_suffix(tmp_path: Path):
    path = tmp_path / "b.txt"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must point to a .json file"):
        rtb._load_batch_file(str(path))


def test_load_rejects_missing_file(tmp_path: Path):
    with pytest.raises(ValueError, match="Batch file not found"):
        rtb._load_batch_file(str(tmp_path / "nope.json"))


def test_load_rejects_empty_path():
    with pytest.raises(ValueError, match="file_path is required"):
        rtb._load_batch_file("   ")


def test_load_rejects_malformed_json(tmp_path: Path):
    path = tmp_path / "b.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        rtb._load_batch_file(str(path))


def test_load_rejects_wrong_top_level_shape(tmp_path: Path):
    path = write_json(tmp_path / "b.json", {"steps": []})
    with pytest.raises(ValueError, match="array of actions"):
        rtb._load_batch_file(str(path))


def test_load_resolves_relative_path(tmp_path: Path, monkeypatch):
    """Docstring says absolute, but expanduser+resolve accepts relative."""
    write_json(tmp_path / "b.json", [ACTION])
    monkeypatch.chdir(tmp_path)
    assert rtb._load_batch_file("b.json") == [ACTION]


# ---------------------------------------------------------------------------
# _resolve_args — the type-preservation rule the editor depends on
# ---------------------------------------------------------------------------


def test_exact_match_preserves_type():
    args = {"n": 7, "flag": True, "obj": {"a": 1}, "lst": [1, 2]}
    assert rtb._resolve_args("${args.n}", args) == 7
    assert rtb._resolve_args("${args.flag}", args) is True
    assert rtb._resolve_args("${args.obj}", args) == {"a": 1}
    assert rtb._resolve_args("${args.lst}", args) == [1, 2]


def test_inline_stringifies_non_strings():
    args = {"n": 7, "obj": {"a": 1}}
    assert rtb._resolve_args("n=${args.n}", args) == "n=7"
    assert rtb._resolve_args("o=${args.obj}", args) == 'o={"a": 1}'


def test_inline_keeps_strings_verbatim():
    assert rtb._resolve_args("cd ${args.p}", {"p": "/a b"}) == "cd /a b"


def test_resolve_args_recurses_into_containers():
    out = rtb._resolve_args(
        {"cmd": ["echo", "${args.x}"], "nested": {"k": "${args.x}"}},
        {"x": 5},
    )
    assert out == {"cmd": ["echo", 5], "nested": {"k": 5}}


def test_resolve_args_dotted_path():
    assert rtb._resolve_args("${args.a.b}", {"a": {"b": "v"}}) == "v"


def test_resolve_args_missing_raises():
    with pytest.raises(ValueError, match="Missing arg"):
        rtb._resolve_args("${args.gone}", {})


def test_bare_dollar_args_is_not_a_placeholder():
    """Only the brace form is recognised (module comment at :503)."""
    assert rtb._resolve_args("$args.x", {"x": 1}) == "$args.x"


@pytest.mark.parametrize("name", ["a", "A9", "a_b", "a-b", "a.b"])
def test_arg_name_charset(name: str):
    """Pinned because the pool validator reuses this exact pattern."""
    assert rtb._ARG_REF_INLINE_PATTERN.findall(f"${{args.{name}}}") == [name]


def test_arg_name_rejects_spaces():
    assert rtb._ARG_REF_INLINE_PATTERN.findall("${args.a b}") == []


# ---------------------------------------------------------------------------
# resolve_step_refs — positional, latest-result-wins
# ---------------------------------------------------------------------------


def results_fixture() -> list[dict[str, Any]]:
    return [
        {"step": 0, "ok": True, "text": "first"},
        {"step": 1, "ok": True, "value": 42, "items": ["a", "b"]},
    ]


def test_step_ref_exact_preserves_type():
    out = rtb.resolve_step_refs("${steps.1.value}", results_fixture())
    assert out == 42 and isinstance(out, int)


def test_step_ref_without_path_returns_whole_result():
    out = rtb.resolve_step_refs("${steps.0}", results_fixture())
    assert out["text"] == "first"


def test_step_ref_inline_stringifies():
    out = rtb.resolve_step_refs("v=${steps.1.value}", results_fixture())
    assert out == "v=42"


def test_step_ref_list_index():
    out = rtb.resolve_step_refs("${steps.1.items.1}", results_fixture())
    assert out == "b"


def test_step_ref_latest_execution_wins():
    """A loop re-running an action must resolve to its newest result."""
    results = [
        {"step": 0, "text": "old"},
        {"step": 1, "text": "other"},
        {"step": 0, "text": "new"},
    ]
    assert rtb.resolve_step_refs("${steps.0.text}", results) == "new"


def test_forward_step_ref_raises():
    """Straight-line forward references can never resolve."""
    with pytest.raises(ValueError, match="has no result"):
        rtb.resolve_step_refs("${steps.5.text}", results_fixture())


def test_step_ref_missing_key_raises():
    with pytest.raises(ValueError, match="Missing key"):
        rtb.resolve_step_refs("${steps.0.nope}", results_fixture())


def test_step_ref_list_index_out_of_range_raises():
    with pytest.raises(ValueError, match="out of range"):
        rtb.resolve_step_refs("${steps.1.items.9}", results_fixture())


def test_step_ref_non_digit_list_index_raises():
    with pytest.raises(ValueError, match="Invalid list index"):
        rtb.resolve_step_refs("${steps.1.items.x}", results_fixture())


def test_var_ref_exact_and_inline():
    variables = {"i": 3, "s": "hi"}
    assert rtb.resolve_step_refs("${vars.i}", [], variables) == 3
    assert rtb.resolve_step_refs("i=${vars.i}", [], variables) == "i=3"
    assert rtb.resolve_step_refs("${vars.s}", [], variables) == "hi"


def test_var_ref_missing_raises():
    with pytest.raises(ValueError, match="Missing var reference"):
        rtb.resolve_step_refs("${vars.gone}", [], {})


# ---------------------------------------------------------------------------
# _parse_scalar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("false", False),
        ("7", 7),
        ("-7", -7),
        (" 7 ", 7),
        ("7.5", "7.5"),  # floats are NOT parsed
        ("abc", "abc"),
    ],
)
def test_parse_scalar(raw: str, expected: Any):
    assert rtb._parse_scalar(raw) == expected


def test_parse_scalar_passes_non_strings_through():
    assert rtb._parse_scalar(1.5) == 1.5
    assert rtb._parse_scalar(None) is None


# ---------------------------------------------------------------------------
# _evaluate_condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cond", "expected"),
    [
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("1>2", False),
        ("2>1", True),
        ("1==1", True),
        ("1!=1", False),
        ("2<=2", True),
        ("3>=4", False),
    ],
)
def test_condition_literals(cond: str, expected: bool):
    assert rtb._evaluate_condition(cond, [], {}) is expected


def test_condition_reads_vars_by_bare_name():
    assert rtb._evaluate_condition("i<5", [], {"i": 3}) is True


def test_condition_reads_placeholders():
    results = [{"step": 0, "value": 10}]
    assert rtb._evaluate_condition("${steps.0.value}>5", results, {}) is True
    assert rtb._evaluate_condition("${vars.i}<2", [], {"i": 1}) is True


def test_condition_undefined_variable_raises():
    with pytest.raises(ValueError, match="Undefined variable"):
        rtb._evaluate_condition("ghost", [], {})


def test_condition_non_boolean_raises():
    with pytest.raises(ValueError, match="Unsupported condition"):
        rtb._evaluate_condition("abc", [], {"abc": "text"})


# ---------------------------------------------------------------------------
# _evaluate_set_var_expr / _evaluate_arithmetic_expr
# ---------------------------------------------------------------------------


def test_set_var_plain_assignment():
    assert rtb._evaluate_set_var_expr("i=0", [], {}) == ("i", 0)


def test_set_var_arithmetic_on_vars():
    assert rtb._evaluate_set_var_expr("i=i+1", [], {"i": 4}) == ("i", 5)


def test_set_var_parenthesised_arithmetic():
    out = rtb._evaluate_set_var_expr("i=(i+1)*2", [], {"i": 3})
    assert out == ("i", 8)


def test_set_var_from_step_ref_keeps_value():
    results = [{"step": 0, "value": 9}]
    assert rtb._evaluate_set_var_expr("n=${steps.0.value}", results, {}) == (
        "n",
        9,
    )


def test_set_var_arithmetic_over_placeholder():
    out = rtb._evaluate_set_var_expr("i=${vars.i}+1", [], {"i": 2})
    assert out == ("i", 3)


# ---------------------------------------------------------------------------
# set_var's boundary: numeric / scalar only, by design.
#
# The tool docstring states the RHS is "intended for scalar values and
# restricted arithmetic expressions only" and "is not a general string-
# templating assignment language" (run_tool_batch.py:1132-1139). These
# tests pin exactly where that boundary falls, because the batch editor
# needs to present `expr` as a numeric-expression field rather than a
# free-text one — offering a text box invites input the evaluator is not
# meant to accept.
# ---------------------------------------------------------------------------


def test_set_var_bare_word_is_a_variable_reference_not_a_string():
    """`s=hello` reads the variable `hello`; it is not a string literal."""
    with pytest.raises(ValueError, match="Undefined variable: hello"):
        rtb._evaluate_set_var_expr("s=hello", [], {})


def test_set_var_bare_word_resolves_when_the_variable_exists():
    out = rtb._evaluate_set_var_expr("s=other", [], {"other": 3})
    assert out == ("s", 3)


def test_set_var_passes_through_text_it_cannot_evaluate():
    """No arithmetic trigger and not an identifier → kept verbatim."""
    assert rtb._evaluate_set_var_expr("s=hello world", [], {}) == (
        "s",
        "hello world",
    )


def test_set_var_keeps_the_quotes_when_quoted():
    """Quoting does not strip — the value literally contains them."""
    assert rtb._evaluate_set_var_expr('s="hello"', [], {}) == ("s", '"hello"')


@pytest.mark.parametrize("rhs", ["/tmp/x", "a/b", "hello-world", "a+b"])
def test_set_var_rejects_non_numeric_arithmetic(rhs: str):
    """Anything with `+ - * / % ()` is evaluated as arithmetic.

    Consequence worth knowing when authoring a script: a filesystem path
    or a hyphenated word is not a valid RHS. Carry such values in
    ``${args.*}`` (resolved at load time) instead of ``set_var``.
    """
    with pytest.raises(ValueError, match="valid numeric expression"):
        rtb._evaluate_set_var_expr(f"s={rhs}", [], {})


def test_set_var_rejects_non_assignment():
    with pytest.raises(ValueError, match="simple assignment"):
        rtb._evaluate_set_var_expr("i", [], {})


def test_set_var_division_by_zero_is_a_value_error():
    with pytest.raises(ValueError, match="division by zero"):
        rtb._evaluate_set_var_expr("i=1/0", [], {})


def test_arithmetic_rejects_booleans():
    with pytest.raises(ValueError, match="numeric"):
        rtb._evaluate_arithmetic_expr("a+1", {"a": True})


def test_arithmetic_rejects_unknown_variable():
    with pytest.raises(ValueError, match="valid numeric expression"):
        rtb._evaluate_arithmetic_expr("ghost+1", {})


def test_arithmetic_is_not_general_templating():
    """set_var is restricted arithmetic — the editor must not imply more."""
    with pytest.raises(ValueError):
        rtb._evaluate_arithmetic_expr("'a'+'b'", {})


# ---------------------------------------------------------------------------
# _build_label_map — reused by the pool validator
# ---------------------------------------------------------------------------


def test_label_map_collects_positions():
    actions = [
        {"tool_name": "noop"},
        {"tool_name": "label", "arguments": {"name": "top"}},
        {"tool_name": "label", "arguments": {"name": "end"}},
    ]
    assert rtb._build_label_map(actions) == {"top": 1, "end": 2}


def test_label_map_rejects_duplicates():
    actions = [
        {"tool_name": "label", "arguments": {"name": "x"}},
        {"tool_name": "label", "arguments": {"name": "x"}},
    ]
    with pytest.raises(ValueError, match="Duplicate label"):
        rtb._build_label_map(actions)


def test_label_map_requires_a_name():
    with pytest.raises(ValueError, match="requires arguments.name"):
        rtb._build_label_map([{"tool_name": "label", "arguments": {}}])


def test_label_map_accepts_tool_alias():
    assert rtb._build_label_map(
        [{"tool": "label", "arguments": {"name": "a"}}],
    ) == {"a": 0}


# ---------------------------------------------------------------------------
# Validation of the top-level inputs
# ---------------------------------------------------------------------------


def test_validate_actions_rejects_empty():
    with pytest.raises(ValueError, match="non-empty list"):
        rtb._validate_batch_actions([])


def test_validate_actions_rejects_non_list():
    with pytest.raises(ValueError, match="non-empty list"):
        rtb._validate_batch_actions({"a": 1})


def test_validate_actions_enforces_step_cap():
    too_many = [ACTION] * (rtb.MAX_BATCH_STEPS + 1)
    with pytest.raises(ValueError, match="Too many steps"):
        rtb._validate_batch_actions(too_many)


def test_validate_actions_accepts_exactly_the_cap():
    assert (
        len(rtb._validate_batch_actions([ACTION] * rtb.MAX_BATCH_STEPS))
        == rtb.MAX_BATCH_STEPS
    )


@pytest.mark.parametrize("bad", [0, -1, "x", None])
def test_validate_maxstep_rejects_non_positive(bad: Any):
    with pytest.raises(ValueError, match="positive integer"):
        rtb._validate_maxstep(bad)


def test_validate_maxstep_coerces_numeric_string():
    assert rtb._validate_maxstep("5") == 5


def test_step_cap_constants_are_what_callers_assume():
    """The pool validator and the editor hard-code these numbers."""
    assert rtb.MAX_BATCH_STEPS == 50
    assert rtb.DEFAULT_MAX_EXECUTION_STEPS == 500


# ---------------------------------------------------------------------------
# _extract_text — preprocess reads the summary block through this
# ---------------------------------------------------------------------------


def test_extract_text_finds_first_text_block():
    chunk = rtb._json_tool_response({"ok": True, "n": 1})
    assert json.loads(rtb._extract_text(chunk))["n"] == 1


def test_extract_text_on_error_payload():
    chunk = rtb._json_tool_response({"ok": False, "error": "boom"})
    assert json.loads(rtb._extract_text(chunk))["error"] == "boom"


# ---------------------------------------------------------------------------
# The step loop, driven through a stubbed _call_tool.
#
# `_call_tool` is the only thing in the module that needs a live Toolkit
# (run_tool_batch.py:599-612), so replacing it makes the whole loop —
# control flow, per-step overrides, payload assembly — testable without an
# agent. The cron preprocess feature depends on all of it.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch):
    """Stub _call_tool; record calls and let a test script failures."""
    calls: list[tuple[str, dict[str, Any]]] = []
    fail_on: set[str] = set()

    async def _stub(tool_name: str, arguments: dict[str, Any]):
        calls.append((tool_name, dict(arguments)))
        if tool_name in fail_on:
            return rtb._json_tool_response(
                {"ok": False, "error": f"{tool_name} exploded"},
            )
        return rtb._json_tool_response({"ok": True, "echo": arguments})

    monkeypatch.setattr(rtb, "_call_tool", _stub)
    return calls, fail_on


async def run_batch(actions: list[dict[str, Any]], **kwargs: Any):
    chunk = await rtb.run_tool_batch(actions=actions, **kwargs)
    return json.loads(rtb._extract_text(chunk))


@pytest.mark.asyncio
async def test_runs_every_step_in_order(fake_tools):
    calls, _ = fake_tools
    payload = await run_batch(
        [
            {"tool_name": "a", "arguments": {}},
            {"tool_name": "b", "arguments": {}},
        ],
    )
    assert [name for name, _ in calls] == ["a", "b"]
    assert payload["ok"] is True
    assert payload["total"] == 2
    assert payload["completed"] == 2


@pytest.mark.asyncio
async def test_last_only_payload_shape(fake_tools):
    payload = await run_batch(
        [
            {"tool_name": "a", "arguments": {}},
            {"tool_name": "b", "arguments": {}},
        ],
        last_only=True,
    )
    assert "last_step_result" in payload
    assert "results" not in payload
    assert payload["last_step_result"]["step"] == 1


@pytest.mark.asyncio
async def test_full_payload_shape(fake_tools):
    payload = await run_batch([{"tool_name": "a", "arguments": {}}])
    assert "results" in payload
    assert "last_step_result" not in payload


@pytest.mark.asyncio
async def test_stop_on_error_halts_by_default(fake_tools):
    calls, fail_on = fake_tools
    fail_on.add("boom")
    payload = await run_batch(
        [
            {"tool_name": "boom", "arguments": {}},
            {"tool_name": "after", "arguments": {}},
        ],
    )
    assert [name for name, _ in calls] == ["boom"]
    assert payload["ok"] is False
    assert "exploded" in payload["error"]


@pytest.mark.asyncio
async def test_per_step_stop_on_error_false_continues(fake_tools):
    calls, fail_on = fake_tools
    fail_on.add("boom")
    await run_batch(
        [
            {"tool_name": "boom", "arguments": {}, "stop_on_error": False},
            {"tool_name": "after", "arguments": {}},
        ],
    )
    assert [name for name, _ in calls] == ["boom", "after"]


@pytest.mark.asyncio
async def test_batch_level_stop_on_error_false_continues(fake_tools):
    calls, fail_on = fake_tools
    fail_on.add("boom")
    await run_batch(
        [
            {"tool_name": "boom", "arguments": {}},
            {"tool_name": "after", "arguments": {}},
        ],
        stop_on_error=False,
    )
    assert [name for name, _ in calls] == ["boom", "after"]


@pytest.mark.asyncio
async def test_per_step_override_beats_batch_level(fake_tools):
    """A step saying True must stop even when the batch says False."""
    calls, fail_on = fake_tools
    fail_on.add("boom")
    await run_batch(
        [
            {"tool_name": "boom", "arguments": {}, "stop_on_error": True},
            {"tool_name": "after", "arguments": {}},
        ],
        stop_on_error=False,
    )
    assert [name for name, _ in calls] == ["boom"]


@pytest.mark.asyncio
async def test_args_are_resolved_for_file_backed_batches(
    tmp_path: Path,
    fake_tools,
):
    calls, _ = fake_tools
    path = write_json(
        tmp_path / "b.json",
        [
            {
                "tool_name": "a",
                "arguments": {"n": "${args.n}", "s": "x=${args.n}"},
            }
        ],
    )
    await rtb.run_tool_batch(file_path=str(path), args={"n": 5})
    assert calls[0][1] == {"n": 5, "s": "x=5"}


@pytest.mark.asyncio
async def test_args_are_NOT_resolved_for_inline_actions(fake_tools):
    """Latent bug, pinned so a caller cannot be surprised by it.

    `_resolve_args` is applied only inside `_load_actions_from_file`
    (run_tool_batch.py:1059-1067). When `actions` is passed inline the
    coerced `args` are computed and then never used, so `${args.x}` reaches
    the tool verbatim. The docstring for `args` does not restrict it to
    file-backed batches, so this is a discrepancy rather than a documented
    limitation — any caller passing inline actions must resolve args itself.
    """
    calls, _ = fake_tools
    await run_batch(
        [{"tool_name": "a", "arguments": {"n": "${args.n}"}}],
        args={"n": 5},
    )
    assert calls[0][1] == {"n": "${args.n}"}


@pytest.mark.asyncio
async def test_step_ref_flows_between_steps(fake_tools):
    calls, _ = fake_tools
    await run_batch(
        [
            {"tool_name": "a", "arguments": {}},
            {"tool_name": "b", "arguments": {"prev": "${steps.0.ok}"}},
        ],
    )
    assert calls[1][1]["prev"] is True


@pytest.mark.asyncio
async def test_loop_terminates_and_maxstep_caps_executions(fake_tools):
    """set_var + goto + label loop, bounded by maxstep."""
    calls, _ = fake_tools
    await run_batch(
        [
            {"tool_name": "set_var", "arguments": {"expr": "i=0"}},
            {"tool_name": "label", "arguments": {"name": "top"}},
            {"tool_name": "work", "arguments": {}},
            {"tool_name": "set_var", "arguments": {"expr": "i=i+1"}},
            {
                "tool_name": "goto",
                "arguments": {"label": "top", "condition": "${vars.i}<3"},
            },
        ],
    )
    # Control-flow pseudo-tools never reach _call_tool.
    assert [name for name, _ in calls] == ["work"] * 3


@pytest.mark.asyncio
async def test_maxstep_stops_a_runaway_loop(fake_tools):
    calls, _ = fake_tools
    payload = await run_batch(
        [
            {"tool_name": "label", "arguments": {"name": "top"}},
            {"tool_name": "work", "arguments": {}},
            {"tool_name": "goto", "arguments": {"label": "top"}},
        ],
        maxstep=7,
    )
    assert len(calls) < 7
    assert payload["ok"] is False


@pytest.mark.asyncio
async def test_unknown_goto_label_is_an_error(fake_tools):
    payload = await run_batch(
        [{"tool_name": "goto", "arguments": {"label": "nowhere"}}],
    )
    assert payload["ok"] is False
    assert "Unknown label" in json.dumps(payload)


@pytest.mark.asyncio
async def test_nested_run_tool_batch_is_refused(fake_tools):
    payload = await run_batch(
        [{"tool_name": "run_tool_batch", "arguments": {}}],
    )
    assert payload["ok"] is False
    assert "Recursive" in json.dumps(payload)


@pytest.mark.asyncio
async def test_file_path_source_is_loaded(tmp_path: Path, fake_tools):
    calls, _ = fake_tools
    path = write_json(tmp_path / "b.json", {"actions": [{"tool_name": "a"}]})
    chunk = await rtb.run_tool_batch(file_path=str(path))
    assert json.loads(rtb._extract_text(chunk))["ok"] is True
    assert [name for name, _ in calls] == ["a"]


@pytest.mark.asyncio
async def test_missing_arg_fails_the_load_for_file_backed_batches(
    tmp_path: Path,
):
    """Resolution happens at load time, so this raises rather than
    producing a per-step error."""
    path = write_json(
        tmp_path / "b.json",
        [{"tool_name": "a", "arguments": {"x": "${args.absent}"}}],
    )
    chunk = await rtb.run_tool_batch(file_path=str(path), args={"other": 1})
    payload = json.loads(rtb._extract_text(chunk))
    assert payload["ok"] is False
    assert "Missing arg" in json.dumps(payload)
