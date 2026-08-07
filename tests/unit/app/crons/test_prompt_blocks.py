# -*- coding: utf-8 -*-
"""Putting leading context into a cron job's agent request.

Asserted with plain sentinels rather than a real preprocess or skill block:
this shape handling is shared by both, and the point of the module is that
it knows nothing about what it is prepending.

The message shapes covered here are the ones ``CronJobRequest.input``
actually allows — it is ``Optional[Any]``, so a bare string, a message
list, and nothing at all all reach production.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from qwenpaw.app.crons.prompt_blocks import prepend_text_blocks

MARK = "<<injected>>"


def last_user_text(messages: list[dict]) -> str:
    user = [m for m in messages if m.get("role") == "user"][-1]
    return "".join(
        block["text"] for block in user["content"] if block.get("text")
    )


class TestOrdering:
    def test_blocks_land_in_the_order_given(self) -> None:
        # The whole reason the executor makes ONE call with an ordered
        # list: each prepend goes in front of whatever is already there,
        # so two calls would silently invert them.
        out = prepend_text_blocks("task", ["FIRST", "SECOND"])

        texts = [block["text"] for block in out[0]["content"]]
        assert texts == ["FIRST", "SECOND", "task"]

    def test_the_task_body_stays_last(self) -> None:
        out = prepend_text_blocks(
            [{"role": "user", "content": [{"type": "text", "text": "ask"}]}],
            ["A", "B"],
        )

        assert [b["text"] for b in out[0]["content"]] == ["A", "B", "ask"]


class TestNothingToPrepend:
    @pytest.mark.parametrize("texts", [[], [""], ["   "], ["", "\n\t"]])
    def test_returns_the_input_identically(self, texts: list[str]) -> None:
        # Identity, not an equal copy: this is what keeps an ordinary agent
        # job's raw string a raw string all the way to `stream_query`, and
        # it is an invariant of this function rather than of the caller's
        # guard.
        request_input = "ping"

        assert prepend_text_blocks(request_input, texts) is request_input

    def test_a_message_list_is_returned_untouched(self) -> None:
        messages = [{"role": "user", "content": "ask"}]

        assert prepend_text_blocks(messages, []) is messages

    def test_blank_entries_are_dropped_but_others_survive(self) -> None:
        out = prepend_text_blocks("task", ["", "REAL", "  "])

        assert [b["text"] for b in out[0]["content"]] == ["REAL", "task"]


class TestMessageShapes:
    def test_prepends_into_the_existing_last_user_message(self) -> None:
        """A second consecutive user turn is rejected by some formatters."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "ask"}]},
        ]
        out = prepend_text_blocks(messages, [MARK])

        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert len(out[0]["content"]) == 2
        assert "ask" in last_user_text(out)
        assert MARK in last_user_text(out)

    def test_does_not_mutate_the_input_list(self) -> None:
        # Callers hand us `job.request`'s dump and keep using it. Every
        # branch builds a new list for this reason — do not "simplify" to
        # `content.insert(0, ...)`.
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "ask"}]},
        ]
        prepend_text_blocks(messages, [MARK])

        assert len(messages[0]["content"]) == 1

    def test_wraps_a_bare_string_input(self) -> None:
        """The console's drawer can produce this via JSON.parse('"hi"')."""
        out = prepend_text_blocks("hi", [MARK])

        assert out[0]["role"] == "user"
        assert "hi" in last_user_text(out)
        assert MARK in last_user_text(out)

    @pytest.mark.parametrize("empty", [None, [], ""])
    def test_synthesizes_a_message_when_input_is_empty(
        self,
        empty: Any,
    ) -> None:
        out = prepend_text_blocks(empty, [MARK])

        assert out[0]["role"] == "user"
        assert MARK in last_user_text(out)

    def test_targets_the_last_user_turn_not_the_first(self) -> None:
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "first"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "reply"}],
            },
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
        ]
        out = prepend_text_blocks(messages, [MARK])

        assert MARK not in json.dumps(out[0])
        assert MARK in json.dumps(out[2])

    def test_adds_a_turn_when_there_is_no_user_message(self) -> None:
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]
        out = prepend_text_blocks(messages, [MARK])

        assert out[-1]["role"] == "user"
        assert MARK in last_user_text(out)

    def test_handles_string_content_on_a_user_message(self) -> None:
        messages = [{"role": "user", "content": "plain string"}]
        out = prepend_text_blocks(messages, [MARK])

        assert "plain string" in last_user_text(out)

    def test_handles_unexpected_content_without_losing_the_blocks(
        self,
    ) -> None:
        # `input` is `Optional[Any]`; a user turn whose content is neither a
        # list nor a string must still receive the context rather than
        # silently drop it.
        messages = [{"role": "user", "content": None}]
        out = prepend_text_blocks(messages, [MARK])

        assert MARK in last_user_text(out)
