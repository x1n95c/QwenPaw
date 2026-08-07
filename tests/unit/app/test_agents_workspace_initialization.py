# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for agent workspace initialization."""

import json
from pathlib import Path
from types import SimpleNamespace

from qwenpaw.app.crons.models import JobsFile
from qwenpaw.app.routers import agents as agents_router


def _stub_global_config(language: str = "en") -> SimpleNamespace:
    return SimpleNamespace(
        agents=SimpleNamespace(language=language),
    )


def test_initialize_agent_workspace_creates_runtime_compatible_files(
    monkeypatch,
    tmp_path,
):
    """New workspaces should match the runtime file contract."""
    import qwenpaw.config as config_module

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: _stub_global_config("en"),
    )
    monkeypatch.setattr(
        agents_router,
        "_install_initial_skills",
        lambda workspace_dir, skill_names: None,
    )

    agents_router._initialize_agent_workspace(tmp_path)

    assert (tmp_path / "sessions").is_dir()
    assert (tmp_path / "memory").is_dir()
    assert (tmp_path / "skills").is_dir()
    # One directory per cron job lives here, each holding that job's own
    # batch scripts.
    assert (tmp_path / "cron_jobs").is_dir()
    assert not (tmp_path / "active_skills").exists()
    assert not (tmp_path / "customized_skills").exists()
    # Asserted against the model rather than a literal: a file stamped
    # below `JobsFile.version` makes every new agent re-run the jobs.json
    # migration on its first load.
    assert json.loads(
        (tmp_path / "jobs.json").read_text(encoding="utf-8"),
    ) == {
        "version": JobsFile().version,
        "jobs": [],
    }
    assert json.loads(
        (tmp_path / "chats.json").read_text(encoding="utf-8"),
    ) == {
        "version": 1,
        "chats": [],
    }


def test_initialize_agent_workspace_applies_md_template_with_language(
    monkeypatch,
    tmp_path,
):
    """Workspace initialization should pass language and md_template_id."""
    import qwenpaw.config as config_module

    recorded_calls: list[tuple[str, Path, str | None]] = []

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: _stub_global_config("ru"),
    )
    monkeypatch.setattr(
        agents_router,
        "copy_workspace_md_files",
        lambda language, workspace_dir, md_template_id=None: (
            recorded_calls.append(
                (language, workspace_dir, md_template_id),
            )
        ),
    )
    monkeypatch.setattr(
        agents_router,
        "_install_initial_skills",
        lambda workspace_dir, skill_names: None,
    )

    agents_router._initialize_agent_workspace(
        tmp_path,
        md_template_id="qa",
    )

    assert recorded_calls == [("ru", tmp_path, "qa")]


def test_copying_an_agent_carries_its_job_scripts_and_templates(
    tmp_path: Path,
):
    """`jobs.json` is copied with its uuids intact, so the scripts those
    jobs run must travel with it.

    Without this the copied agent's jobs look perfectly fine in the UI and
    fail at their next fire with "script not found" — the worst failure
    mode in the whole per-job change, because nothing surfaces until the
    schedule comes round.
    """
    from qwenpaw.app.routers.agents import _copy_selected_workspace_files

    source = tmp_path / "src"
    dest = tmp_path / "dst"
    job_id = "3f4a1c2e-0000-4000-8000-000000000001"
    scripts = source / "cron_jobs" / job_id / "batch"
    scripts.mkdir(parents=True)
    (scripts / "collect.json").write_text("[]", encoding="utf-8")
    (source / "jobs.json").write_text(
        json.dumps({"version": 2, "jobs": [{"id": job_id}]}),
        encoding="utf-8",
    )
    tpl = source / "cron_templates" / "mine"
    tpl.mkdir(parents=True)
    (tpl / "TEMPLATE.md").write_text(
        "---\nname: mine\n---\n", encoding="utf-8"
    )
    dest.mkdir()

    _copy_selected_workspace_files(
        request=SimpleNamespace(
            copy_md_files=False,
            copy_skills=False,
            copy_jobs=True,
        ),
        source_workspace=source,
        workspace_dir=dest,
    )

    landed = dest / "cron_jobs" / job_id / "batch" / "collect.json"
    assert landed.is_file()
    assert (dest / "cron_templates" / "mine" / "TEMPLATE.md").is_file()

    # And the copied job resolves its script in the *new* workspace.
    from qwenpaw.app.crons.script_paths import resolve_job_script

    assert resolve_job_script(dest, job_id, "collect") == landed


def test_copying_without_jobs_leaves_the_scripts_behind(tmp_path: Path):
    from qwenpaw.app.routers.agents import _copy_selected_workspace_files

    source = tmp_path / "src"
    dest = tmp_path / "dst"
    (source / "cron_jobs" / "j" / "batch").mkdir(parents=True)
    dest.mkdir()

    _copy_selected_workspace_files(
        request=SimpleNamespace(
            copy_md_files=False,
            copy_skills=False,
            copy_jobs=False,
        ),
        source_workspace=source,
        workspace_dir=dest,
    )

    assert not (dest / "cron_jobs").exists()
