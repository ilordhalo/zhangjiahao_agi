from __future__ import annotations

from pathlib import Path
import os
import time

from symphonz.service.codex_app_server import CodexAppServer
from symphonz.service.dashboard import DashboardServer
from symphonz.service.dynamic_tools import execute_linear_graphql
from symphonz.service.event_log import JsonlEventLog
from symphonz.service.linear import LinearClient
from symphonz.service.models import RuntimeState
from symphonz.service.orchestrator import Orchestrator
from symphonz.service.workflow import load_workflow


def run_service(
    project_root: Path,
    workflow_path: Path,
    logs_root: Path,
    port: int | None,
    once: bool = False,
) -> int:
    workflow = load_workflow(workflow_path)
    logs_root.mkdir(parents=True, exist_ok=True)
    state = RuntimeState(event_sink=JsonlEventLog(logs_root / "runtime.jsonl").write)
    state.add_event("service_started", "Symphonz service started")

    linear = build_linear_client(workflow.config)
    codex_config = workflow.config.get("codex", {})
    codex = CodexAppServer(
        command=codex_config.get("command", "codex app-server"),
        dynamic_tool_executor=lambda _name, arguments: execute_linear_graphql(linear, arguments),
        read_timeout_ms=int(codex_config.get("read_timeout_ms", 5000)),
        turn_timeout_ms=int(codex_config.get("turn_timeout_ms", 3_600_000)),
        stall_timeout_ms=int(codex_config.get("stall_timeout_ms", 300_000)),
    )
    orchestrator = Orchestrator(project_root, workflow, linear, codex, state=state)
    orchestrator.startup_cleanup()

    dashboard = None
    if port is not None:
        dashboard = DashboardServer("127.0.0.1", port, state)
        dashboard.start()
        state.add_event("dashboard_started", f"Dashboard listening on http://127.0.0.1:{dashboard.port}")
        print(f"Symphonz dashboard: http://127.0.0.1:{dashboard.port}")

    try:
        if once:
            orchestrator.poll_once()
            return 0
        interval_ms = int(workflow.config.get("polling", {}).get("interval_ms", 5000))
        print("Symphonz service running. Press Ctrl+C to stop.")
        while True:
            orchestrator.tick()
            time.sleep(max(interval_ms, 1000) / 1000)
    except KeyboardInterrupt:
        state.add_event("service_stopped", "Symphonz service stopped")
        return 0
    finally:
        orchestrator.shutdown()
        if dashboard is not None:
            dashboard.stop()


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
