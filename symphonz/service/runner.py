from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import os
import re
import sys
import time
from urllib.parse import urlsplit

from symphonz.install import read_config, read_dashboard_auth
from symphonz.service.auth import AuthService, DashboardAuth
from symphonz.service.codex_app_server import CodexAppServer
from symphonz.service.attempt_store import AttemptStore
from symphonz.service.dashboard import DashboardServer
from symphonz.service.event_log import CompositeEventSink, ErrorJsonlLog, JsonlEventLog, RuntimeEventRouter
from symphonz.service.linear import LinearClient
from symphonz.service.models import Issue, RuntimeErrorRecord, RuntimeState
from symphonz.service.orchestrator import Orchestrator
from symphonz.service.reporting import PendingReportSynchronizer, ReportPublisher, _public_base_url
from symphonz.service.runtime_store import RuntimeStore
from symphonz.service.workflow import load_workflow


_LOCAL_PUBLIC_BASE_URL = "http://127.0.0.1"
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_IPV4_NUMBER = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)")


@dataclass(frozen=True)
class RuntimeCollaborators:
    store: RuntimeStore
    state: RuntimeState
    artifacts_root: Path
    report_publisher_factory: Callable[[Issue], ReportPublisher]
    report_synchronizer: PendingReportSynchronizer


@dataclass(frozen=True)
class _DashboardConfiguration:
    host: str
    port: int
    public_base_url: str
    secure_cookie: bool

    @property
    def insecure_warning(self) -> bool:
        return not self.secure_cookie


class _ReportStore:
    """Route reporting's direct errors through the runtime error composite."""

    def __init__(self, store: RuntimeStore, error_sink: CompositeEventSink):
        self._store = store
        self._error_sink = error_sink

    def record_error(self, record: RuntimeErrorRecord) -> None:
        self._error_sink.write(record)

    def __getattr__(self, name: str):
        return getattr(self._store, name)


def build_runtime_collaborators(
    logs_root: Path,
    linear_client: LinearClient,
    public_base_url: str | None = None,
    artifacts_root: Path | None = None,
) -> RuntimeCollaborators:
    logs_root = Path(logs_root)
    logs_root.mkdir(parents=True, exist_ok=True)
    store = RuntimeStore(logs_root / "runtime.sqlite3")
    runtime_log = JsonlEventLog(logs_root / "runtime.jsonl")
    error_log = ErrorJsonlLog(logs_root / "errors.jsonl")
    state = RuntimeState(event_sink=RuntimeEventRouter(runtime_log, store, error_log))
    report_error_sink = CompositeEventSink(store.record_error, error_log)
    report_store = _ReportStore(store, report_error_sink)
    if artifacts_root is None:
        if logs_root.name != "logs" or logs_root.parent.name != ".symphonz":
            raise ValueError("artifacts_root is required when logs_root is outside .symphonz/logs")
        artifacts_root = logs_root.parent / "artifacts"
    else:
        artifacts_root = Path(artifacts_root)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    report_base_url = _LOCAL_PUBLIC_BASE_URL if public_base_url is None else public_base_url

    def report_publisher_factory(issue: Issue) -> ReportPublisher:
        return ReportPublisher(
            store=report_store,
            artifact_root=artifacts_root,
            public_base_url=report_base_url,
            linear_client=linear_client,
            active_issue_id=issue.id,
            active_issue_identifier=issue.identifier,
        )

    report_synchronizer = PendingReportSynchronizer(
        store=report_store,
        artifact_root=artifacts_root,
        public_base_url=report_base_url,
        linear_client=linear_client,
    )
    return RuntimeCollaborators(
        store=store,
        state=state,
        artifacts_root=artifacts_root,
        report_publisher_factory=report_publisher_factory,
        report_synchronizer=report_synchronizer,
    )


def run_service(
    project_root: Path,
    workflow_path: Path,
    logs_root: Path,
    port: int | None,
    once: bool = False,
    host: str = "127.0.0.1",
    public_base_url: str | None = None,
    dashboard_username: str = "admin",
    session_days: int = 30,
    _legacy_unauthenticated_dashboard: bool = False,
) -> int:
    dashboard_configuration = None
    dashboard_auth = None
    if _legacy_unauthenticated_dashboard:
        installed_config_path = project_root / ".symphonz" / "config.toml"
        if not installed_config_path.is_file() or "dashboard" in read_config(installed_config_path):
            raise ValueError(
                "Legacy unauthenticated dashboard requires an installed config without [dashboard]"
            )
        if port is None:
            raise ValueError("Legacy unauthenticated dashboard requires an explicit port")
        if not _is_loopback_host(_validate_bind_host(host)):
            raise ValueError("Legacy unauthenticated dashboard must bind to a loopback host")
    if port is not None:
        dashboard_configuration = _validate_dashboard_configuration(
            host=host,
            port=port,
            public_base_url=public_base_url,
            dashboard_username=dashboard_username,
            session_days=session_days,
        )
        if not _legacy_unauthenticated_dashboard:
            dashboard_auth = read_dashboard_auth(project_root)

    workflow = load_workflow(workflow_path)
    linear = build_linear_client(workflow.config)
    report_base_url = (
        dashboard_configuration.public_base_url
        if dashboard_configuration is not None
        else public_base_url
    )
    collaborators = build_runtime_collaborators(
        logs_root,
        linear,
        report_base_url,
        artifacts_root=project_root / ".symphonz" / "artifacts",
    )
    state = collaborators.state
    auth_service = None
    if dashboard_configuration is not None and not _legacy_unauthenticated_dashboard:
        assert dashboard_auth is not None
        auth_service = _build_auth_service(
            collaborators.store,
            dashboard_auth,
            dashboard_username,
            session_days,
            dashboard_configuration.secure_cookie,
        )
    codex_config = workflow.config.get("codex", {})
    codex = CodexAppServer(
        command=codex_config.get("command", "codex app-server"),
        read_timeout_ms=int(codex_config.get("read_timeout_ms", 5000)),
        turn_timeout_ms=int(codex_config.get("turn_timeout_ms", 3_600_000)),
        stall_timeout_ms=int(codex_config.get("stall_timeout_ms", 300_000)),
    )
    orchestrator = Orchestrator(
        project_root,
        workflow,
        linear,
        codex,
        state=state,
        attempt_store=AttemptStore(logs_root / "attempts.sqlite3"),
        runtime_store=collaborators.store,
        report_publisher_factory=collaborators.report_publisher_factory,
        report_synchronizer=collaborators.report_synchronizer,
    )

    dashboard = None
    result = None
    primary_error = None
    try:
        state.add_event("service_started", "Symphonz service started")
        orchestrator.startup_cleanup()
        if dashboard_configuration is not None:
            if _legacy_unauthenticated_dashboard:
                message = (
                    "Legacy dashboard is running unauthenticated on loopback only; "
                    "run `symphonz configure-dashboard` to enable authentication."
                )
                state.add_event(
                    "legacy_dashboard_warning",
                    message,
                    severity="warning",
                    category="dashboard",
                )
                print(f"Warning: {message}", file=sys.stderr)
            dashboard = DashboardServer(
                dashboard_configuration.host,
                dashboard_configuration.port,
                collaborators.store,
                auth_service,
                collaborators.artifacts_root,
                dashboard_configuration.insecure_warning,
            )
            dashboard.start()
            dashboard_url = _dashboard_bind_url(dashboard_configuration.host, dashboard.port)
            state.add_event("dashboard_started", f"Dashboard listening on {dashboard_url}")
            print(f"Symphonz dashboard: {dashboard_url}")
            _warn_on_public_port_mismatch(
                state,
                dashboard.port,
                dashboard_configuration.public_base_url,
            )

        if once:
            orchestrator.poll_once()
            result = 0
        else:
            interval_ms = int(workflow.config.get("polling", {}).get("interval_ms", 5000))
            print("Symphonz service running. Press Ctrl+C to stop.")
            while True:
                orchestrator.tick()
                time.sleep(max(interval_ms, 1000) / 1000)
    except KeyboardInterrupt:
        result = 0
    except BaseException as error:
        primary_error = error

    lifecycle_error = None
    cleanup_steps = [("orchestrator.shutdown", orchestrator.shutdown)]
    if dashboard is not None:
        cleanup_steps.append(("dashboard.stop", dashboard.stop))
    for operation, cleanup in cleanup_steps:
        try:
            cleanup()
        except BaseException as error:
            if lifecycle_error is None:
                lifecycle_error = error
            _persist_lifecycle_event(
                state,
                "service_cleanup_failed",
                f"Service cleanup failed during {operation}: {error}",
                severity="error",
                category="service",
                stage="cleanup",
                operation=operation,
                error_type=type(error).__name__,
                error=str(error),
            )
    stopped_persistence_error = _persist_lifecycle_event(
        state,
        "service_stopped",
        "Symphonz service stopped",
    )
    if lifecycle_error is None:
        lifecycle_error = stopped_persistence_error

    if primary_error is not None:
        raise primary_error.with_traceback(primary_error.__traceback__)
    if lifecycle_error is not None:
        raise lifecycle_error.with_traceback(lifecycle_error.__traceback__)
    assert result is not None
    return result


def _persist_lifecycle_event(
    state: RuntimeState,
    event_type: str,
    message: str,
    **data: object,
) -> BaseException | None:
    try:
        state.add_event(event_type, message, **data)
    except BaseException as error:
        try:
            print(
                f"Symphonz lifecycle event persistence failed for {event_type}: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )
        except BaseException:
            pass
        return error
    return None


def build_linear_client(config: dict) -> LinearClient:
    tracker = config.get("tracker", {})
    api_key_ref = str(tracker.get("api_key") or "$LINEAR_API_KEY")
    api_key = resolve_env_value(api_key_ref)
    project_slug = tracker.get("project_slug")
    endpoint = os.environ.get("SYMPHONZ_LINEAR_ENDPOINT") or tracker.get("endpoint")
    if not api_key:
        raise RuntimeError(f"Linear API key is missing. Export {api_key_ref.lstrip('$')}.")
    if not project_slug:
        raise RuntimeError("Linear project_slug is missing in WORKFLOW.md.")
    if endpoint:
        return LinearClient(api_key=api_key, project_slug=str(project_slug), endpoint=str(endpoint))
    return LinearClient(api_key=api_key, project_slug=str(project_slug))


def resolve_env_value(value: str) -> str:
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def _validate_dashboard_configuration(
    *,
    host: str,
    port: int,
    public_base_url: str | None,
    dashboard_username: str,
    session_days: int,
) -> _DashboardConfiguration:
    validated_host = _validate_bind_host(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Dashboard port must be an integer from 1 to 65535")
    if not isinstance(dashboard_username, str) or not dashboard_username.strip():
        raise ValueError("Dashboard username must not be empty")
    if isinstance(session_days, bool) or not isinstance(session_days, int) or session_days <= 0:
        raise ValueError("Dashboard session duration must be a positive integer")

    bind_is_loopback = _is_loopback_host(validated_host)
    if public_base_url is None:
        if not bind_is_loopback:
            raise ValueError("A usable explicit public_base_url is required for non-loopback dashboard binding")
        normalized_bind_host = validated_host.rstrip(".")
        public_host = (
            "127.0.0.1"
            if normalized_bind_host.casefold() == "localhost"
            else str(ipaddress.IPv4Address(normalized_bind_host))
        )
        validated_public_url = f"http://{public_host}:{port}"
    else:
        validated_public_url = _public_base_url(public_base_url)

    parsed_public_url = urlsplit(validated_public_url)
    public_host = parsed_public_url.hostname or ""
    public_is_loopback = _validate_public_host(public_host)
    if parsed_public_url.port == 0:
        raise ValueError("public_base_url must use a positive port")
    if not bind_is_loopback and public_is_loopback:
        raise ValueError("public_base_url must be reachable from the non-loopback dashboard network")

    return _DashboardConfiguration(
        host=validated_host,
        port=port,
        public_base_url=validated_public_url,
        secure_cookie=parsed_public_url.scheme == "https",
    )


def _validate_bind_host(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Dashboard host must be a non-empty bind host")
    if any(character.isspace() or ord(character) <= 31 or ord(character) == 127 for character in value):
        raise ValueError("Dashboard host must not contain whitespace or control characters")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if ":" in value:
            raise ValueError("Dashboard host must be an IP address or valid hostname")
    else:
        if address.version == 6:
            raise ValueError(
                "Dashboard 0.4 requires an IPv4 bind host; use an IPv4 address or hostname "
                "and configure public_base_url with the IPv6 proxy URL"
            )
        return value

    hostname = value[:-1] if value.endswith(".") else value
    labels = hostname.split(".")
    if (
        not hostname.isascii()
        or len(hostname) > 253
        or not labels
        or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
    ):
        raise ValueError("Dashboard host must be an IP address or valid hostname")
    return value


def _is_loopback_host(host: str) -> bool:
    normalized = host.rstrip(".").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_public_host(host: str) -> bool:
    normalized = host[:-1] if host.endswith(".") else host
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        labels = normalized.split(".")
        numeric_ipv4_alias = (
            1 <= len(labels) <= 4
            and all(_IPV4_NUMBER.fullmatch(label) is not None for label in labels)
        )
        if (
            not normalized
            or not normalized.isascii()
            or len(normalized) > 253
            or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
            or numeric_ipv4_alias
        ):
            raise ValueError("public_base_url must use a valid public host")
        folded = normalized.casefold()
        return folded == "localhost" or folded.endswith(".localhost")

    effective_address = address
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id is not None or address.is_link_local:
            raise ValueError("public_base_url must not use a scoped or link-local IPv6 host")
        if address.ipv4_mapped is not None:
            effective_address = address.ipv4_mapped
    if effective_address.is_unspecified:
        raise ValueError("public_base_url must not use a wildcard host")
    return effective_address.is_loopback


def _build_auth_service(
    store: RuntimeStore,
    dashboard_auth: DashboardAuth,
    username: str,
    session_days: int,
    secure_cookie: bool,
) -> AuthService:
    return AuthService(
        store=store,
        username=username,
        password_record=dashboard_auth.password_record,
        session_secret=dashboard_auth.session_secret,
        session_days=session_days,
        secure_cookie=secure_cookie,
    )


def _dashboard_bind_url(host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display_host}:{port}"


def _warn_on_public_port_mismatch(
    state: RuntimeState,
    effective_port: int,
    public_base_url: str,
) -> None:
    parsed = urlsplit(public_base_url)
    public_url_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if effective_port == public_url_port:
        return
    message = (
        f"Dashboard effective port {effective_port} does not match public_base_url "
        f"port {public_url_port}; report URLs will continue to use {public_base_url}."
    )
    state.add_event(
        "dashboard_url_warning",
        message,
        severity="warning",
        category="dashboard",
        effective_port=effective_port,
        public_url_port=public_url_port,
        public_base_url=public_base_url,
    )
    print(f"Warning: {message}", file=sys.stderr)
