from __future__ import annotations

import argparse
import sys

from symphonz import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphonz")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install = subcommands.add_parser("install", help="Install symphonz into the current project")
    install.add_argument("--yes", action="store_true", help="Accept detected defaults without interactive prompts")
    install.add_argument("--skip-linear-preflight", action="store_true", help="Skip the Linear connectivity check")
    install.add_argument("--linear-project", help="Linear project slug or ID")
    install.add_argument("--linear-api-key-env", help="Name of the environment variable containing the Linear API key")
    install.add_argument("--git-provider", choices=["github", "gitlab"])
    install.add_argument("--repo-url", help="Git remote URL cloned into issue workspaces")
    install.add_argument("--base-branch", help="Base branch for issue branches")
    install.add_argument("--target-branch", help="Pull/merge request target branch")
    install.add_argument("--gitlab-base-url", help="GitLab instance URL")

    run = subcommands.add_parser("run", help="Run the installed Symphony workflow")
    run.add_argument("--print-command", action="store_true", help="Print the runtime command instead of executing it")
    run.add_argument("--port", type=int, help="Serve the built-in dashboard on this port")

    subcommands.add_parser("version", help="Print symphonz version")

    return parser


def build_service_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphonz service")
    parser.add_argument("workflow")
    parser.add_argument("--logs-root", default=".symphonz/logs")
    parser.add_argument("--port", type=int)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "service":
        args = build_service_parser().parse_args(argv[1:])
        from pathlib import Path
        from symphonz.service.runner import run_service

        return run_service(
            project_root=Path.cwd(),
            workflow_path=Path(args.workflow),
            logs_root=Path(args.logs_root),
            port=args.port,
            once=args.once,
        )

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        from symphonz.install import install_project

        install_project(
            assume_yes=args.yes,
            skip_linear_preflight=args.skip_linear_preflight,
            linear_project_slug=args.linear_project,
            linear_api_key_env=args.linear_api_key_env,
            git_provider=args.git_provider,
            repo_url=args.repo_url,
            base_branch=args.base_branch,
            mr_target=args.target_branch,
            gitlab_base_url=args.gitlab_base_url,
        )
        return 0

    if args.command == "run":
        from symphonz.runtime import run_installed

        return run_installed(print_command=args.print_command, port=args.port)

    if args.command == "version":
        print(f"symphonz {__version__}")
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2
