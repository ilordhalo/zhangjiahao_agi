from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphonz")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install = subcommands.add_parser("install", help="Install symphonz into the current project")
    install.add_argument("--runtime", choices=["embedded", "global"], default="embedded")
    install.add_argument("--yes", action="store_true", help="Accept detected defaults without interactive prompts")
    install.add_argument("--skip-runtime-download", action="store_true", help="Create embedded runtime layout without downloading Symphony")

    run = subcommands.add_parser("run", help="Run the installed Symphony workflow")
    run.add_argument("--print-command", action="store_true", help="Print the runtime command instead of executing it")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        from symphonz.install import install_project

        install_project(runtime_mode=args.runtime, assume_yes=args.yes, skip_runtime_download=args.skip_runtime_download)
        return 0

    if args.command == "run":
        from symphonz.runtime import run_installed

        return run_installed(print_command=args.print_command)

    parser.error(f"unsupported command: {args.command}")
    return 2
