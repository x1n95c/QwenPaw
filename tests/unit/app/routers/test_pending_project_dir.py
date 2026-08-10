# -*- coding: utf-8 -*-
"""Binding pending project dirs to a chat that does not exist yet.

The console can only offer a directory picker *before* a chat has an id, so
the choice arrives with the first message as
``request_context.pending_project_dirs`` (ordered list, primary first;
the legacy singular ``pending_project_dir`` is still honoured). It is
validated and persisted right after the chat is created — before the turn
runs — so the first turn already works in the chosen directories.
"""
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.console import (
    _persist_pending_project_dirs,
    _read_request_context,
)


class _FakeChatManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list | None]] = []
        # Recorded separately so the existing (chat_id, dirs) assertions
        # stay readable while still covering the name.
        self.names: list[str | None] = []

    async def set_session_project_dirs(
        self,
        chat_id: str,
        project_dirs: list | None,
        project_name: str | None = None,
    ):
        self.calls.append((chat_id, project_dirs))
        self.names.append(project_name)
        return SimpleNamespace(id=chat_id)


class _FailingChatManager(_FakeChatManager):
    async def set_session_project_dirs(
        self,
        chat_id,
        project_dirs,
        project_name=None,
    ):
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


class TestPersistPendingProjectDirs:
    @pytest.mark.asyncio
    async def test_persists_a_valid_list(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dirs": [
                        {"path": str(tmp_path), "label": "main"},
                        {"path": str(other)},
                    ],
                },
            },
        )

        assert ws.chat_manager.calls == [
            (
                "chat-1",
                [
                    {"path": str(tmp_path), "label": "main"},
                    {"path": str(other), "label": None},
                ],
            ),
        ]

    @pytest.mark.asyncio
    async def test_legacy_singular_key_still_works(self, tmp_path):
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {"request_context": {"pending_project_dir": str(tmp_path)}},
        )

        assert ws.chat_manager.calls == [
            ("chat-1", [{"path": str(tmp_path), "label": None}]),
        ]

    @pytest.mark.asyncio
    async def test_rejects_entries_that_are_not_directories(self, tmp_path):
        """Client-supplied, so they must be checked.

        Writing an unusable path onto the chat would silently steer every
        later turn in that conversation.
        """
        target = tmp_path / "a-file.txt"
        target.write_text("x", encoding="utf-8")
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dirs": [str(target)],
                },
            },
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_valid_entries_survive_a_bad_sibling(self, tmp_path):
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dirs": [
                        str(tmp_path),
                        str(tmp_path / "nope"),
                    ],
                },
            },
        )

        assert ws.chat_manager.calls == [
            ("chat-1", [{"path": str(tmp_path), "label": None}]),
        ]

    @pytest.mark.asyncio
    async def test_does_not_clobber_an_existing_session_override(
        self,
        tmp_path,
    ):
        """A chat that already has an override is not a new chat."""
        ws = _workspace()
        chat = _chat(
            {
                "runtime_context": {
                    "project_dirs": [{"path": "/repos/chosen"}],
                },
            },
        )

        await _persist_pending_project_dirs(
            ws,
            chat,
            {"request_context": {"pending_project_dirs": [str(tmp_path)]}},
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "pending",
        ["", "   ", None, 123, [], {}],
    )
    async def test_ignores_blank_or_malformed_values(self, pending):
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {"request_context": {"pending_project_dirs": pending}},
        )

        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_no_request_context_is_a_noop(self):
        ws = _workspace()
        await _persist_pending_project_dirs(ws, _chat(), {})
        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_missing_chat_is_a_noop(self, tmp_path):
        ws = _workspace()
        await _persist_pending_project_dirs(
            ws,
            None,
            {"request_context": {"pending_project_dirs": [str(tmp_path)]}},
        )
        assert ws.chat_manager.calls == []

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_raise(self, tmp_path):
        """The user's message must still be delivered.

        A failure here degrades to the agent default rather than dropping
        the turn the user just submitted.
        """
        ws = SimpleNamespace(chat_manager=_FailingChatManager())

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {"request_context": {"pending_project_dirs": [str(tmp_path)]}},
        )

    @pytest.mark.asyncio
    async def test_carries_a_pending_project_name(self, tmp_path):
        """A name typed before the first message must not be lost."""
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dirs": [{"path": str(tmp_path)}],
                    "pending_project_name": "  My App  ",
                },
            },
        )

        assert ws.chat_manager.names == ["My App"]

    @pytest.mark.asyncio
    async def test_missing_pending_name_stays_none(self, tmp_path):
        # None keeps the name derived from the primary directory rather
        # than pinning one the user never typed.
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dirs": [{"path": str(tmp_path)}],
                },
            },
        )

        assert ws.chat_manager.names == [None]

    @pytest.mark.asyncio
    async def test_normalizes_before_persisting(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        ws = _workspace()

        await _persist_pending_project_dirs(
            ws,
            _chat(),
            {
                "request_context": {
                    "pending_project_dirs": [f"{tmp_path}/a/../a/b"],
                },
            },
        )

        assert ws.chat_manager.calls == [
            ("chat-1", [{"path": str(nested), "label": None}]),
        ]


class TestPendingDirsForTheTurn:
    """The hook must agree with the router about what is acceptable."""

    def test_valid_pending_list_is_used_when_session_unset(self):
        from qwenpaw.hooks.request_setup.contextvars_hook import (
            _pending_project_dirs,
        )

        result = _pending_project_dirs(
            {
                "pending_project_dirs": [
                    {"path": "/tmp", "label": "main"},
                ],
            },
        )
        assert result == [{"path": "/tmp", "label": "main"}]

    def test_invalid_entries_are_dropped(self, tmp_path):
        """Otherwise the turn would run where the chat was never bound."""
        from qwenpaw.hooks.request_setup.contextvars_hook import (
            _pending_project_dirs,
        )

        assert (
            _pending_project_dirs(
                {"pending_project_dirs": [str(tmp_path / "nope")]},
            )
            is None
        )

    def test_legacy_singular_key_is_honoured(self, tmp_path):
        from qwenpaw.hooks.request_setup.contextvars_hook import (
            _pending_project_dirs,
        )

        result = _pending_project_dirs(
            {"pending_project_dir": str(tmp_path)},
        )
        assert result == [{"path": str(tmp_path), "label": None}]
