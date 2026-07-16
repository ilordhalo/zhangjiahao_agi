from __future__ import annotations

from pathlib import Path
import os
import shlex

from symphonz.install import read_config


def _dashboard_values(
    config: dict[str, dict[str, str]],
    host: str | None,
    port: int | None,
) -> tuple[str, int | None, str | None, str, int]:
    dashboard = config.get("dashboard")
    if dashboard is None:
        return host or "127.0.0.1", port, None, "admin", 30

    try:
        configured_port = int(dashboard["port"])
        session_days = int(dashboard["session_days"])
    except (KeyError, ValueError) as error:
        raise RuntimeError(
            "Dashboard configuration is invalid; run `symphonz configure-dashboard` to regenerate it"
        ) from error
    return (
        host or dashboard.get("host", "127.0.0.1"),
        port if port is not None else configured_port,
        dashboard.get("public_base_url") or None,
        dashboard.get("username", "admin"),
        session_days,
    )


def _extend_dashboard_command(
    command: list[str],
    host: str,
    port: int | None,
    public_base_url: str | None,
    dashboard_username: str,
    session_days: int,
) -> None:
    if port is None:
        return
    command.extend(["--host", host, "--port", str(port)])
    if public_base_url is not None:
        command.extend(["--public-base-url", public_base_url])
    command.extend(
        [
            "--dashboard-username",
            dashboard_username,
            "--session-days",
            str(session_days),
        ]
    )


def build_run_command(
    project_root: Path,
    host: str | None = None,
    port: int | None = None,
) -> tuple[list[str], dict[str, str]]:
    config = read_config(project_root / ".symphonz" / "config.toml")
    command = ["symphonz", "service", ".symphonz/WORKFLOW.md", "--logs-root", config["logs"]["root"]]
    if "dashboard" in config:
        dashboard_values = _dashboard_values(config, host, port)
        _extend_dashboard_command(command, *dashboard_values)
    else:
        if host is not None:
            command.extend(["--host", host])
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


def run_installed(
    print_command: bool = False,
    host: str | None = None,
    port: int | None = None,
    project_root: Path | None = None,
) -> int:
    root = project_root or Path.cwd()
    command, env_updates = build_run_command(root, host=host, port=port)
    if print_command:
        assignments = [f"{key}={shlex.quote(value)}" for key, value in env_updates.items()]
        print(" ".join([*assignments, *(shlex.quote(part) for part in command)]))
        return 0

    from symphonz.service.runner import run_service

    config = read_config(root / ".symphonz" / "config.toml")
    dashboard_host, dashboard_port, public_base_url, dashboard_username, session_days = (
        _dashboard_values(config, host, port)
    )

    env = os.environ.copy()
    env.update(env_updates)
    old_env = os.environ.copy()
    try:
        os.environ.update(env)
        return run_service(
            project_root=root,
            workflow_path=root / ".symphonz" / "WORKFLOW.md",
            logs_root=root / config["logs"]["root"],
            port=dashboard_port,
            once=False,
            host=dashboard_host,
            public_base_url=public_base_url,
            dashboard_username=dashboard_username,
            session_days=session_days,
        )
    finally:
        os.environ.clear()
        os.environ.update(old_env)
