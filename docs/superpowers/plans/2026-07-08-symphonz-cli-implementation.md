# Symphonz CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working `symphonz` command line tool with `install` and `run` for project-local Symphony orchestration.

**Architecture:** Implement a small Python standard-library CLI at the repository root. `bin/symphonz` is the executable wrapper, `symphonz/cli.py` owns argument parsing and orchestration, `symphonz/install.py` owns project detection and installation, `symphonz/workflow.py` renders `.symphonz/WORKFLOW.md`, and `symphonz/runtime.py` owns runtime setup and process launching.

**Tech Stack:** Python 3.11+ standard library, `unittest`, Git CLI, optional `glab`, optional `mise`, existing root `WORKFLOW.md` as the workflow template.

## Global Constraints

- The CLI command name is `symphonz`.
- `symphonz install` creates `.symphonz`.
- Default runtime mode is embedded.
- `symphonz install --runtime global` skips runtime download/build.
- Workspace root is `.symphonz/workspace`.
- Secrets are referenced by environment variable name instead of written directly to `.symphonz/config.toml`.
- Linear issue workspace directories use the issue identifier managed by Symphony.
- `Done` is a publish trigger, not a terminal state.
- GitLab is the default review target.
- Default GitLab base URL is `https://gitlab.example.com`.
- Default base branch and merge request target are `main`.
- Use only Python standard library dependencies for the first CLI version.

---

## File Structure

- Create `bin/symphonz`: executable Python wrapper that calls `symphonz.cli.main`.
- Create `symphonz/__init__.py`: package marker and version string.
- Create `symphonz/cli.py`: argparse entrypoint for `install` and `run`.
- Create `symphonz/install.py`: dataclass config, prompt collection, Git detection, directory creation, config writing, `.gitignore` update, and install orchestration.
- Create `symphonz/workflow.py`: render root `WORKFLOW.md` into `.symphonz/WORKFLOW.md` with project-specific values.
- Create `symphonz/runtime.py`: embedded/global runtime setup and launch command construction.
- Create `tests/test_symphonz_cli.py`: unit tests for config writing, workflow rendering, install layout, and run command construction.
- Modify `symphony_readme.md`: document local development usage for `bin/symphonz`.

---

### Task 1: CLI Skeleton and Config Model

**Files:**
- Create: `bin/symphonz`
- Create: `symphonz/__init__.py`
- Create: `symphonz/cli.py`
- Create: `symphonz/install.py`
- Test: `tests/test_symphonz_cli.py`

**Interfaces:**
- Produces: `symphonz.cli.main(argv: list[str] | None = None) -> int`
- Produces: `symphonz.install.InstallConfig`
- Produces: `symphonz.install.write_config(path: Path, config: InstallConfig) -> None`
- Produces: `symphonz.install.read_config(path: Path) -> dict[str, dict[str, str]]`

- [ ] **Step 1: Write the failing tests**

Add this initial test file:

```python
from pathlib import Path
import tempfile
import unittest

from symphonz.install import InstallConfig, read_config, write_config


class ConfigTests(unittest.TestCase):
    def test_write_config_uses_env_var_for_linear_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command=".symphonz/bin/symphony",
                linear_api_key_env="LINEAR_API_KEY",
                linear_project_slug="REPLACE_WITH_LINEAR_PROJECT_SLUG",
                git_provider="gitlab",
                repo_url="https://example.com/your-org/your-repo.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="https://gitlab.example.com",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
            )

            write_config(path, config)

            content = path.read_text()
            self.assertIn('[runtime]', content)
            self.assertIn('mode = "embedded"', content)
            self.assertIn('api_key_env = "LINEAR_API_KEY"', content)
            self.assertNotIn("lin_api_", content)
            parsed = read_config(path)
            self.assertEqual(parsed["linear"]["project_slug"], "REPLACE_WITH_LINEAR_PROJECT_SLUG")
            self.assertEqual(parsed["git"]["gitlab_base_url"], "https://gitlab.example.com")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_symphonz_cli.ConfigTests.test_write_config_uses_env_var_for_linear_key -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'symphonz'`.

- [ ] **Step 3: Create the CLI package skeleton**

Create `symphonz/__init__.py`:

```python
"""Project-local installer and launcher for Symphony workflows."""

__version__ = "0.1.0"
```

Create `bin/symphonz`:

```python
#!/usr/bin/env python3
from symphonz.cli import main

raise SystemExit(main())
```

Create `symphonz/cli.py`:

```python
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
```

Create `symphonz/install.py` with config read/write:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class InstallConfig:
    runtime_mode: str
    runtime_command: str
    linear_api_key_env: str
    linear_project_slug: str
    git_provider: str
    repo_url: str
    base_branch: str
    mr_target: str
    gitlab_base_url: str
    workspace_root: str
    logs_root: str


def toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_config(path: Path, config: InstallConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "[runtime]",
            f"mode = {toml_quote(config.runtime_mode)}",
            f"command = {toml_quote(config.runtime_command)}",
            "",
            "[linear]",
            f"api_key_env = {toml_quote(config.linear_api_key_env)}",
            f"project_slug = {toml_quote(config.linear_project_slug)}",
            "",
            "[git]",
            f"provider = {toml_quote(config.git_provider)}",
            f"remote = {toml_quote(config.repo_url)}",
            f"base_branch = {toml_quote(config.base_branch)}",
            f"mr_target = {toml_quote(config.mr_target)}",
            f"gitlab_base_url = {toml_quote(config.gitlab_base_url)}",
            "",
            "[workspace]",
            f"root = {toml_quote(config.workspace_root)}",
            "",
            "[logs]",
            f"root = {toml_quote(config.logs_root)}",
            "",
        ]
    )
    path.write_text(content)


def read_config(path: Path) -> dict[str, dict[str, str]]:
    with path.open("rb") as file:
        return tomllib.load(file)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_symphonz_cli.ConfigTests.test_write_config_uses_env_var_for_linear_key -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/symphonz symphonz/__init__.py symphonz/cli.py symphonz/install.py tests/test_symphonz_cli.py
git commit -m "feat(cli): add symphonz command skeleton"
```

---

### Task 2: Git Detection and Interactive Install Inputs

**Files:**
- Modify: `symphonz/install.py`
- Modify: `tests/test_symphonz_cli.py`

**Interfaces:**
- Consumes: `InstallConfig`
- Produces: `symphonz.install.detect_git_defaults(project_root: Path) -> dict[str, str]`
- Produces: `symphonz.install.collect_install_config(project_root: Path, runtime_mode: str, assume_yes: bool, input_func: Callable[[str], str] = input) -> InstallConfig`

- [ ] **Step 1: Write the failing tests**

Append these tests:

```python
import subprocess

from symphonz.install import collect_install_config, detect_git_defaults


class InstallInputTests(unittest.TestCase):
    def test_detect_git_defaults_reads_remote_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.com/group/repo.git"],
                cwd=root,
                check=True,
            )

            defaults = detect_git_defaults(root)

            self.assertEqual(defaults["repo_url"], "https://example.com/group/repo.git")
            self.assertEqual(defaults["base_branch"], "main")

    def test_collect_install_config_uses_answers_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.com/group/repo.git"],
                cwd=root,
                check=True,
            )
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            config = collect_install_config(root, "global", False, input_func=lambda prompt: next(answers))

            self.assertEqual(config.runtime_mode, "global")
            self.assertEqual(config.runtime_command, "symphony")
            self.assertEqual(config.linear_project_slug, "project-slug")
            self.assertEqual(config.git_provider, "gitlab")
            self.assertEqual(config.repo_url, "https://example.com/group/repo.git")
            self.assertEqual(config.base_branch, "main")
            self.assertEqual(config.mr_target, "main")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_symphonz_cli.InstallInputTests -v`

Expected: FAIL with import errors for `collect_install_config` and `detect_git_defaults`.

- [ ] **Step 3: Implement Git detection and prompt collection**

Add to `symphonz/install.py`:

```python
from collections.abc import Callable
import subprocess


DEFAULT_GITLAB_BASE_URL = "https://gitlab.example.com"


def run_git(project_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def require_git_repo(project_root: Path) -> None:
    inside = run_git(project_root, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        raise RuntimeError("symphonz install must be run inside a Git repository")


def detect_git_defaults(project_root: Path) -> dict[str, str]:
    require_git_repo(project_root)
    repo_url = run_git(project_root, ["remote", "get-url", "origin"])
    base_branch = run_git(project_root, ["branch", "--show-current"]) or "main"
    return {
        "repo_url": repo_url,
        "base_branch": base_branch,
        "mr_target": base_branch,
        "git_provider": "gitlab",
        "gitlab_base_url": DEFAULT_GITLAB_BASE_URL,
    }


def prompt_value(input_func: Callable[[str], str], label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input_func(f"{label}{suffix}: ").strip()
    return value or default


def collect_install_config(
    project_root: Path,
    runtime_mode: str,
    assume_yes: bool,
    input_func: Callable[[str], str] = input,
) -> InstallConfig:
    defaults = detect_git_defaults(project_root)

    if assume_yes:
        linear_api_key_env = "LINEAR_API_KEY"
        linear_project_slug = ""
        git_provider = defaults["git_provider"]
        repo_url = defaults["repo_url"]
        base_branch = defaults["base_branch"]
        mr_target = defaults["mr_target"]
        gitlab_base_url = defaults["gitlab_base_url"]
    else:
        linear_api_key_env = prompt_value(input_func, "Linear API key environment variable", "LINEAR_API_KEY")
        linear_project_slug = prompt_value(input_func, "Linear project slug or ID", "")
        git_provider = prompt_value(input_func, "Git provider", defaults["git_provider"])
        repo_url = prompt_value(input_func, "Git remote URL", defaults["repo_url"])
        base_branch = prompt_value(input_func, "Base branch", defaults["base_branch"])
        mr_target = prompt_value(input_func, "Merge request target branch", defaults["mr_target"])
        gitlab_base_url = prompt_value(input_func, "GitLab base URL", defaults["gitlab_base_url"])

    if not linear_project_slug:
        raise RuntimeError("Linear project slug or ID is required")
    if not repo_url:
        raise RuntimeError("Git remote URL is required")

    runtime_command = ".symphonz/bin/symphony" if runtime_mode == "embedded" else "symphony"
    return InstallConfig(
        runtime_mode=runtime_mode,
        runtime_command=runtime_command,
        linear_api_key_env=linear_api_key_env,
        linear_project_slug=linear_project_slug,
        git_provider=git_provider,
        repo_url=repo_url,
        base_branch=base_branch,
        mr_target=mr_target,
        gitlab_base_url=gitlab_base_url,
        workspace_root=".symphonz/workspace",
        logs_root=".symphonz/logs",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_symphonz_cli.ConfigTests tests.test_symphonz_cli.InstallInputTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add symphonz/install.py tests/test_symphonz_cli.py
git commit -m "feat(cli): collect symphonz install settings"
```

---

### Task 3: Workflow Rendering and Install Layout

**Files:**
- Create: `symphonz/workflow.py`
- Modify: `symphonz/install.py`
- Modify: `tests/test_symphonz_cli.py`

**Interfaces:**
- Consumes: `InstallConfig`
- Produces: `symphonz.workflow.render_workflow(template: str, config: InstallConfig) -> str`
- Produces: `symphonz.workflow.write_workflow(project_root: Path, config: InstallConfig) -> Path`
- Produces: `symphonz.install.install_project(runtime_mode: str, assume_yes: bool, skip_runtime_download: bool) -> Path`

- [ ] **Step 1: Write the failing tests**

Append these tests:

```python
from symphonz.install import install_project
from symphonz.workflow import render_workflow


class WorkflowInstallTests(unittest.TestCase):
    def make_config(self) -> InstallConfig:
        return InstallConfig(
            runtime_mode="global",
            runtime_command="symphony",
            linear_api_key_env="LINEAR_API_KEY",
            linear_project_slug="project-slug",
            git_provider="gitlab",
            repo_url="https://example.com/group/repo.git",
            base_branch="main",
            mr_target="main",
            gitlab_base_url="https://gitlab.example.com",
            workspace_root=".symphonz/workspace",
            logs_root=".symphonz/logs",
        )

    def test_render_workflow_replaces_project_values(self):
        template = Path("WORKFLOW.md").read_text()

        rendered = render_workflow(template, self.make_config())

        self.assertIn('api_key: $LINEAR_API_KEY', rendered)
        self.assertIn('project_slug: "project-slug"', rendered)
        self.assertIn('workspace:\n  root: .symphonz/workspace', rendered)
        self.assertIn('SYMPHONZ_REPO_URL:-https://example.com/group/repo.git', rendered)
        self.assertIn('https://gitlab.example.com', rendered)
        self.assertIn("- `Done` -> implementation is considered complete", rendered)

    def test_install_project_global_creates_expected_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            install_project(
                project_root=root,
                runtime_mode="global",
                assume_yes=False,
                skip_runtime_download=False,
                input_func=lambda prompt: next(answers),
            )

            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "workspace").is_dir())
            self.assertTrue((root / ".symphonz" / "logs").is_dir())
            self.assertFalse((root / ".symphonz" / "runtime").exists())
            self.assertIn(".symphonz/workspace/", (root / ".gitignore").read_text())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_symphonz_cli.WorkflowInstallTests -v`

Expected: FAIL with import error for `symphonz.workflow`.

- [ ] **Step 3: Implement workflow rendering and install layout**

Create `symphonz/workflow.py`:

```python
from __future__ import annotations

from pathlib import Path
import re

from symphonz.install import InstallConfig


def template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "WORKFLOW.md"


def render_workflow(template: str, config: InstallConfig) -> str:
    rendered = re.sub(
        r'project_slug: "[^"]*"',
        f'project_slug: "{config.linear_project_slug}"',
        template,
        count=1,
    )
    rendered = re.sub(
        r"api_key: \$[A-Z0-9_]+",
        f"api_key: ${config.linear_api_key_env}",
        rendered,
        count=1,
    )
    rendered = re.sub(
        r"workspace:\n  root: [^\n]+",
        f"workspace:\n  root: {config.workspace_root}",
        rendered,
        count=1,
    )
    rendered = re.sub(
        r'\$\{SYMPHONZ_REPO_URL:-[^}]+\}',
        "${SYMPHONZ_REPO_URL:-" + config.repo_url + "}",
        rendered,
        count=1,
    )
    rendered = re.sub(
        r"expected base URL is `[^`]+`",
        f"expected base URL is `{config.gitlab_base_url}`",
        rendered,
        count=1,
    )
    return rendered


def write_workflow(project_root: Path, config: InstallConfig) -> Path:
    destination = project_root / ".symphonz" / "WORKFLOW.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_workflow(template_path().read_text(), config)
    destination.write_text(rendered)
    return destination
```

Add these functions to `symphonz/install.py`:

```python
def ensure_gitignore(project_root: Path) -> None:
    gitignore = project_root / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    additions = [".symphonz/workspace/", ".symphonz/logs/", ".symphonz/runtime/"]
    updated = existing[:]
    for item in additions:
        if item not in updated:
            updated.append(item)
    gitignore.write_text("\n".join(updated).rstrip() + "\n")


def create_base_layout(project_root: Path) -> None:
    for relative in [".symphonz", ".symphonz/workspace", ".symphonz/logs"]:
        (project_root / relative).mkdir(parents=True, exist_ok=True)


def install_project(
    project_root: Path | None = None,
    runtime_mode: str = "embedded",
    assume_yes: bool = False,
    skip_runtime_download: bool = False,
    input_func: Callable[[str], str] = input,
) -> Path:
    root = project_root or Path.cwd()
    config = collect_install_config(root, runtime_mode, assume_yes, input_func=input_func)
    create_base_layout(root)
    write_config(root / ".symphonz" / "config.toml", config)

    from symphonz.workflow import write_workflow

    write_workflow(root, config)
    ensure_gitignore(root)

    if runtime_mode == "embedded":
        from symphonz.runtime import install_embedded_runtime

        install_embedded_runtime(root, skip_download=skip_runtime_download)

    return root / ".symphonz"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_symphonz_cli.WorkflowInstallTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add symphonz/workflow.py symphonz/install.py tests/test_symphonz_cli.py
git commit -m "feat(cli): generate symphonz project layout"
```

---

### Task 4: Embedded Runtime Setup and Run Command

**Files:**
- Create: `symphonz/runtime.py`
- Modify: `tests/test_symphonz_cli.py`

**Interfaces:**
- Consumes: `.symphonz/config.toml`
- Produces: `symphonz.runtime.install_embedded_runtime(project_root: Path, skip_download: bool) -> None`
- Produces: `symphonz.runtime.build_run_command(project_root: Path) -> tuple[list[str], dict[str, str]]`
- Produces: `symphonz.runtime.run_installed(print_command: bool = False, project_root: Path | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

Append these tests:

```python
from symphonz.runtime import build_run_command, install_embedded_runtime


class RuntimeTests(unittest.TestCase):
    def test_install_embedded_runtime_skip_download_creates_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            install_embedded_runtime(root, skip_download=True)

            shim = root / ".symphonz" / "bin" / "symphony"
            self.assertTrue(shim.exists())
            self.assertIn("runtime download was skipped", shim.read_text())
            self.assertTrue(shim.stat().st_mode & 0o111)

    def test_build_run_command_embedded_exports_expected_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = InstallConfig(
                runtime_mode="embedded",
                runtime_command=".symphonz/bin/symphony",
                linear_api_key_env="LINEAR_API_KEY",
                linear_project_slug="project-slug",
                git_provider="gitlab",
                repo_url="https://example.com/group/repo.git",
                base_branch="main",
                mr_target="main",
                gitlab_base_url="https://gitlab.example.com",
                workspace_root=".symphonz/workspace",
                logs_root=".symphonz/logs",
            )
            write_config(root / ".symphonz" / "config.toml", config)

            command, env = build_run_command(root)

            self.assertEqual(command, [".symphonz/bin/symphony", ".symphonz/WORKFLOW.md", "--logs-root", ".symphonz/logs"])
            self.assertEqual(env["SYMPHONZ_REPO_URL"], "https://example.com/group/repo.git")
            self.assertEqual(env["SYMPHONZ_BASE_BRANCH"], "main")
            self.assertEqual(env["SYMPHONZ_MR_TARGET"], "main")
            self.assertEqual(env["SYMPHONZ_GIT_PROVIDER"], "gitlab")
            self.assertEqual(env["GITLAB_BASE_URL"], "https://gitlab.example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_symphonz_cli.RuntimeTests -v`

Expected: FAIL with import error for `symphonz.runtime`.

- [ ] **Step 3: Implement runtime setup and command construction**

Create `symphonz/runtime.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_symphonz_cli.RuntimeTests -v`

Expected: PASS.

- [ ] **Step 5: Run the full Python unit suite**

Run: `python3 -m unittest discover -v`

Expected: PASS for all `tests/test_symphonz_cli.py` tests.

- [ ] **Step 6: Commit**

```bash
git add symphonz/runtime.py tests/test_symphonz_cli.py
git commit -m "feat(cli): add symphonz runtime launcher"
```

---

### Task 5: End-to-End CLI Smoke Tests and Usage Docs

**Files:**
- Modify: `tests/test_symphonz_cli.py`
- Modify: `symphony_readme.md`
- Modify: `bin/symphonz`

**Interfaces:**
- Consumes: `symphonz.cli.main`
- Produces: documented commands for local development and manual use

- [ ] **Step 1: Write the CLI smoke tests**

Append these tests:

```python
from symphonz.cli import main


class CliSmokeTests(unittest.TestCase):
    def test_main_rejects_missing_command(self):
        with self.assertRaises(SystemExit) as raised:
            main([])
        self.assertEqual(raised.exception.code, 2)

    def test_install_global_with_answers_creates_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/group/repo.git"], cwd=root, check=True)
            old_cwd = Path.cwd()
            answers = iter(["LINEAR_API_KEY", "project-slug", "", "", "", "", ""])

            try:
                os.chdir(root)
                from unittest.mock import patch

                with patch("builtins.input", lambda prompt: next(answers)):
                    exit_code = main(["install", "--runtime", "global"])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / ".symphonz" / "config.toml").exists())
            self.assertTrue((root / ".symphonz" / "WORKFLOW.md").exists())
```

- [ ] **Step 2: Run tests to verify the first pass result**

Run: `python3 -m unittest tests.test_symphonz_cli.CliSmokeTests -v`

Expected: PASS. If `NameError: name 'os' is not defined` appears, add `import os` at the top of `tests/test_symphonz_cli.py` and rerun the command.

- [ ] **Step 3: Make `bin/symphonz` executable**

Run:

```bash
chmod +x bin/symphonz
```

Expected: `test -x bin/symphonz` returns exit code 0.

- [ ] **Step 4: Document local usage**

Append this section to `symphony_readme.md`:

```markdown

## symphonz CLI

This repository now owns the `symphonz` installer/launcher for project-local Symphony orchestration.

Local development:

```bash
python3 -m unittest discover -v
./bin/symphonz install --runtime global
./bin/symphonz run --print-command
```

Use embedded runtime mode for a self-contained project install:

```bash
./bin/symphonz install
```

Use global runtime mode when `symphony` is already available on the machine:

```bash
./bin/symphonz install --runtime global
```
```

- [ ] **Step 5: Run full verification**

Run:

```bash
python3 -m unittest discover -v
ruby -ryaml -e 'content = File.read("WORKFLOW.md"); parts = content.split(/^---\s*$/); config = YAML.safe_load(parts[1], aliases: true); raise "bad root" unless config.dig("workspace", "root") == ".symphonz/workspace"; raise "Done not active" unless config.dig("tracker", "active_states").include?("Done"); raise "Done terminal" if config.dig("tracker", "terminal_states").include?("Done"); puts "workflow_contract=ok"'
ruby -e 's = File.read("WORKFLOW.md"); abort "var mismatch" unless s.scan(/\{\{/).size == s.scan(/\}\}/).size; abort "tag mismatch" unless s.scan(/\{%/).size == s.scan(/%\}/).size; puts "template_delimiters=ok"'
```

Expected:

```text
OK
workflow_contract=ok
template_delimiters=ok
```

- [ ] **Step 6: Commit**

```bash
git add bin/symphonz symphonz tests/test_symphonz_cli.py symphony_readme.md
git commit -m "docs(cli): document symphonz usage"
```

---

## Self-Review

**Spec coverage:** The plan covers `symphonz install`, `symphonz run`, embedded/global runtime mode, `.symphonz` layout, config generation, workflow rendering from root `WORKFLOW.md`, workspace/logs directories, Git defaults, GitLab base URL, environment exports, and skipped secret persistence.

**Placeholder scan:** The plan intentionally avoids deferred implementation language. Every file path, function name, command, and expected result needed for the first implementation is specified.

**Type consistency:** `InstallConfig` is produced in Task 1 and consumed by Tasks 2-4 with the same fields. `read_config` returns TOML sections used by `runtime.build_run_command`. `install_project` parameters match the `cli.main` call site.
