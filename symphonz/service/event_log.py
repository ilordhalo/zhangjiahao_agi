from __future__ import annotations

from pathlib import Path
import json
import re
import sys
import threading

from symphonz.service.models import RuntimeErrorRecord, RuntimeEvent, runtime_error_from_event


_REDACTED = "[REDACTED]"
_MAX_DETAIL_BYTES = 16 * 1024
_EXACT_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "client_key",
    "cookie",
    "credentials",
    "passwd",
    "password",
    "private_key",
    "refresh_token",
    "session_token",
    "set_cookie",
}


def redact_sensitive_data(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _is_secret_key(str(key)) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return _REDACTED
    return value


def bounded_redacted_data(value: object, maximum_bytes: int = _MAX_DETAIL_BYTES) -> object:
    redacted = redact_sensitive_data(value)
    encoded = json.dumps(redacted, sort_keys=True, default=str).encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return redacted
    return {"truncated": True, "original_bytes": len(encoded)}


def _is_secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    compact = normalized.replace("_", "")
    return (
        normalized in _EXACT_SECRET_KEYS
        or compact in _EXACT_SECRET_KEYS
        or "secret" in normalized
        or normalized.endswith("_api_key")
        or normalized.endswith("_authorization")
        or normalized.endswith("_token")
        or normalized == "token"
    )


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
            "data": bounded_redacted_data(event.data),
        }
        self._write_payload(payload)

    def _write_payload(self, payload: dict) -> None:
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as output:
                    output.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        except OSError as error:
            print(f"Symphonz event log write failed: {error}", file=sys.stderr)


class ErrorJsonlLog(JsonlEventLog):
    def write(self, record: RuntimeErrorRecord | RuntimeEvent) -> None:
        if isinstance(record, RuntimeEvent):
            record = runtime_error_from_event(record)
            if record is None:
                return
        payload = {
            "issue_identifier": record.issue_identifier,
            "session_id": record.session_id,
            "stage": record.stage,
            "error_type": record.error_type,
            "message": record.message,
            "retryable": record.retryable,
            "attempt": record.attempt,
            "timestamp": record.timestamp,
            "context": bounded_redacted_data(record.context),
        }
        self._write_payload(payload)


class CompositeEventSink:
    def __init__(self, *sinks):
        self._sinks = tuple(sink for sink in sinks if sink is not None)

    def __call__(self, event: RuntimeEvent) -> None:
        self.write(event)

    def write(self, event: RuntimeEvent) -> None:
        for sink in self._sinks:
            writer = sink.write if hasattr(sink, "write") else sink
            try:
                writer(event)
            except Exception as error:
                print(f"Symphonz event sink failed: {error}", file=sys.stderr)
