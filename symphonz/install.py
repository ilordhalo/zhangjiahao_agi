from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import base64
import binascii
import os
from pathlib import Path
import secrets
import stat
import subprocess

from symphonz.service.auth import DashboardAuth, PasswordRecord, hash_password, validate_password_record


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
    return _parse_config(path.read_text())


def _parse_config(content: str) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    current_section: str | None = None

    for raw_line in content.splitlines():
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


def write_auth_config(path: Path, password: str) -> DashboardAuth:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    directory_descriptor = _open_auth_parent(path)
    descriptor = -1
    temporary_name: str | None = None
    try:
        _validate_auth_destination(path, directory_descriptor)
        record = hash_password(password)
        session_secret = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        content = "\n".join(
            [
                "[auth]",
                f"algorithm = {toml_quote(record.algorithm)}",
                f"salt = {toml_quote(record.salt)}",
                f"password_hash = {toml_quote(record.password_hash)}",
                f"session_secret = {toml_quote(session_secret)}",
                "",
            ]
        )
        descriptor, temporary_name = _create_auth_temp(path.name, directory_descriptor)
        os.fchmod(descriptor, 0o600)
        auth_file = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with auth_file:
            auth_file.write(content)
            auth_file.flush()
            os.fsync(auth_file.fileno())
        _validate_auth_destination(path, directory_descriptor)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_name = None
        os.fsync(directory_descriptor)
        return DashboardAuth(password_record=record, session_secret=session_secret)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)


def read_dashboard_auth(project_root: Path) -> DashboardAuth:
    auth_path = project_root / ".symphonz" / "auth.toml"
    content = _read_private_auth_file(auth_path)
    auth_values = _parse_config(content).get("auth", {})
    required = ("algorithm", "salt", "password_hash", "session_secret")
    if any(not auth_values.get(field) for field in required):
        raise _auth_config_error(auth_path, "required auth fields are missing")
    record = PasswordRecord(
        algorithm=auth_values["algorithm"],
        salt=auth_values["salt"],
        password_hash=auth_values["password_hash"],
    )
    try:
        validate_password_record(record)
        _decode_auth_value(auth_values["session_secret"], "session_secret", 32)
    except (RuntimeError, ValueError) as error:
        raise _auth_config_error(auth_path, str(error)) from error
    return DashboardAuth(password_record=record, session_secret=auth_values["session_secret"])


def _open_auth_parent(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError as error:
        raise RuntimeError(
            f"Dashboard auth parent directory must be a directory and not a symbolic link: {path.parent}"
        ) from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError(
            f"Dashboard auth parent directory must be a directory and not a symbolic link: {path.parent}"
        )
    return descriptor


def _validate_auth_destination(path: Path, directory_descriptor: int) -> None:
    try:
        metadata = os.stat(path.name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Dashboard auth configuration destination must not be a symbolic link: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Dashboard auth configuration destination must be a regular file: {path}")


def _create_auth_temp(filename: str, directory_descriptor: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _unused in range(100):
        temporary_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        try:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_descriptor)
            return descriptor, temporary_name
        except FileExistsError:
            continue
    raise RuntimeError("Unable to create an exclusive dashboard auth temporary file")


def _read_private_auth_file(path: Path) -> str:
    try:
        directory_descriptor = _open_auth_parent(path)
    except RuntimeError as error:
        raise _auth_config_error(path, str(error)) from error
    try:
        _validate_auth_destination(path, directory_descriptor)
    except FileNotFoundError as error:
        os.close(directory_descriptor)
        raise _auth_config_error(path, "file does not exist") from error
    except RuntimeError as error:
        os.close(directory_descriptor)
        raise _auth_config_error(path, str(error)) from error

    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise _auth_config_error(path, "file must be a regular file")
        if stat.S_IMODE(opened_metadata.st_mode) != 0o600:
            raise _auth_config_error(path, "file permissions must be exactly 0600")
        auth_file = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = -1
        with auth_file:
            return auth_file.read()
    except (OSError, UnicodeError) as error:
        raise _auth_config_error(path, f"file cannot be opened safely: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_descriptor)


def _decode_auth_value(value: str, field: str, expected_bytes: int) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ValueError(f"{field} must be valid Base64") from error
    if len(decoded) != expected_bytes:
        raise ValueError(f"{field} must decode to exactly {expected_bytes} bytes")
    return decoded


def _auth_config_error(path: Path, reason: str) -> RuntimeError:
    return RuntimeError(
        f"Dashboard auth configuration is invalid at {path}: {reason}. "
        "Run `symphonz configure-dashboard` to regenerate it."
    )


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
    assume_yes: bool,
    input_func: Callable[[str], str] | None = None,
    *,
    linear_project_slug: str | None = None,
    linear_api_key_env: str | None = None,
    git_provider: str | None = None,
    repo_url: str | None = None,
    base_branch: str | None = None,
    mr_target: str | None = None,
    gitlab_base_url: str | None = None,
    environ: dict[str, str] | None = None,
) -> InstallConfig:
    input_func = input_func or input
    environ = os.environ if environ is None else environ
    defaults = detect_git_defaults(project_root)

    supplied = {
        "linear_api_key_env": linear_api_key_env or environ.get("SYMPHONZ_LINEAR_API_KEY_ENV", ""),
        "linear_project_slug": linear_project_slug or environ.get("SYMPHONZ_LINEAR_PROJECT", ""),
        "git_provider": git_provider or environ.get("SYMPHONZ_GIT_PROVIDER", ""),
        "repo_url": repo_url or environ.get("SYMPHONZ_REPO_URL", ""),
        "base_branch": base_branch or environ.get("SYMPHONZ_BASE_BRANCH", ""),
        "mr_target": mr_target or environ.get("SYMPHONZ_TARGET_BRANCH", ""),
        "gitlab_base_url": gitlab_base_url or environ.get("SYMPHONZ_GITLAB_BASE_URL", ""),
    }

    if assume_yes:
        linear_api_key_env = supplied["linear_api_key_env"] or "LINEAR_API_KEY"
        linear_project_slug = supplied["linear_project_slug"]
        git_provider = normalize_git_provider(supplied["git_provider"] or defaults["git_provider"])
        repo_url = supplied["repo_url"] or defaults["repo_url"]
        base_branch = supplied["base_branch"] or defaults["base_branch"]
        mr_target = supplied["mr_target"] or base_branch
        gitlab_base_url = (
            supplied["gitlab_base_url"] or defaults["gitlab_base_url"]
            if git_provider == "gitlab"
            else ""
        )
    else:
        linear_api_key_env = supplied["linear_api_key_env"] or prompt_value(
            input_func, "Linear API key environment variable", "LINEAR_API_KEY"
        )
        linear_project_slug = supplied["linear_project_slug"] or prompt_value(
            input_func, "Linear project slug or ID", ""
        )
        git_provider = normalize_git_provider(
            supplied["git_provider"]
            or prompt_value(input_func, "Git provider (github/gitlab)", defaults["git_provider"])
        )
        repo_url = supplied["repo_url"] or prompt_value(input_func, "Git remote URL", defaults["repo_url"])
        base_branch = supplied["base_branch"] or prompt_value(input_func, "Base branch", defaults["base_branch"])
        mr_target = supplied["mr_target"] or prompt_value(
            input_func, "Merge request target branch", defaults["mr_target"]
        )
        gitlab_base_url = (
            supplied["gitlab_base_url"]
            or prompt_value(input_func, "GitLab base URL", defaults["gitlab_base_url"])
            if git_provider == "gitlab"
            else ""
        )

    if not linear_project_slug:
        if assume_yes:
            raise RuntimeError(
                "Linear project slug or ID is required; pass --linear-project or SYMPHONZ_LINEAR_PROJECT"
            )
        raise RuntimeError("Linear project slug or ID is required")
    if not repo_url:
        raise RuntimeError("Git remote URL is required")

    return InstallConfig(
        runtime_mode="embedded",
        runtime_command="symphonz-internal",
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
    additions = [".symphonz/workspace/", ".symphonz/logs/", ".symphonz/auth.toml"]
    updated = existing[:]
    for item in additions:
        if item not in updated:
            updated.append(item)
    gitignore.write_text("\n".join(updated).rstrip() + "\n")


def create_base_layout(project_root: Path) -> None:
    for relative in [".symphonz", ".symphonz/workspace", ".symphonz/logs"]:
        (project_root / relative).mkdir(parents=True, exist_ok=True)


def linear_preflight(config: InstallConfig, environ: dict[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    api_key = environ.get(config.linear_api_key_env, "").strip()
    if not api_key:
        return (
            "Linear preflight skipped because the API key is not set. Before running the service:\n"
            f'  export {config.linear_api_key_env}="<linear-api-key>"'
        )

    from symphonz.service.linear import LINEAR_GRAPHQL_URL, LinearClient

    endpoint = environ.get("SYMPHONZ_LINEAR_ENDPOINT", LINEAR_GRAPHQL_URL)
    client = LinearClient(api_key=api_key, project_slug=config.linear_project_slug, endpoint=endpoint)
    body = client.graphql("query SymphonzInstallPreflight { viewer { id } }")
    if body.get("errors") or not body.get("data", {}).get("viewer", {}).get("id"):
        raise RuntimeError(f"Linear preflight failed: {body.get('errors') or 'viewer was not returned'}")
    return "Linear connection verified."


def install_project(
    project_root: Path | None = None,
    assume_yes: bool = False,
    skip_linear_preflight: bool = False,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
    **config_values: object,
) -> Path:
    root = project_root or Path.cwd()
    environ = config_values.get("environ")
    config = collect_install_config(
        root,
        assume_yes,
        input_func=input_func,
        **config_values,
    )
    if not skip_linear_preflight:
        (output_func or print)(linear_preflight(config, environ=environ))
    create_base_layout(root)
    write_config(root / ".symphonz" / "config.toml", config)

    from symphonz.workflow import write_workflow

    write_workflow(root, config)
    ensure_gitignore(root)

    return root / ".symphonz"
