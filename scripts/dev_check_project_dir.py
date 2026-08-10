#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the workspace / project-dirs split against a live sandbox.

Run this after ``scripts/dev_sandbox_workspace.py`` and after exporting
``QWENPAW_WORKING_DIR``. It drives the real resolver, the real ContextVars
and the real tool entry points — no mocks — and reports pass/fail per
behaviour of the project-dirs model (ordered list, index 0 = primary).

    eval "$(python scripts/dev_sandbox_workspace.py --print-env-only)"
    python scripts/dev_check_project_dir.py

Exit code is non-zero if any check fails, so it is usable in CI.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

_FAILURES: list[str] = []
_CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    """Record one assertion and print it."""
    global _CHECKS  # noqa: PLW0603 - tiny script, a counter is fine
    _CHECKS += 1
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
        _FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n== {title} ==")


def _require_sandbox() -> Path:
    raw = os.environ.get("QWENPAW_WORKING_DIR")
    if not raw:
        print(
            "error: QWENPAW_WORKING_DIR is not set.\n"
            "       Run scripts/dev_sandbox_workspace.py first, then\n"
            '       eval "$(python scripts/dev_sandbox_workspace.py '
            '--print-env-only)"',
            file=sys.stderr,
        )
        raise SystemExit(2)
    working_dir = Path(raw).expanduser().resolve()
    if not (working_dir / "config.json").is_file():
        print(
            f"error: no config.json under {working_dir}; "
            "is this really a sandbox workspace?",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if working_dir == Path("~/.qwenpaw").expanduser().resolve():
        print(
            "refusing to run against your real ~/.qwenpaw workspace.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return working_dir


def test_resolver_precedence() -> None:
    section("Resolver precedence")
    from qwenpaw.config.project_dir import (
        SOURCE_AGENT,
        SOURCE_FORK,
        SOURCE_SESSION,
        SOURCE_WORKSPACE_FALLBACK,
        resolve_effective_project_dirs,
    )

    ws = "/tmp/ws"
    r = resolve_effective_project_dirs(workspace_dir=ws)
    check(
        "no config -> workspace fallback (empty list)",
        r.source == SOURCE_WORKSPACE_FALLBACK
        and r.dirs == ()
        and str(r.primary_path) == ws,
        f"got {r}",
    )

    r = resolve_effective_project_dirs(
        workspace_dir=ws,
        agent_project_dirs=[{"path": "/tmp/agent", "label": "main"}],
    )
    check(
        "agent default wins over fallback (label kept)",
        r.source == SOURCE_AGENT and r.dirs[0].label == "main",
        str(r),
    )

    r = resolve_effective_project_dirs(
        workspace_dir=ws,
        agent_project_dirs=[{"path": "/tmp/agent"}],
        session_project_dirs=[{"path": "/tmp/session"}],
    )
    check(
        "session override wins over agent",
        r.source == SOURCE_SESSION
        and str(r.primary_path) == "/tmp/session",
        str(r),
    )

    r = resolve_effective_project_dirs(
        workspace_dir=ws,
        agent_project_dirs=[{"path": "/tmp/agent"}],
        session_project_dirs=[{"path": "/tmp/session"}],
        request_override="/tmp/acp",
        mode_override=[{"path": "/tmp/mission"}],
        fork_project_dir="/tmp/worktree",
    )
    check(
        "fork override beats every other source",
        r.source == SOURCE_FORK and str(r.primary_path) == "/tmp/worktree",
        str(r),
    )

    r = resolve_effective_project_dirs(
        workspace_dir=ws,
        agent_project_dirs=[{"path": "/tmp/agent"}],
        fork_project_dir="/tmp/worktree",
    )
    check(
        "fork replaces the primary but keeps the rest",
        [str(e.path) for e in r.dirs]
        == [str(Path("/tmp/worktree")), str(Path("/tmp/agent"))],
        str(r),
    )

    r = resolve_effective_project_dirs(
        workspace_dir=ws,
        agent_project_dirs=[{"path": "  "}],
        session_project_dirs=None,
    )
    check(
        "blank values are ignored, not treated as a path",
        r.source == SOURCE_WORKSPACE_FALLBACK,
        str(r),
    )

    r = resolve_effective_project_dirs(
        workspace_dir=ws,
        agent_project_dirs=[{"path": "/tmp/definitely/not/here"}],
    )
    check(
        "missing dir is reported, not swallowed",
        r.dirs[0].exists is False,
        str(r),
    )


def test_migration(working_dir: Path) -> None:
    section("Legacy config migration")
    from qwenpaw.config.project_dir import migrate_project_dirs_in_place

    data = {"coding_mode": {"enabled": True, "project_dir": "/tmp/legacy"}}
    changed = migrate_project_dirs_in_place(data)
    check(
        "legacy value lifted into project_dirs",
        changed
        and data["project_dirs"] == [
            {"path": str(Path("/tmp/legacy")), "label": None},
        ]
        and "project_dir" not in data["coding_mode"],
        json.dumps(data),
    )
    check(
        "coding_mode.enabled is preserved",
        data["coding_mode"]["enabled"] is True,
        json.dumps(data),
    )
    check(
        "second run is a no-op (idempotent)",
        migrate_project_dirs_in_place(data) is False,
        json.dumps(data),
    )

    data = {
        "project_dirs": [{"path": "/tmp/new", "label": None}],
        "coding_mode": {"project_dir": "/tmp/old"},
    }
    migrate_project_dirs_in_place(data)
    check(
        "existing list wins over legacy",
        data["project_dirs"] == [{"path": "/tmp/new", "label": None}]
        and "project_dir" not in data["coding_mode"],
        json.dumps(data),
    )

    # The sandbox ships a "legacy" agent; loading it must migrate on disk.
    legacy_json = working_dir / "workspaces" / "legacy" / "agent.json"
    if legacy_json.is_file():
        from qwenpaw.config.config import load_agent_config

        cfg = load_agent_config("legacy")
        on_disk = json.loads(legacy_json.read_text(encoding="utf-8"))
        check(
            "loading the legacy agent migrates agent.json on disk",
            "project_dir" not in on_disk.get("coding_mode", {})
            and bool(on_disk.get("project_dirs")),
            json.dumps(on_disk.get("coding_mode")),
        )
        check(
            "migrated config resolves the project dirs",
            bool(cfg.project_dirs),
            f"project_dirs={cfg.project_dirs!r}",
        )


def test_tool_base_dir(working_dir: Path) -> None:
    section("Tool base directory")
    from qwenpaw.config.context import (
        get_tool_base_dir,
        set_current_project_dir,
        set_current_workspace_dir,
    )
    from qwenpaw.agents.tools.file_io import _resolve_file_path

    workspace = working_dir / "workspaces" / "default"
    project = working_dir.parent / "project"

    set_current_workspace_dir(workspace)
    set_current_project_dir(None)
    check(
        "no project dir -> base is the workspace",
        get_tool_base_dir() == workspace,
        str(get_tool_base_dir()),
    )

    set_current_project_dir(project)
    check(
        "primary project dir set -> base is the project",
        get_tool_base_dir() == project,
        str(get_tool_base_dir()),
    )
    check(
        "relative read path resolves into the primary project",
        _resolve_file_path("PROJECT_MARKER.txt")
        == str(project / "PROJECT_MARKER.txt"),
        _resolve_file_path("PROJECT_MARKER.txt"),
    )
    check(
        "absolute paths are left alone",
        _resolve_file_path(str(workspace / "agent.json"))
        == str(workspace / "agent.json"),
        _resolve_file_path(str(workspace / "agent.json")),
    )

    if (project / "PROJECT_MARKER.txt").is_file():
        resolved = Path(_resolve_file_path("PROJECT_MARKER.txt"))
        check(
            "the marker file is actually reachable that way",
            resolved.is_file(),
            str(resolved),
        )

    set_current_project_dir(None)
    set_current_workspace_dir(None)


def test_shell_default_cwd(working_dir: Path) -> None:
    section("Shell default cwd")
    from qwenpaw.config.context import (
        set_current_project_dir,
        set_current_workspace_dir,
    )
    from qwenpaw.agents.tools.shell import execute_shell_command

    workspace = working_dir / "workspaces" / "default"
    project = working_dir.parent / "project"
    set_current_workspace_dir(workspace)
    set_current_project_dir(project)

    def _block_text(block: object) -> str:
        # Content blocks are dict-like in some agentscope versions and
        # attribute objects in others; handle both rather than guessing.
        if isinstance(block, dict):
            return str(block.get("text", ""))
        return str(getattr(block, "text", "") or "")

    async def _run(cmd: str, **kwargs: object) -> str:
        chunk = await execute_shell_command(cmd, **kwargs)
        return "".join(_block_text(b) for b in (chunk.content or []))

    try:
        out = asyncio.run(_run("pwd"))
        check(
            "bare command runs in the primary project dir",
            str(project) in out or str(project.resolve()) in out,
            out.strip()[:200],
        )
        out = asyncio.run(_run("pwd", cwd=workspace))
        check(
            "explicit cwd= still wins",
            str(workspace) in out,
            out.strip()[:200],
        )
    except Exception as exc:  # pragma: no cover - diagnostic path
        check("shell tool is runnable", False, repr(exc))
    finally:
        set_current_project_dir(None)
        set_current_workspace_dir(None)


def test_governance_registration() -> None:
    section("Governance registration")
    from qwenpaw.governance.policy import (
        GovernanceAction,
        ToolCallSpec,
        _create_default_policy,
    )

    ws = "/tmp/gov-ws"
    project = "/tmp/gov-project"
    extra = "/tmp/gov-extra"
    policy = _create_default_policy(
        workspace_dir=ws,
        coding_project_dir=project,
        extra_project_dirs=[extra],
    )
    unresolved = [
        rule.match
        for rule in policy.user_rules
        if "PROJECT_DIR" in rule.match or "WORKSPACE_DIR" in rule.match
    ]
    check(
        "no placeholder survives into a resolved policy",
        not unresolved,
        str(unresolved),
    )

    def _tc(tool: str, target: str) -> ToolCallSpec:
        return ToolCallSpec(tool, target, "agent", "session")

    check(
        "writes inside the primary project dir are allowed",
        policy.evaluate(_tc("Write", f"{project}/src/x.py")).action
        is GovernanceAction.ALLOW,
    )
    check(
        "writes inside an EXTRA project dir are allowed",
        policy.evaluate(_tc("Write", f"{extra}/src/x.py")).action
        is GovernanceAction.ALLOW,
    )
    check(
        "writes inside the workspace are allowed",
        policy.evaluate(_tc("Write", f"{ws}/notes.md")).action
        is GovernanceAction.ALLOW,
    )
    check(
        "writes outside every granted dir are not auto-allowed",
        policy.evaluate(_tc("Write", "/tmp/gov-unrelated/x.py")).action
        is not GovernanceAction.ALLOW,
    )


def test_workspace_not_repointed(working_dir: Path) -> None:
    section("Workspace ContextVar is never repointed")
    from qwenpaw.config.context import (
        get_current_workspace_dir,
        get_tool_base_dir,
        set_current_project_dir,
        set_current_workspace_dir,
    )

    workspace = working_dir / "workspaces" / "default"
    worktree = working_dir.parent / "project"
    set_current_workspace_dir(workspace)
    set_current_project_dir(worktree)
    try:
        check(
            "workspace var still points at agent storage",
            get_current_workspace_dir() == workspace,
            str(get_current_workspace_dir()),
        )
        check(
            "tool base dir points at the project instead",
            get_tool_base_dir() == worktree,
            str(get_tool_base_dir()),
        )
        check(
            "the two are genuinely different paths",
            get_current_workspace_dir() != get_tool_base_dir(),
        )
    finally:
        set_current_project_dir(None)
        set_current_workspace_dir(None)


def test_session_override_across_turns(working_dir: Path) -> None:
    """A per-chat override must hold on turn 2, 3, 4 … not just turn 1.

    Exercises the real ChatManager and the real request-setup helper, which
    is what a hand-written mock could not do: a previous bug had the helper
    calling ``get_chat_id_by_session()`` with the wrong arity, and mocks
    written against the caller agreed with the mistake.
    """
    section("Session override across turns")

    from qwenpaw.app.channels.schema import DEFAULT_CHANNEL
    from qwenpaw.app.chats.manager import ChatManager
    from qwenpaw.app.chats.repo import JsonChatRepository
    from qwenpaw.config.project_dir import resolve_effective_project_dirs
    from qwenpaw.hooks.request_setup.contextvars_hook import (
        _session_project_dirs,
    )

    workspace = working_dir / "workspaces" / "default"
    agent_default = working_dir.parent / "project"
    session_project = working_dir.parent / "session-project"
    session_project.mkdir(parents=True, exist_ok=True)
    session_entries = [{"path": str(session_project), "label": None}]

    class _Ctx:
        def __init__(self, manager: object) -> None:
            self.session_id = "console:devcheck"
            self.workspace = SimpleNamespace(chat_manager=manager)
            self.request = SimpleNamespace(
                channel=DEFAULT_CHANNEL,
                user_id="devcheck",
            )

    async def run() -> None:
        repo_path = workspace / "chats-devcheck.json"
        if repo_path.exists():
            repo_path.unlink()
        manager = ChatManager(repo=JsonChatRepository(repo_path))
        chat = await manager.get_or_create_chat(
            "console:devcheck",
            "devcheck",
            DEFAULT_CHANNEL,
        )
        await manager.set_session_project_dirs(chat.id, session_entries)
        ctx = _Ctx(manager)

        check(
            "hook reads back the override just set",
            await _session_project_dirs(ctx) == session_entries,
        )

        all_session = True
        for _ in range(4):
            resolved = resolve_effective_project_dirs(
                workspace_dir=str(workspace),
                agent_project_dirs=[
                    {"path": str(agent_default), "label": None},
                ],
                session_project_dirs=await _session_project_dirs(ctx),
            )
            if resolved.source != "session":
                all_session = False
            # Every real turn touches the chat; make sure that keeps meta.
            await manager.touch_chat(chat.id)
        check(
            "override still wins after 4 turns (does not revert)",
            all_session,
        )

        reloaded = ChatManager(repo=JsonChatRepository(repo_path))
        check(
            "override survives a manager restart",
            await _session_project_dirs(_Ctx(reloaded)) == session_entries,
        )

        await manager.set_session_project_dirs(chat.id, None)
        resolved = resolve_effective_project_dirs(
            workspace_dir=str(workspace),
            agent_project_dirs=[
                {"path": str(agent_default), "label": None},
            ],
            session_project_dirs=await _session_project_dirs(ctx),
        )
        check(
            "clearing returns to the agent default",
            resolved.source == "agent",
            str(resolved),
        )
        repo_path.unlink(missing_ok=True)

    try:
        asyncio.run(run())
    except Exception as exc:  # pragma: no cover - diagnostic path
        check("session override checks are runnable", False, repr(exc))


def test_state_stays_out_of_the_project(working_dir: Path) -> None:
    """Mission and fork bookkeeping must not land in the user's repo.

    The project dirs are where the agent *works*; they are not a place to
    keep agent state. Fork checkouts are ~100 MB each and are never cleaned
    up, so this invariant is the difference between a clean repository and
    tens of gigabytes of dead worktrees inside it.
    """
    section("Agent state stays out of the project")

    import subprocess

    from qwenpaw.agents.fork_project import (
        git_root_from_worktree,
        registry_base_from_worktree,
    )
    from qwenpaw.modes.mission.state import create_loop_dir, missions_base

    workspace = working_dir / "workspaces" / "default"
    project = working_dir.parent / "project"

    check(
        "missions base is under the workspace",
        missions_base(workspace) == workspace / "missions",
        str(missions_base(workspace)),
    )
    check(
        "missions base is not under the project",
        not str(missions_base(workspace)).startswith(str(project)),
        str(missions_base(workspace)),
    )

    loop_dir = create_loop_dir(workspace)
    try:
        check(
            "a created mission dir lives in the workspace",
            loop_dir.is_relative_to(workspace),
            str(loop_dir),
        )
        check(
            "creating a mission adds nothing to the project",
            not (project / ".qwenpaw").exists(),
            str(project / ".qwenpaw"),
        )
    finally:
        shutil.rmtree(loop_dir, ignore_errors=True)

    # Fork: a worktree checked out in the workspace must still resolve back
    # to the project for git operations.
    if (project / ".git").exists():
        wt = workspace / ".qwenpaw" / "worktrees" / "devcheck"
        shutil.rmtree(wt, ignore_errors=True)
        wt.parent.mkdir(parents=True, exist_ok=True)
        added = subprocess.run(
            ["git", "worktree", "add", str(wt), "-b", "fork/devcheck"],
            cwd=str(project),
            capture_output=True,
            text=True,
            check=False,
        )
        if added.returncode == 0:
            try:
                check(
                    "registry base of a workspace worktree is the workspace",
                    registry_base_from_worktree(wt) == workspace.resolve(),
                    str(registry_base_from_worktree(wt)),
                )
                check(
                    "git root of a workspace worktree is the project",
                    git_root_from_worktree(wt) == project.resolve(),
                    str(git_root_from_worktree(wt)),
                )
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt)],
                    cwd=str(project),
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    ["git", "branch", "-D", "fork/devcheck"],
                    cwd=str(project),
                    capture_output=True,
                    check=False,
                )
        else:
            check(
                "git worktree add is usable",
                False,
                added.stderr.strip()[:200],
            )


def main() -> int:
    working_dir = _require_sandbox()
    print(f"Sandbox working dir: {working_dir}")

    test_resolver_precedence()
    test_migration(working_dir)
    test_tool_base_dir(working_dir)
    test_shell_default_cwd(working_dir)
    test_governance_registration()
    test_workspace_not_repointed(working_dir)
    test_session_override_across_turns(working_dir)
    test_state_stays_out_of_the_project(working_dir)

    print(f"\n{_CHECKS - len(_FAILURES)}/{_CHECKS} checks passed")
    if _FAILURES:
        print("\nFailed:")
        for name in _FAILURES:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
