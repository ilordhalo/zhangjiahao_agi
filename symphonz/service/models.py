from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
import threading
import time


@dataclass(frozen=True)
class BlockerRef:
    id: str
    identifier: str
    state: str | None = None


@dataclass(frozen=True)
class Issue:
    id: str
    identifier: str
    title: str
    description: str | None = None
    state: str | None = None
    labels: list[str] = field(default_factory=list)
    url: str | None = None
    priority: int | None = None
    branch_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    blocked_by: list[BlockerRef] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowDefinition:
    path: Path
    config: dict
    prompt_template: str


@dataclass
class RuntimeEvent:
    type: str
    message: str
    issue_identifier: str | None = None
    timestamp: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)


@dataclass
class RuntimeErrorRecord:
    issue_identifier: str | None = None
    session_id: str | None = None
    stage: str = "runtime"
    error_type: str = "RuntimeError"
    message: str = ""
    retryable: bool = False
    attempt: int | None = None
    timestamp: float = field(default_factory=time.time)
    context: dict = field(default_factory=dict)


def runtime_error_from_event(event: RuntimeEvent) -> RuntimeErrorRecord | None:
    nested_event = event.data.get("event")
    data = nested_event if isinstance(nested_event, dict) else event.data
    event_type = str(data.get("type") or event.type).lower()
    result = data.get("result")
    is_failed_tool = "tool" in event_type and (
        data.get("success") is False or (isinstance(result, dict) and result.get("success") is False)
    )
    is_report_sync_failure = "report" in event_type and "sync" in event_type and (
        "failed" in event_type
        or data.get("linear_sync_status") == "failed"
        or data.get("sync_status") == "failed"
    )
    if not (
        event_type.endswith("_failed")
        or is_failed_tool
        or "exception" in event_type
        or "timeout" in event_type
        or "blocked" in event_type
        or is_report_sync_failure
    ):
        return None
    return RuntimeErrorRecord(
        issue_identifier=event.issue_identifier,
        session_id=_string_or_none(data.get("session_id") or data.get("codex_session_id")),
        stage=_string_or_none(data.get("stage")) or ("codex" if nested_event else event_type.split("_", 1)[0]),
        error_type=_string_or_none(data.get("error_type")) or str(data.get("type") or event.type),
        message=_error_message(event.message, data),
        retryable=bool(data.get("retryable", False)),
        attempt=_integer_or_none(data.get("attempt")),
        timestamp=event.timestamp,
        context=dict(event.data),
    )


def _error_message(default: str, data: dict) -> str:
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("output", "error", "message"):
            if result.get(key):
                return str(result[key])
    for key in ("error", "message"):
        if data.get(key):
            return str(data[key])
    return default


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def _integer_or_none(value: object) -> int | None:
    return int(value) if value is not None else None


@dataclass
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    running: dict[str, dict] = field(default_factory=dict)
    completed: dict[str, dict] = field(default_factory=dict)
    blocked: dict[str, dict] = field(default_factory=dict)
    retrying: dict[str, dict] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    events: list[RuntimeEvent] = field(default_factory=list)
    event_sink: Callable[[RuntimeEvent], None] | None = field(default=None, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def add_event(self, event_type: str, message: str, issue_identifier: str | None = None, **data: object) -> None:
        event = RuntimeEvent(event_type, message, issue_identifier, data=dict(data))
        with self.lock:
            self.events.append(event)
            self.events = self.events[-200:]
        if self.event_sink is not None:
            self.event_sink(event)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "started_at": self.started_at,
                "counts": {
                    "claimed": len(self.claimed),
                    "running": len(self.running),
                    "completed": len(self.completed),
                    "blocked": len(self.blocked),
                    "retrying": len(self.retrying),
                },
                "running": [dict(entry) for entry in self.running.values()],
                "completed": [dict(entry) for entry in self.completed.values()],
                "blocked": [dict(entry) for entry in self.blocked.values()],
                "retrying": [dict(entry) for entry in self.retrying.values()],
                "events": [
                    {
                        "type": event.type,
                        "message": event.message,
                        "issue_identifier": event.issue_identifier,
                        "timestamp": event.timestamp,
                        "data": event.data,
                    }
                    for event in self.events
                ],
            }
