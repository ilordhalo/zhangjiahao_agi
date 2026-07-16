from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
import time

from symphonz.service.codex_app_server import CodexAppServer
from symphonz.service.attempt_store import AttemptStore
from symphonz.service.dashboard import DashboardServer
from symphonz.service.event_log import CompositeEventSink, ErrorJsonlLog, JsonlEventLog, RuntimeEventRouter
from symphonz.service.linear import LinearClient
from symphonz.service.models import Issue, RuntimeErrorRecord, RuntimeState
from symphonz.service.orchestrator import Orchestrator
from symphonz.service.reporting import PendingReportSynchronizer, ReportPublisher
from symphonz.service.runtime_store import RuntimeStore
from symphonz.service.workflow import load_workflow


_LOCAL_PUBLIC_BASE_URL = "http://127.0.0.1"


@dataclass(frozen=True)
class RuntimeCollaborators:
    store: RuntimeStore
    state: RuntimeState
    artifacts_root: Path
    report_publisher_factory: Callable[[Issue], ReportPublisher]
    report_synchronizer: PendingReportSynchronizer


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
) -> RuntimeCollaborators:
    logs_root.mkdir(parents=True, exist_ok=True)
    store = RuntimeStore(logs_root / "runtime.sqlite3")
    runtime_log = JsonlEventLog(logs_root / "runtime.jsonl")
    error_log = ErrorJsonlLog(logs_root / "errors.jsonl")
    state = RuntimeState(event_sink=RuntimeEventRouter(runtime_log, store, error_log))
    report_error_sink = CompositeEventSink(store.record_error, error_log)
    report_store = _ReportStore(store, report_error_sink)
    artifacts_root = logs_root / "artifacts"
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
) -> int:
    workflow = load_workflow(workflow_path)
    linear = build_linear_client(workflow.config)
    collaborators = build_runtime_collaborators(logs_root, linear, public_base_url)
    state = collaborators.state
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
    state.add_event("service_started", "Symphonz service started")
    try:
        orchestrator.startup_cleanup()
        if port is not None:
            dashboard = DashboardServer("127.0.0.1", port, state)
            dashboard.start()
            state.add_event("dashboard_started", f"Dashboard listening on http://127.0.0.1:{dashboard.port}")
            print(f"Symphonz dashboard: http://127.0.0.1:{dashboard.port}")

        if once:
            orchestrator.poll_once()
            return 0
        interval_ms = int(workflow.config.get("polling", {}).get("interval_ms", 5000))
        print("Symphonz service running. Press Ctrl+C to stop.")
        while True:
            orchestrator.tick()
            time.sleep(max(interval_ms, 1000) / 1000)
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            orchestrator.shutdown()
        finally:
            try:
                if dashboard is not None:
                    dashboard.stop()
            finally:
                state.add_event("service_stopped", "Symphonz service stopped")


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
