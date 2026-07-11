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
