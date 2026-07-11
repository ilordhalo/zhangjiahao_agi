from __future__ import annotations

from pathlib import Path
import json
import sys
import threading

from symphonz.service.models import RuntimeEvent


class JsonlEventLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, event: RuntimeEvent) -> None:
        payload = {
            "type": event.type,
            "message": event.message,
            "issue_identifier": event.issue_identifier,
            "timestamp": event.timestamp,
            "data": event.data,
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as output:
                    output.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        except OSError as error:
            print(f"Symphonz event log write failed: {error}", file=sys.stderr)
