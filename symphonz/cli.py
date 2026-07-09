from __future__ import annotations

import argparse
import sys

from symphonz import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphonz")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install = subcommands.add_parser("install", help="Install symphonz into the current project")
    install.add_argument("--runtime", choices=["embedded", "global"], default="embedded")
    install.add_argument("--yes", action="store_true", help="Accept detected defaults without interactive prompts")
    install.add_argument("--skip-runtime-download", action="store_true", help="Create embedded runtime layout without downloading Symphony")

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

        install_project(runtime_mode=args.runtime, assume_yes=args.yes, skip_runtime_download=args.skip_runtime_download)
        return 0

    if args.command == "run":
        from symphonz.runtime import run_installed

        return run_installed(print_command=args.print_command, port=args.port)

    if args.command == "version":
        print(f"symphonz {__version__}")
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2
