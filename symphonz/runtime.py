from __future__ import annotations

from pathlib import Path
import os
import shlex
import stat
import subprocess

from symphonz.install import read_config


SYMPHONY_REPO_URL = "https://github.com/openai/symphony.git"


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_embedded_runtime(project_root: Path, skip_download: bool) -> None:
    bin_dir = project_root / ".symphonz" / "bin"
    runtime_dir = project_root / ".symphonz" / "runtime" / "symphony"
    bin_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "symphony"

    if skip_download:
        shim.write_text(
            "#!/bin/sh\n"
            "echo 'symphonz embedded runtime download was skipped; reinstall without --skip-runtime-download' >&2\n"
            "exit 2\n"
        )
        make_executable(shim)
        return

    if not runtime_dir.exists():
        subprocess.run(["git", "clone", SYMPHONY_REPO_URL, str(runtime_dir)], check=True)

    elixir_dir = runtime_dir / "elixir"
    if (elixir_dir / "mise.toml").exists():
        subprocess.run(["mise", "trust"], cwd=elixir_dir, check=True)
        subprocess.run(["mise", "install"], cwd=elixir_dir, check=True)
        subprocess.run(["mise", "exec", "--", "mix", "setup"], cwd=elixir_dir, check=True)
        subprocess.run(["mise", "exec", "--", "mix", "build"], cwd=elixir_dir, check=True)

    shim.write_text(
        "#!/bin/sh\n"
        'exec "$(dirname "$0")/../runtime/symphony/elixir/bin/symphony" "$@"\n'
    )
    make_executable(shim)


def build_run_command(project_root: Path) -> tuple[list[str], dict[str, str]]:
    config = read_config(project_root / ".symphonz" / "config.toml")
    command = [
        config["runtime"]["command"],
        ".symphonz/WORKFLOW.md",
        "--logs-root",
        config["logs"]["root"],
    ]
    env = {
        "SYMPHONZ_REPO_URL": config["git"]["remote"],
        "SYMPHONZ_BASE_BRANCH": config["git"]["base_branch"],
        "SYMPHONZ_MR_TARGET": config["git"]["mr_target"],
        "SYMPHONZ_GIT_PROVIDER": config["git"]["provider"],
        "GITLAB_BASE_URL": config["git"]["gitlab_base_url"],
    }
    return command, env


def run_installed(print_command: bool = False, project_root: Path | None = None) -> int:
    root = project_root or Path.cwd()
    command, env_updates = build_run_command(root)
    if print_command:
        print(" ".join(shlex.quote(part) for part in command))
        return 0
    env = os.environ.copy()
    env.update(env_updates)
    return subprocess.call(command, cwd=root, env=env)
