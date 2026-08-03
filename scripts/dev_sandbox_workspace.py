#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spin up a throwaway QwenPaw workspace in a tmp dir, plus a demo project.

Why this exists
---------------
Testing the workspace / project-dir split by hand against ``~/.qwenpaw``
means touching your real agents, chats and memory. This script builds an
isolated tree instead::

    <root>/working/                     # QWENPAW_WORKING_DIR
      config.json                       # one agent: "default"
      workspaces/default/agent.json     # project_dir -> ../../project
    <root>/project/                     # a real git repo to act on
      README.md, src/hello.py

Everything the agent writes (sessions, memory, skills) lands under
``working/``; everything it *works on* lands under ``project/``. If a file
shows up on the wrong side, the separation is broken — that is the whole
point of the layout.

Usage
-----
Create and print the env you need::

    python scripts/dev_sandbox_workspace.py

Then follow the printed ``export`` line, or eval it directly::

    eval "$(python scripts/dev_sandbox_workspace.py --print-env-only)"
    qwenpaw app          # or: pytest tests/integration/...

Options::

    --root PATH        where to build it (default: a fresh mkdtemp)
    --agent-id ID      agent id to create (default: default)
    --no-project-dir   leave project_dir unset, to test workspace fallback
    --no-git           skip ``git init`` in the project
    --force            overwrite an existing --root
    --print-env-only   emit only shell exports, nothing else
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_AGENT_JSON_MINIMAL = {
    "name": "Default Agent",
    "description": "Throwaway agent for project-dir separation testing",
    "backend": "qwenpaw",
    "coding_mode": {"enabled": False},
}


def _build_project(project_dir: Path, use_git: bool) -> None:
    """Create a small but real project tree for the agent to act on."""
    (project_dir / "src").mkdir(parents=True, exist_ok=True)
    (project_dir / "README.md").write_text(
        "# Demo project\n\n"
        "Sandbox project for QwenPaw project-dir testing.\n",
        encoding="utf-8",
    )
    (project_dir / "src" / "hello.py").write_text(
        'def hello(name: str) -> str:\n    return f"hello {name}"\n',
        encoding="utf-8",
    )
    # A marker whose location proves which base dir a relative path used.
    (project_dir / "PROJECT_MARKER.txt").write_text(
        "If a tool reads 'PROJECT_MARKER.txt' by relative path and finds "
        "this file, the project dir is the tool base dir.\n",
        encoding="utf-8",
    )

    if not use_git:
        return
    if shutil.which("git") is None:
        print("  ! git not found; skipping git init", file=sys.stderr)
        return
    subprocess.run(
        ["git", "init", "-q"],
        cwd=project_dir,
        check=False,
    )
    # Commit so `git log` / watchdog have something to look at. Use -c so we
    # never depend on (or mutate) the developer's global git identity.
    subprocess.run(["git", "add", "-A"], cwd=project_dir, check=False)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=dev@example.com",
            "-c",
            "user.name=QwenPaw Sandbox",
            "commit",
            "-q",
            "-m",
            "Initial sandbox commit",
        ],
        cwd=project_dir,
        check=False,
    )


def _build_workspace(
    working_dir: Path,
    agent_id: str,
    project_dir: Path | None,
) -> Path:
    """Write config.json + workspaces/<id>/agent.json."""
    workspace_dir = working_dir / "workspaces" / agent_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    agent_json = dict(_AGENT_JSON_MINIMAL)
    agent_json["id"] = agent_id
    agent_json["workspace_dir"] = str(workspace_dir)
    # None (rather than omitted) documents the fallback case explicitly.
    agent_json["project_dir"] = str(project_dir) if project_dir else None
    (workspace_dir / "agent.json").write_text(
        json.dumps(agent_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    config = {
        "agents": {
            "active_agent": agent_id,
            "profiles": {
                agent_id: {
                    "id": agent_id,
                    "workspace_dir": str(workspace_dir),
                    "enabled": True,
                },
            },
        },
    }
    (working_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return workspace_dir


def _write_legacy_agent(working_dir: Path, project_dir: Path) -> Path:
    """Add a second agent still using the legacy coding_mode.project_dir.

    Loading this agent exercises the one-time migration: the nested value
    should move to the top level and the nested key should disappear.
    """
    agent_id = "legacy"
    workspace_dir = working_dir / "workspaces" / agent_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "agent.json").write_text(
        json.dumps(
            {
                "id": agent_id,
                "name": "Legacy Agent",
                "workspace_dir": str(workspace_dir),
                "coding_mode": {
                    "enabled": True,
                    "project_dir": str(project_dir),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    config_path = working_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agents"]["profiles"][agent_id] = {
        "id": agent_id,
        "workspace_dir": str(workspace_dir),
        "enabled": True,
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return workspace_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create an isolated tmp QwenPaw workspace + demo project for "
            "testing the workspace / project-dir split."
        ),
    )
    parser.add_argument("--root", default=None)
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--no-project-dir", action="store_true")
    parser.add_argument("--no-git", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-env-only", action="store_true")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).expanduser().resolve()
        if root.exists():
            if not args.force:
                print(
                    f"error: {root} already exists (use --force to replace)",
                    file=sys.stderr,
                )
                return 1
            shutil.rmtree(root)
        root.mkdir(parents=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="qwenpaw-sandbox-")).resolve()

    working_dir = root / "working"
    project_dir = root / "project"
    working_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _build_project(project_dir, use_git=not args.no_git)
    workspace_dir = _build_workspace(
        working_dir,
        args.agent_id,
        None if args.no_project_dir else project_dir,
    )
    legacy_workspace = _write_legacy_agent(working_dir, project_dir)

    exports = (
        f"export QWENPAW_WORKING_DIR={working_dir}\n"
        f"export QWENPAW_SECRET_DIR={root / 'secret'}\n"
        f"export QWENPAW_BACKUP_DIR={root / 'backups'}"
    )

    if args.print_env_only:
        print(exports)
        return 0

    print(f"Sandbox root:      {root}")
    print(f"  working dir:     {working_dir}   (QWENPAW_WORKING_DIR)")
    print(f"  agent workspace: {workspace_dir}")
    print(f"  legacy agent ws: {legacy_workspace}  (tests migration)")
    print(
        "  project dir:     "
        + (
            "(unset — tests workspace fallback)"
            if args.no_project_dir
            else str(project_dir)
        ),
    )
    print()
    print("Activate it:")
    print()
    print(exports)
    print()
    print("Or in one shot:")
    print()
    print(
        "  eval \"$(python scripts/dev_sandbox_workspace.py "
        f"--root {root} --force --print-env-only)\"",
    )
    print()
    print("Then verify the split:")
    print()
    print("  python scripts/dev_check_project_dir.py")
    print()
    print(f"Clean up with:  rm -rf {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
