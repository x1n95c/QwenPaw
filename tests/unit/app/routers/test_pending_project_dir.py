# -*- coding: utf-8 -*-
"""Binding a project dir to a chat that does not exist yet.

The console can only offer a directory picker *before* a chat has an id, so
the choice arrives with the first message as
``request_context.pending_project_dir``. It is validated and persisted right
after the chat is created — before the turn runs — so the first turn already
works in the chosen directory.
"""
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.console import (
    _persist_pending_project_dir,
    _read_request_context,
)


class _FakeChatManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def set_session_project_dir(
        self,
        chat_id: str,
        project_dir: str | None,
    ):
        self.calls.append((chat_id, project_dir))
        return SimpleNamespace(id=chat_id)


class _FailingChatManager(_FakeChatManager):
    async def set_session_project_dir(self, chat_id, project_dir):
        raise RuntimeError("disk on fire")


def _workspace() -> SimpleNamespace:
    return SimpleNamespace(chat_manager=_FakeChatManager())


def _chat(meta: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id="chat-1", meta=meta or {})


class TestReadRequestContext:
    def test_reads_from_dict_payload(self):
        assert _read_request_context({"request_context": {"a": 1}}) == {"a": 1}

    def test_reads_from_model_payload(self):
        payload = SimpleNamespace(request_context={"a": 1})
        assert _read_request_context(payload) == {"a": 1}

    @pytest.mark.parametrize(
        "payload",
        [{}, {"request_context": None}, {"request_context": "nope"}, object()],
    )
    def test_missing_or_malformed_yields_empty(self, payload):
        assert _read_request_context(payload) == {}


class TestPersistPendingProjectDir:
    @pytest.mark.asyncio
    async def test_persists_a_valid_directory(self, tmp_path):
        ws = _workspace()

        await _persist_pending_project_dir(
            ws,
            _chat(),
            {"request_context": {"pending_project_dir": str(tmp_path)}},
        )

        assert ws.chat_manager.calls == [("chat-1", str(tmp_path))]

    @pytest.mark.asyncio
    async def test_rejects_a_path_that_is_not_a_directory(self, tmp_path):
        """Client-supplied, so it must be checked.

        Writing an unusable path onto the chat would silently steer every
        later turn in that conversation.
        """
        target = tmp_path / "a-file.txt"
        target.write_text("x", encoding="utf-8")
        ws = _workspace()

        await _persist_pending_project_dir(
            ws,
            _chat(),
            {"request_context": {"pending_project_dir": str(target)}},
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_rejects_a_missing_directory(self, tmp_path):
        ws = _workspace()

        await _persist_pending_project_dir(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dir": str(tmp_path / "nope"),
                },
            },
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_does_not_clobber_an_existing_session_override(
        self,
        tmp_path,
    ):
        """A chat that already has an override is not a new chat."""
        ws = _workspace()
        chat = _chat(
            {"runtime_context": {"project_dir": "/repos/already-chosen"}},
        )

        await _persist_pending_project_dir(
            ws,
            chat,
            {"request_context": {"pending_project_dir": str(tmp_path)}},
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pending", ["", "   ", None, 123])
    async def test_ignores_blank_or_non_string_values(self, pending):
        ws = _workspace()

        await _persist_pending_project_dir(
            ws,
            _chat(),
            {"request_context": {"pending_project_dir": pending}},
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_no_request_context_is_a_noop(self):
        ws = _workspace()
        await _persist_pending_project_dir(ws, _chat(), {})
        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_missing_chat_is_a_noop(self, tmp_path):
        ws = _workspace()
        await _persist_pending_project_dir(
            ws,
            None,
            {"request_context": {"pending_project_dir": str(tmp_path)}},
        )
        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_raise(self, tmp_path):
        """The user's message must still be delivered.

        A failure here degrades to the agent default rather than dropping
        the turn the user just submitted.
        """
        ws = SimpleNamespace(chat_manager=_FailingChatManager())

        await _persist_pending_project_dir(
            ws,
            _chat(),
            {"request_context": {"pending_project_dir": str(tmp_path)}},
        )

    @pytest.mark.asyncio
    async def test_normalizes_before_persisting(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        ws = _workspace()

        await _persist_pending_project_dir(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dir": f"{tmp_path}/a/../a/b",
                },
            },
        )

        assert ws.chat_manager.calls == [("chat-1", str(nested))]


class TestTrustedRequestOverride:
    """The hook must agree with the router about what is acceptable."""

    def test_valid_pending_dir_is_used_for_the_turn(self, tmp_path):
        from qwenpaw.hooks.request_setup.contextvars_hook import (
            _trusted_request_project_dir,
        )

        result = _trusted_request_project_dir(
            {"pending_project_dir": str(tmp_path)},
        )
        assert result == str(tmp_path)

    def test_invalid_pending_dir_is_ignored(self, tmp_path):
        """Otherwise the turn would run where the chat was never bound."""
        from qwenpaw.hooks.request_setup.contextvars_hook import (
            _trusted_request_project_dir,
        )

        assert (
            _trusted_request_project_dir(
                {"pending_project_dir": str(tmp_path / "nope")},
            )
            is None
        )

    def test_acp_key_takes_precedence(self, tmp_path):
        from qwenpaw.agents.acp.meta import ACP_CODING_PROJECT_META_KEY
        from qwenpaw.hooks.request_setup.contextvars_hook import (
            _trusted_request_project_dir,
        )

        result = _trusted_request_project_dir(
            {
                ACP_CODING_PROJECT_META_KEY: "/from/acp",
                "pending_project_dir": str(tmp_path),
            },
        )
        assert result == "/from/acp"
