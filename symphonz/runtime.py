from __future__ import annotations

from pathlib import Path
import os
import shlex

from symphonz.install import read_config


def build_run_command(project_root: Path, port: int | None = None) -> tuple[list[str], dict[str, str]]:
    config = read_config(project_root / ".symphonz" / "config.toml")
    command = ["symphonz", "service", ".symphonz/WORKFLOW.md", "--logs-root", config["logs"]["root"]]
    if port is not None:
        command.extend(["--port", str(port)])

    env = {
        "SYMPHONZ_REPO_URL": config["git"]["remote"],
        "SYMPHONZ_BASE_BRANCH": config["git"]["base_branch"],
        "SYMPHONZ_MR_TARGET": config["git"]["mr_target"],
        "SYMPHONZ_GIT_PROVIDER": config["git"]["provider"],
    }
    if config["git"].get("gitlab_base_url"):
        env["GITLAB_BASE_URL"] = config["git"]["gitlab_base_url"]
    return command, env


def run_installed(print_command: bool = False, port: int | None = None, project_root: Path | None = None) -> int:
    root = project_root or Path.cwd()
    command, env_updates = build_run_command(root, port=port)
    if print_command:
        assignments = [f"{key}={shlex.quote(value)}" for key, value in env_updates.items()]
        print(" ".join([*assignments, *(shlex.quote(part) for part in command)]))
        return 0

    from symphonz.service.runner import run_service

    config = read_config(root / ".symphonz" / "config.toml")

    env = os.environ.copy()
    env.update(env_updates)
    old_env = os.environ.copy()
    try:
        os.environ.update(env)
        return run_service(
            project_root=root,
            workflow_path=root / ".symphonz" / "WORKFLOW.md",
            logs_root=root / config["logs"]["root"],
            port=port,
            once=False,
        )
    finally:
        os.environ.clear()
        os.environ.update(old_env)
