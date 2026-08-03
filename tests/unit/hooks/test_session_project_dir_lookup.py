# -*- coding: utf-8 -*-
"""Reading the persisted per-chat project override on every turn.

Regression cover for a bug where the override applied only to the turn that
set it: the lookup called ``get_chat_id_by_session()`` with the wrong
arity, the resulting ``TypeError`` was swallowed by a broad ``except``, and
every later turn silently fell back to the agent default.
"""
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace

import pytest

from qwenpaw.hooks.request_setup.contextvars_hook import _session_project_dir


class _ChatManager:
    """Mirrors the real signature, including the required `channel` arg."""

    def __init__(self, chat=None) -> None:
        self._chat = chat
        self.lookup_calls: list[tuple] = []

    async def get_chat_id_by_session(
        self,
        session_id: str,
        channel: str,
        user_id: str | None = None,
    ) -> str | None:
        self.lookup_calls.append((session_id, channel, user_id))
        return self._chat.id if self._chat else None

    async def get_chat(self, chat_id: str):
        if self._chat and self._chat.id == chat_id:
            return self._chat
        return None


def _ctx(chat_manager, *, session_id="console:u1", channel="console"):
    return SimpleNamespace(
        session_id=session_id,
        workspace=SimpleNamespace(chat_manager=chat_manager),
        request=SimpleNamespace(channel=channel, user_id="u1"),
    )


def _chat(project_dir: str | None):
    meta = {}
    if project_dir:
        meta["runtime_context"] = {"project_dir": project_dir}
    return SimpleNamespace(id="chat-1", meta=meta)


@pytest.mark.asyncio
async def test_reads_the_persisted_override():
    """The whole point: this must work on turn 2, 3, 4 … not just turn 1."""
    manager = _ChatManager(_chat("/repos/chosen"))

    result = await _session_project_dir(_ctx(manager))

    assert result == "/repos/chosen"


@pytest.mark.asyncio
async def test_passes_channel_to_the_lookup():
    """`channel` is a required positional arg; omitting it raised TypeError."""
    manager = _ChatManager(_chat("/repos/chosen"))

    await _session_project_dir(_ctx(manager, channel="console"))

    assert manager.lookup_calls == [("console:u1", "console", "u1")]


@pytest.mark.asyncio
async def test_returns_none_when_chat_has_no_override():
    manager = _ChatManager(_chat(None))
    assert await _session_project_dir(_ctx(manager)) is None


@pytest.mark.asyncio
async def test_returns_none_when_no_chat_matches():
    manager = _ChatManager(None)
    assert await _session_project_dir(_ctx(manager)) is None


@pytest.mark.asyncio
async def test_returns_none_without_a_session_id():
    manager = _ChatManager(_chat("/repos/chosen"))
    assert await _session_project_dir(_ctx(manager, session_id="")) is None


@pytest.mark.asyncio
async def test_returns_none_without_a_chat_manager():
    ctx = SimpleNamespace(
        session_id="console:u1",
        workspace=SimpleNamespace(chat_manager=None),
        request=SimpleNamespace(channel="console", user_id="u1"),
    )
    assert await _session_project_dir(ctx) is None


@pytest.mark.asyncio
async def test_lookup_failure_is_logged_loudly(caplog):
    """A broken lookup silently degrades behaviour, so it must be visible.

    The original bug hid behind a debug-level log for weeks of turns.
    """

    class _Broken(_ChatManager):
        async def get_chat_id_by_session(self, *args, **kwargs):
            raise RuntimeError("repo unavailable")

    with caplog.at_level("WARNING"):
        result = await _session_project_dir(_ctx(_Broken()))

    assert result is None
    assert any(
        record.levelname == "WARNING" for record in caplog.records
    ), "a failed session-override lookup must warn, not whisper at debug"


@pytest.mark.asyncio
async def test_missing_channel_falls_back_to_default_channel():
    """Cron/heartbeat turns may not carry a channel on the request."""
    manager = _ChatManager(_chat("/repos/chosen"))
    ctx = SimpleNamespace(
        session_id="console:u1",
        workspace=SimpleNamespace(chat_manager=manager),
        request=SimpleNamespace(channel=None, user_id=None),
    )

    result = await _session_project_dir(ctx)

    assert result == "/repos/chosen"
    assert manager.lookup_calls[0][1], "a channel must still be supplied"
