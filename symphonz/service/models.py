from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time


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
    events: list[RuntimeEvent] = field(default_factory=list)

    def add_event(self, event_type: str, message: str, issue_identifier: str | None = None, **data: object) -> None:
        self.events.append(RuntimeEvent(event_type, message, issue_identifier, data=dict(data)))
        self.events = self.events[-200:]

    def snapshot(self) -> dict:
        return {
            "started_at": self.started_at,
            "counts": {
                "running": len(self.running),
                "completed": len(self.completed),
                "blocked": len(self.blocked),
                "retrying": len(self.retrying),
            },
            "running": list(self.running.values()),
            "completed": list(self.completed.values()),
            "blocked": list(self.blocked.values()),
            "retrying": list(self.retrying.values()),
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

