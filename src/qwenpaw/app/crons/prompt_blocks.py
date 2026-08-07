# -*- coding: utf-8 -*-
"""Putting leading context into a cron job's agent request.

A scheduled fire can carry two kinds of context ahead of the task itself —
the skills it was attached to, and the results of its preprocess chain —
and their order is part of the contract. So the ordering lives at the one
call site that knows both, and this module only takes an already-ordered
list and gets it into the message shape.

A leaf module on purpose: imported on the request path, so it depends on
nothing but ``typing``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def prepend_text_blocks(request_input: Any, texts: Sequence[str]) -> Any:
    """Put ``texts`` before the task body, in the order given.

    Before, not after: this is context the task is written against — "报告
    下面这份天气" only reads correctly if the data precedes the instruction.
    Trailing context also competes with the instruction for the model's
    attention, and the instruction is what should be last.

    Prepended into the existing last user turn rather than pushed as its own
    message: two consecutive user turns are something several formatters
    normalise or reject outright.

    Blank entries are dropped here rather than at the call site, so a job
    with nothing to prepend gets its ``input`` back **identically** — that
    is what keeps an ordinary agent job's raw string a raw string all the
    way to ``stream_query``.

    Tolerates the shapes ``CronJobRequest.input`` actually allows — it is
    ``Optional[Any]``, and the console's drawer will happily produce a bare
    string from ``JSON.parse('"hi"')``.

    Note for future edits: every branch builds a **new** list rather than
    mutating in place (``[*blocks, *content]``, not ``content.insert``), and
    the messages are shallow-copied first. Callers hand us ``job.request``'s
    dump and reuse it; ``test_prompt_blocks.py`` pins this.
    """
    blocks = [
        {"type": "text", "text": text}
        for text in texts
        if text and text.strip()
    ]
    if not blocks:
        return request_input

    if isinstance(request_input, str):
        return [
            {
                "role": "user",
                "content": [
                    *blocks,
                    {"type": "text", "text": request_input},
                ],
            },
        ]

    if not isinstance(request_input, list) or not request_input:
        return [{"role": "user", "content": list(blocks)}]

    messages = [dict(m) if isinstance(m, dict) else m for m in request_input]
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            message["content"] = [*blocks, *content]
        elif isinstance(content, str):
            message["content"] = [
                *blocks,
                {"type": "text", "text": content},
            ]
        else:
            message["content"] = list(blocks)
        return messages

    # No user turn to attach to; add one rather than losing the data.
    return [*messages, {"role": "user", "content": list(blocks)}]


__all__ = ["prepend_text_blocks"]
