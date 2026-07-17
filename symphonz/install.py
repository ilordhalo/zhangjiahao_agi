from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import base64
import binascii
import getpass
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

from symphonz.service.auth import DashboardAuth, PasswordRecord, hash_password, validate_password_record


DEFAULT_GITLAB_BASE_URL = "https://gitlab.example.com"
DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 4000
DEFAULT_DASHBOARD_USERNAME = "admin"
DEFAULT_DASHBOARD_SESSION_DAYS = 30
SUPPORTED_GIT_PROVIDERS = {"github", "gitlab"}
_BARE_TOML_KEY = r"[A-Za-z0-9_-]+"
_BASIC_TOML_KEY = r'"(?:\\.|[^"\\\r\n])*"'
_LITERAL_TOML_KEY = r"'[^'\r\n]*'"
_TOML_KEY_PART = rf"(?:{_BARE_TOML_KEY}|{_BASIC_TOML_KEY}|{_LITERAL_TOML_KEY})"
_TOML_KEY_PATH = rf"{_TOML_KEY_PART}(?:[ \t]*\.[ \t]*{_TOML_KEY_PART})*"
_TOML_KEY_PART_PATTERN = re.compile(_TOML_KEY_PART)
_TABLE_HEADER = re.compile(
    rf"^[ \t]*\[[ \t]*(?P<name>{_TOML_KEY_PATH})"
    r"[ \t]*\][ \t]*(?:#.*)?$"
)
_ARRAY_TABLE_HEADER = re.compile(
    rf"^[ \t]*\[\[[ \t]*(?P<name>{_TOML_KEY_PATH})"
    r"[ \t]*\]\][ \t]*(?:#.*)?$"
)
_GENERATED_TOML_STRING = re.compile(r'^"(?:\\["\\]|[^"\\\r\n])*"$')
_LEGACY_CONFIG_LAYOUT = (
    "[runtime]",
    "mode",
    "command",
    "",
    "[linear]",
    "api_key_env",
    "project_slug",
    "",
    "[git]",
    "provider",
    "remote",
    "base_branch",
    "mr_target",
    "gitlab_base_url",
    "",
    "[workspace]",
    "root",
    "",
    "[logs]",
    "root",
)


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
    dashboard_host: str = DEFAULT_DASHBOARD_HOST
    dashboard_port: int = DEFAULT_DASHBOARD_PORT
    dashboard_public_base_url: str = "http://127.0.0.1:4000"
    dashboard_username: str = DEFAULT_DASHBOARD_USERNAME
    dashboard_session_days: int = DEFAULT_DASHBOARD_SESSION_DAYS


def toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_config(path: Path, config: InstallConfig) -> None:
    _atomic_write_text(path, _render_config(config))


def _render_config(config: InstallConfig) -> str:
    return "\n".join(
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
            "[dashboard]",
            f"host = {toml_quote(config.dashboard_host)}",
            f"port = {toml_quote(str(config.dashboard_port))}",
            f"public_base_url = {toml_quote(config.dashboard_public_base_url)}",
            f"username = {toml_quote(config.dashboard_username)}",
            f"session_days = {toml_quote(str(config.dashboard_session_days))}",
            "",
        ]
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_path: Path | None = None
    primary_error: BaseException | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        for _unused in range(100):
            candidate = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
            try:
                descriptor = os.open(candidate, flags, 0o666)
            except FileExistsError:
                continue
            temporary_path = candidate
            break
        else:
            raise RuntimeError(f"Unable to create an exclusive temporary file for {path}")

        output = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException as error:
        primary_error = error

    if descriptor >= 0:
        os.close(descriptor)
    cleanup_error = None
    if temporary_path is not None:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except BaseException as error:
            cleanup_error = error
    if primary_error is not None:
        if cleanup_error is not None:
            raise RuntimeError(
                f"{primary_error}; temporary file cleanup also failed: {cleanup_error}"
            ) from primary_error
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error


def read_config(path: Path) -> dict[str, dict[str, str]]:
    return _parse_config(path.read_text())


def read_installed_config(path: Path) -> tuple[dict[str, dict[str, str]], str]:
    content = path.read_text()
    return _parse_config(content), classify_dashboard_config(content)


def _parse_config(content: str) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    current_section: str | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section_name = _table_header_name(raw_line)
        if section_name is not None:
            current_section = section_name
            parsed[current_section] = {}
            continue
        if current_section is None or "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        parsed[current_section][key.strip()] = parse_toml_string(raw_value.strip())

    return parsed


def _table_header_name(line: str) -> str | None:
    header = _table_header_path(line)
    if header is None or header[0]:
        return None
    return ".".join(header[1])


def _table_header_path(line: str) -> tuple[bool, tuple[str, ...]] | None:
    candidate = line.rstrip("\r\n")
    match = _TABLE_HEADER.fullmatch(candidate)
    is_array = False
    if match is None:
        match = _ARRAY_TABLE_HEADER.fullmatch(candidate)
        is_array = True
    if match is None:
        return None

    parts = []
    for key_match in _TOML_KEY_PART_PATTERN.finditer(match.group("name")):
        value = key_match.group(0)
        if value.startswith('"'):
            decoded = _decode_basic_toml_key(value)
            if decoded is None:
                return None
            value = decoded
        elif value.startswith("'"):
            value = value[1:-1]
        parts.append(value)
    return is_array, tuple(parts)


def _decode_basic_toml_key(value: str) -> str | None:
    escapes = {
        '"': '"',
        "\\": "\\",
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "f": "\f",
        "r": "\r",
    }
    body = value[1:-1]
    result = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(body):
            return None
        escape = body[index + 1]
        if escape in escapes:
            result.append(escapes[escape])
            index += 2
            continue
        if escape not in {"u", "U"}:
            return None
        width = 4 if escape == "u" else 8
        digits = body[index + 2 : index + 2 + width]
        if len(digits) != width or re.fullmatch(r"[0-9A-Fa-f]+", digits) is None:
            return None
        codepoint = int(digits, 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return None
        result.append(chr(codepoint))
        index += 2 + width
    return "".join(result)


def classify_dashboard_config(content: str) -> str:
    dashboard_headers = []
    for line in content.splitlines():
        header = _table_header_path(line)
        if header is not None and header[1] and header[1][0] == "dashboard":
            dashboard_headers.append(header)

    direct_tables = [header for header in dashboard_headers if not header[0] and header[1] == ("dashboard",)]
    if len(direct_tables) > 1 and len(direct_tables) == len(dashboard_headers):
        raise RuntimeError(
            "Configuration contains multiple [dashboard] sections; resolve the duplicate definitions, "
            "then run `symphonz configure-dashboard`"
        )
    if dashboard_headers:
        if len(dashboard_headers) == 1 and direct_tables:
            return "configured"
        raise RuntimeError(
            "Dashboard configuration uses a noncanonical dashboard-related table; "
            "run `symphonz configure-dashboard` after resolving it"
        )
    if _is_generated_legacy_config(content):
        return "legacy"
    raise RuntimeError(
        "Dashboard configuration is missing or ambiguous; run `symphonz configure-dashboard` to regenerate it"
    )


def _is_generated_legacy_config(content: str) -> bool:
    if not content.endswith("\n"):
        return False
    lines = content.splitlines()
    if len(lines) != len(_LEGACY_CONFIG_LAYOUT):
        return False
    for line, expected in zip(lines, _LEGACY_CONFIG_LAYOUT):
        if expected == "" or expected.startswith("["):
            if line != expected:
                return False
            continue
        prefix = f"{expected} = "
        if not line.startswith(prefix) or _GENERATED_TOML_STRING.fullmatch(line[len(prefix) :]) is None:
            return False
    return True


def _is_table_header(line: str) -> bool:
    candidate = line.rstrip("\r\n")
    return _TABLE_HEADER.fullmatch(candidate) is not None or _ARRAY_TABLE_HEADER.fullmatch(candidate) is not None


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
    primary_error: BaseException | None = None
    result: DashboardAuth | None = None
    try:
        _validate_auth_destination(path, directory_descriptor)
        previous = _snapshot_auth_destination(path.name, directory_descriptor)
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
        try:
            os.fsync(directory_descriptor)
        except BaseException as commit_error:
            try:
                _restore_auth_destination(path.name, directory_descriptor, previous)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"{commit_error}; auth rollback also failed: {rollback_error}"
                ) from commit_error
            raise
        result = DashboardAuth(password_record=record, session_secret=session_secret)
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    if descriptor >= 0:
        os.close(descriptor)
    if temporary_name is not None:
        cleanup_error = _cleanup_auth_temp(temporary_name, directory_descriptor)
    os.close(directory_descriptor)

    if primary_error is not None:
        if cleanup_error is not None:
            raise RuntimeError(
                f"{primary_error}; temporary auth cleanup also failed: {cleanup_error}"
            ) from primary_error
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error
    assert result is not None
    return result


def _snapshot_auth_destination(
    filename: str,
    directory_descriptor: int,
) -> tuple[bytes, int] | None:
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Dashboard auth configuration destination must be a regular file")
        auth_file = os.fdopen(descriptor, "rb")
        descriptor = -1
        with auth_file:
            return auth_file.read(), stat.S_IMODE(metadata.st_mode)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _restore_auth_destination(
    filename: str,
    directory_descriptor: int,
    previous: tuple[bytes, int] | None,
) -> None:
    if previous is None:
        os.unlink(filename, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
        return

    content, mode = previous
    descriptor = -1
    temporary_name: str | None = None
    primary_error: BaseException | None = None
    try:
        descriptor, temporary_name = _create_auth_temp(filename, directory_descriptor)
        os.fchmod(descriptor, mode)
        auth_file = os.fdopen(descriptor, "wb")
        descriptor = -1
        with auth_file:
            auth_file.write(content)
            auth_file.flush()
            os.fsync(auth_file.fileno())
        _validate_auth_destination(Path(filename), directory_descriptor)
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_name = None
        os.fsync(directory_descriptor)
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    if descriptor >= 0:
        os.close(descriptor)
    if temporary_name is not None:
        cleanup_error = _cleanup_auth_temp(temporary_name, directory_descriptor)
    if primary_error is not None:
        if cleanup_error is not None:
            raise RuntimeError(
                f"{primary_error}; auth rollback temporary cleanup also failed: {cleanup_error}"
            ) from primary_error
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error


def _cleanup_auth_temp(temporary_name: str, directory_descriptor: int) -> BaseException | None:
    try:
        os.unlink(temporary_name, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return None
    except BaseException as error:
        return error
    return None


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


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} must be a positive integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} must be a positive integer") from error
    if parsed <= 0:
        raise RuntimeError(f"{label} must be a positive integer")
    return parsed


def _dashboard_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


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
    dashboard_host: str | None = None,
    dashboard_port: int | None = None,
    dashboard_public_base_url: str | None = None,
    dashboard_username: str | None = None,
    dashboard_session_days: int | None = None,
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
        "dashboard_host": dashboard_host
        if dashboard_host is not None
        else environ.get("SYMPHONZ_DASHBOARD_HOST", ""),
        "dashboard_port": dashboard_port
        if dashboard_port is not None
        else environ.get("SYMPHONZ_DASHBOARD_PORT", ""),
        "dashboard_public_base_url": dashboard_public_base_url
        if dashboard_public_base_url is not None
        else environ.get("SYMPHONZ_DASHBOARD_PUBLIC_BASE_URL", ""),
        "dashboard_username": dashboard_username
        if dashboard_username is not None
        else environ.get("SYMPHONZ_DASHBOARD_USERNAME", ""),
        "dashboard_session_days": dashboard_session_days
        if dashboard_session_days is not None
        else environ.get("SYMPHONZ_DASHBOARD_SESSION_DAYS", ""),
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
        dashboard_host = str(supplied["dashboard_host"] or DEFAULT_DASHBOARD_HOST)
        dashboard_port = _positive_integer(
            DEFAULT_DASHBOARD_PORT
            if supplied["dashboard_port"] == ""
            else supplied["dashboard_port"],
            "Dashboard port",
        )
        dashboard_public_base_url = str(
            supplied["dashboard_public_base_url"] or _dashboard_url(dashboard_port)
        )
        dashboard_username = str(supplied["dashboard_username"] or DEFAULT_DASHBOARD_USERNAME)
        dashboard_session_days = _positive_integer(
            DEFAULT_DASHBOARD_SESSION_DAYS
            if supplied["dashboard_session_days"] == ""
            else supplied["dashboard_session_days"],
            "Dashboard session days",
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
        dashboard_host = str(supplied["dashboard_host"] or prompt_value(
            input_func, "Dashboard host", DEFAULT_DASHBOARD_HOST
        ))
        dashboard_port = _positive_integer(
            prompt_value(input_func, "Dashboard port", str(DEFAULT_DASHBOARD_PORT))
            if supplied["dashboard_port"] == ""
            else supplied["dashboard_port"],
            "Dashboard port",
        )
        dashboard_public_base_url = str(
            supplied["dashboard_public_base_url"]
            or prompt_value(input_func, "Dashboard LAN/public base URL", _dashboard_url(dashboard_port))
        )
        dashboard_username = str(supplied["dashboard_username"] or prompt_value(
            input_func, "Dashboard username", DEFAULT_DASHBOARD_USERNAME
        ))
        dashboard_session_days = _positive_integer(
            prompt_value(
                input_func,
                "Dashboard session days",
                str(DEFAULT_DASHBOARD_SESSION_DAYS),
            )
            if supplied["dashboard_session_days"] == ""
            else supplied["dashboard_session_days"],
            "Dashboard session days",
        )

    if not linear_project_slug:
        if assume_yes:
            raise RuntimeError(
                "Linear project slug or ID is required; pass --linear-project or SYMPHONZ_LINEAR_PROJECT"
            )
        raise RuntimeError("Linear project slug or ID is required")
    if not repo_url:
        raise RuntimeError("Git remote URL is required")
    if not dashboard_host.strip():
        raise RuntimeError("Dashboard host is required")
    if not dashboard_public_base_url.strip():
        raise RuntimeError("Dashboard LAN/public base URL is required")
    if not dashboard_username.strip():
        raise RuntimeError("Dashboard username is required")

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
        dashboard_host=dashboard_host,
        dashboard_port=dashboard_port,
        dashboard_public_base_url=dashboard_public_base_url,
        dashboard_username=dashboard_username,
        dashboard_session_days=dashboard_session_days,
    )


def _render_gitignore(project_root: Path) -> str:
    gitignore = project_root / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    additions = [
        ".symphonz/artifacts/",
        ".symphonz/logs/",
        ".symphonz/workspace/",
        ".symphonz/auth.toml",
    ]
    updated = [line for line in existing if line not in additions]
    updated.extend(additions)
    return "\n".join(updated).rstrip() + "\n"


def ensure_gitignore(project_root: Path) -> None:
    _atomic_write_text(project_root / ".gitignore", _render_gitignore(project_root))


def create_base_layout(project_root: Path) -> None:
    for relative in [
        ".symphonz",
        ".symphonz/artifacts",
        ".symphonz/logs",
        ".symphonz/workspace",
    ]:
        (project_root / relative).mkdir(parents=True, exist_ok=True)


def _dashboard_password(
    *,
    password: str | None,
    environ: dict[str, str],
    getpass_func: Callable[[str], str] | None,
    environment_required: bool,
) -> str:
    if password is not None:
        resolved = password
    elif environment_required:
        resolved = environ.get("SYMPHONZ_DASHBOARD_PASSWORD", "")
        if not resolved:
            raise RuntimeError(
                "Dashboard password is required; set SYMPHONZ_DASHBOARD_PASSWORD for non-interactive installation"
            )
    else:
        resolved = environ.get("SYMPHONZ_DASHBOARD_PASSWORD", "")
        if not resolved:
            resolved = (getpass_func or getpass.getpass)("Dashboard password: ")
    if not resolved:
        raise RuntimeError("dashboard password is required")
    return resolved


def _render_dashboard_section(
    host: str,
    port: int,
    public_base_url: str,
    username: str,
    session_days: int,
) -> str:
    return "\n".join(
        [
            "[dashboard]",
            f"host = {toml_quote(host)}",
            f"port = {toml_quote(str(port))}",
            f"public_base_url = {toml_quote(public_base_url)}",
            f"username = {toml_quote(username)}",
            f"session_days = {toml_quote(str(session_days))}",
            "",
            "",
        ]
    )


def _replace_dashboard_section(content: str, section: str) -> str:
    lines = content.splitlines(keepends=True)
    bounds = _dashboard_section_bounds(lines)
    if bounds is None:
        if not content:
            return section.rstrip("\n") + "\n"
        separator = "" if content.endswith("\n\n") else "\n" if content.endswith("\n") else "\n\n"
        return content + separator + section.rstrip("\n") + "\n"

    start, end = bounds
    replacement = section if end < len(lines) else section.rstrip("\n") + "\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


def _dashboard_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    starts = [index for index, line in enumerate(lines) if _table_header_name(line) == "dashboard"]
    if len(starts) > 1:
        raise RuntimeError(
            "Configuration contains multiple [dashboard] sections; resolve the duplicate definitions first"
        )
    if not starts:
        return None

    start = starts[0]
    end = next(
        (index for index in range(start + 1, len(lines)) if _is_table_header(lines[index])),
        len(lines),
    )
    return start, end


def configure_dashboard(
    project_root: Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    public_base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    session_days: int | None = None,
    environ: dict[str, str] | None = None,
    getpass_func: Callable[[str], str] | None = None,
) -> Path:
    root = project_root or Path.cwd()
    config_path = root / ".symphonz" / "config.toml"
    if not config_path.is_file():
        raise RuntimeError(f"Symphonz configuration does not exist at {config_path}; run `symphonz install` first")

    environ = os.environ if environ is None else environ
    content = config_path.read_text()
    classify_dashboard_config(content)
    _dashboard_section_bounds(content.splitlines(keepends=True))
    current = _parse_config(content).get("dashboard", {})
    resolved_host = (host if host is not None else current.get("host", DEFAULT_DASHBOARD_HOST)).strip()
    resolved_port = _positive_integer(
        port if port is not None else current.get("port", DEFAULT_DASHBOARD_PORT),
        "Dashboard port",
    )
    resolved_public_base_url = (
        public_base_url
        if public_base_url is not None
        else current.get("public_base_url", _dashboard_url(resolved_port))
    ).strip()
    resolved_username = (
        username if username is not None else current.get("username", DEFAULT_DASHBOARD_USERNAME)
    ).strip()
    resolved_session_days = _positive_integer(
        session_days
        if session_days is not None
        else current.get("session_days", DEFAULT_DASHBOARD_SESSION_DAYS),
        "Dashboard session days",
    )
    if not resolved_host:
        raise RuntimeError("Dashboard host is required")
    if not resolved_public_base_url:
        raise RuntimeError("Dashboard LAN/public base URL is required")
    if not resolved_username:
        raise RuntimeError("Dashboard username is required")
    resolved_password = _dashboard_password(
        password=password,
        environ=environ,
        getpass_func=getpass_func,
        environment_required=False,
    )

    section = _render_dashboard_section(
        resolved_host,
        resolved_port,
        resolved_public_base_url,
        resolved_username,
        resolved_session_days,
    )
    updated_config = _replace_dashboard_section(content, section)
    updated_gitignore = _render_gitignore(root)
    _atomic_write_text(root / ".gitignore", updated_gitignore)
    try:
        _atomic_write_text(config_path, updated_config)
    except BaseException as config_error:
        try:
            _atomic_write_text(config_path, content)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"Dashboard config update failed and rollback failed: {rollback_error}"
            ) from config_error
        raise
    try:
        write_auth_config(root / ".symphonz" / "auth.toml", resolved_password)
    except BaseException as auth_error:
        try:
            _atomic_write_text(config_path, content)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"Dashboard auth update failed and config rollback failed: {rollback_error}"
            ) from auth_error
        raise
    return root / ".symphonz"


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
    getpass_func: Callable[[str], str] | None = None,
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
    password = _dashboard_password(
        password=None,
        environ=(os.environ if environ is None else environ) if assume_yes else {},
        getpass_func=getpass_func,
        environment_required=assume_yes,
    )
    if not skip_linear_preflight:
        (output_func or print)(linear_preflight(config, environ=environ))
    from symphonz.workflow import render_workflow, template_path

    config_content = _render_config(config)
    workflow_content = render_workflow(template_path().read_text(), config)
    gitignore_content = _render_gitignore(root)
    create_base_layout(root)
    _atomic_write_text(root / ".gitignore", gitignore_content)
    _atomic_write_text(root / ".symphonz" / "config.toml", config_content)
    _atomic_write_text(root / ".symphonz" / "WORKFLOW.md", workflow_content)
    write_auth_config(root / ".symphonz" / "auth.toml", password)

    return root / ".symphonz"
