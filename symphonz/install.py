from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess


DEFAULT_GITLAB_BASE_URL = "https://gitlab.example.com"
SUPPORTED_GIT_PROVIDERS = {"github", "gitlab"}


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
    parsed: dict[str, dict[str, str]] = {}
    current_section: str | None = None

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            parsed[current_section] = {}
            continue
        if current_section is None or "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        parsed[current_section][key.strip()] = parse_toml_string(raw_value.strip())

    return parsed


def parse_toml_string(value: str) -> str:
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        return value

    body = value[1:-1]
    result = []
    escaped = False
    for char in body:
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)

    if escaped:
        result.append("\\")

    return "".join(result)


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


def detect_git_provider(repo_url: str) -> str:
    normalized = repo_url.lower()
    if "github.com" in normalized:
        return "github"
    if "gitlab" in normalized:
        return "gitlab"
    return "gitlab"


def normalize_git_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_GIT_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_GIT_PROVIDERS))
        raise RuntimeError(f"Git provider must be one of: {allowed}")
    return normalized


def detect_git_defaults(project_root: Path) -> dict[str, str]:
    require_git_repo(project_root)
    repo_url = run_git(project_root, ["remote", "get-url", "origin"])
    base_branch = run_git(project_root, ["branch", "--show-current"]) or "main"
    return {
        "repo_url": repo_url,
        "base_branch": base_branch,
        "mr_target": base_branch,
        "git_provider": detect_git_provider(repo_url),
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
    input_func: Callable[[str], str] | None = None,
) -> InstallConfig:
    input_func = input_func or input
    defaults = detect_git_defaults(project_root)

    if assume_yes:
        linear_api_key_env = "LINEAR_API_KEY"
        linear_project_slug = ""
        git_provider = normalize_git_provider(defaults["git_provider"])
        repo_url = defaults["repo_url"]
        base_branch = defaults["base_branch"]
        mr_target = defaults["mr_target"]
        gitlab_base_url = defaults["gitlab_base_url"] if git_provider == "gitlab" else ""
    else:
        linear_api_key_env = prompt_value(input_func, "Linear API key environment variable", "LINEAR_API_KEY")
        linear_project_slug = prompt_value(input_func, "Linear project slug or ID", "")
        git_provider = normalize_git_provider(
            prompt_value(input_func, "Git provider (github/gitlab)", defaults["git_provider"])
        )
        repo_url = prompt_value(input_func, "Git remote URL", defaults["repo_url"])
        base_branch = prompt_value(input_func, "Base branch", defaults["base_branch"])
        mr_target = prompt_value(input_func, "Merge request target branch", defaults["mr_target"])
        gitlab_base_url = (
            prompt_value(input_func, "GitLab base URL", defaults["gitlab_base_url"])
            if git_provider == "gitlab"
            else ""
        )

    if not linear_project_slug:
        raise RuntimeError("Linear project slug or ID is required")
    if not repo_url:
        raise RuntimeError("Git remote URL is required")

    runtime_command = "symphonz-internal" if runtime_mode == "embedded" else "symphony"
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


def ensure_gitignore(project_root: Path) -> None:
    gitignore = project_root / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    additions = [".symphonz/workspace/", ".symphonz/logs/"]
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
    input_func: Callable[[str], str] | None = None,
) -> Path:
    root = project_root or Path.cwd()
    config = collect_install_config(root, runtime_mode, assume_yes, input_func=input_func)
    create_base_layout(root)
    write_config(root / ".symphonz" / "config.toml", config)

    from symphonz.workflow import write_workflow

    write_workflow(root, config)
    ensure_gitignore(root)

    return root / ".symphonz"
