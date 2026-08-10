# -*- coding: utf-8 -*-
"""A session project-dirs override must survive across turns.

Uses the real :class:`ChatManager` and the real request-setup hook helper,
because the bug this covers was an argument-arity mismatch between them:
unit tests with a hand-written mock manager had agreed with the caller and
missed it, while the override silently reverted to the agent default on
every turn after the one that set it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.app.channels.schema import DEFAULT_CHANNEL
from qwenpaw.app.chats.manager import ChatManager
from qwenpaw.app.chats.repo import JsonChatRepository
from qwenpaw.config.project_dir import (
    SOURCE_AGENT,
    SOURCE_SESSION,
    resolve_effective_project_dirs,
)
from qwenpaw.hooks.request_setup.contextvars_hook import _session_project_dirs

SESSION_ID = "console:user-1"


@pytest.fixture
def manager(tmp_path) -> ChatManager:
    return ChatManager(repo=JsonChatRepository(tmp_path / "chats.json"))


def _ctx(manager: ChatManager) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=SESSION_ID,
        workspace=SimpleNamespace(chat_manager=manager),
        request=SimpleNamespace(channel=DEFAULT_CHANNEL, user_id="user-1"),
    )


async def _seed_chat(manager: ChatManager):
    return await manager.get_or_create_chat(
        SESSION_ID,
        "user-1",
        DEFAULT_CHANNEL,
        name="Chat",
    )


def _entries(*dirs: Path) -> list[dict]:
    return [{"path": str(d), "label": None} for d in dirs]


@pytest.mark.asyncio
async def test_override_is_readable_by_the_request_hook(manager, tmp_path):
    chat = await _seed_chat(manager)
    project = tmp_path / "repo"
    project.mkdir()

    await manager.set_session_project_dirs(chat.id, _entries(project))

    assert await _session_project_dirs(_ctx(manager)) == _entries(project)


@pytest.mark.asyncio
async def test_override_survives_repeated_turns(manager, tmp_path):
    """Simulates turn 1 → turn 2 → turn 3 with no further user action."""
    chat = await _seed_chat(manager)
    project = tmp_path / "repo"
    extra = tmp_path / "extra"
    project.mkdir()
    extra.mkdir()
    await manager.set_session_project_dirs(chat.id, _entries(project, extra))

    for turn in range(3):
        resolved = resolve_effective_project_dirs(
            workspace_dir=str(tmp_path / "workspace"),
            agent_project_dirs=_entries(tmp_path / "agent-default"),
            session_project_dirs=await _session_project_dirs(_ctx(manager)),
        )
        assert resolved.source == SOURCE_SESSION, f"reverted on turn {turn+1}"
        assert [str(p) for p in resolved.paths] == [
            str(project),
            str(extra),
        ]


@pytest.mark.asyncio
async def test_override_survives_a_touch(manager, tmp_path):
    """Each turn touches the chat; that must not drop meta."""
    chat = await _seed_chat(manager)
    project = tmp_path / "repo"
    project.mkdir()
    await manager.set_session_project_dirs(chat.id, _entries(project))

    await manager.touch_chat(chat.id)

    assert await _session_project_dirs(_ctx(manager)) == _entries(project)


@pytest.mark.asyncio
async def test_override_survives_a_rename(manager, tmp_path):
    """Async title generation renames the chat mid-conversation."""
    from qwenpaw.app.chats.models import ChatUpdate

    chat = await _seed_chat(manager)
    project = tmp_path / "repo"
    project.mkdir()
    await manager.set_session_project_dirs(chat.id, _entries(project))

    await manager.patch_chat(chat.id, ChatUpdate(name="Renamed by title gen"))

    assert await _session_project_dirs(_ctx(manager)) == _entries(project)


@pytest.mark.asyncio
async def test_override_survives_a_manager_restart(manager, tmp_path):
    """A server restart must not lose the binding."""
    chat = await _seed_chat(manager)
    project = tmp_path / "repo"
    project.mkdir()
    await manager.set_session_project_dirs(chat.id, _entries(project))

    reloaded = ChatManager(repo=JsonChatRepository(tmp_path / "chats.json"))

    assert await _session_project_dirs(_ctx(reloaded)) == _entries(project)


@pytest.mark.asyncio
async def test_clearing_returns_to_the_agent_default(manager, tmp_path):
    chat = await _seed_chat(manager)
    project = tmp_path / "repo"
    project.mkdir()
    agent_default = tmp_path / "agent-default"
    agent_default.mkdir()
    await manager.set_session_project_dirs(chat.id, _entries(project))

    await manager.set_session_project_dirs(chat.id, None)

    resolved = resolve_effective_project_dirs(
        workspace_dir=str(tmp_path / "workspace"),
        agent_project_dirs=_entries(agent_default),
        session_project_dirs=await _session_project_dirs(_ctx(manager)),
    )
    assert resolved.source == SOURCE_AGENT
    assert resolved.paths == [agent_default]


@pytest.mark.asyncio
async def test_two_chats_keep_separate_overrides(manager, tmp_path):
    """Same agent, two conversations, two project lists."""
    chat_a = await manager.get_or_create_chat(
        "console:user-1#a",
        "user-1",
        DEFAULT_CHANNEL,
    )
    chat_b = await manager.get_or_create_chat(
        "console:user-1#b",
        "user-1",
        DEFAULT_CHANNEL,
    )
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    await manager.set_session_project_dirs(chat_a.id, _entries(proj_a))
    await manager.set_session_project_dirs(chat_b.id, _entries(proj_b))

    def ctx_for(session_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            session_id=session_id,
            workspace=SimpleNamespace(chat_manager=manager),
            request=SimpleNamespace(channel=DEFAULT_CHANNEL, user_id="user-1"),
        )

    assert await _session_project_dirs(ctx_for("console:user-1#a")) == (
        _entries(proj_a)
    )
    assert await _session_project_dirs(ctx_for("console:user-1#b")) == (
        _entries(proj_b)
    )
