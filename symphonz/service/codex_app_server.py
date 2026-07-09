from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
import json
import subprocess


class CodexAppServer:
    def __init__(self, command: str):
        self.command = command
        self._next_id = 1

    def run_turn(
        self,
        workspace: Path,
        prompt: str,
        title: str,
        approval_policy: str | dict,
        thread_sandbox: str,
        turn_sandbox_policy: dict,
        on_event: Callable[[dict], None] | None = None,
    ) -> dict:
        on_event = on_event or (lambda event: None)
        process = subprocess.Popen(
            self.command,
            cwd=workspace,
            shell=True,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        try:
            self._request(process, "initialize", {"capabilities": {"experimentalApi": True}})
            self._notify(process, "initialized", {})
            thread_response = self._request(
                process,
                "thread/start",
                {
                    "approvalPolicy": approval_policy,
                    "sandbox": thread_sandbox,
                    "cwd": str(workspace),
                    "dynamicTools": [],
                },
            )
            thread_id = thread_response.get("thread", {}).get("id")
            if not thread_id:
                raise RuntimeError(f"Codex app-server returned invalid thread response: {thread_response!r}")

            turn_response = self._request(
                process,
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": str(workspace),
                    "title": title,
                    "approvalPolicy": approval_policy,
                    "sandboxPolicy": turn_sandbox_policy,
                },
            )
            turn_id = turn_response.get("turn", {}).get("id") or turn_response.get("turnId") or "turn"
            session_id = f"{thread_id}-{turn_id}"
            on_event({"type": "session_started", "session_id": session_id, "thread_id": thread_id, "turn_id": turn_id})
            result = self._await_completion(process, on_event)
            return {"thread_id": thread_id, "turn_id": turn_id, "session_id": session_id, "result": result}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()

    def _request(self, process: subprocess.Popen, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        self._send(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._read(process)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"Codex app-server {method} failed: {message['error']!r}")
            return message.get("result") or {}

    def _notify(self, process: subprocess.Popen, method: str, params: dict) -> None:
        self._send(process, {"jsonrpc": "2.0", "method": method, "params": params})

    def _await_completion(self, process: subprocess.Popen, on_event: Callable[[dict], None]) -> dict:
        while True:
            message = self._read(process)
            method = message.get("method", "")
            params = message.get("params") or {}
            if method:
                event = {"type": normalize_event_type(method), "method": method, "params": params}
                on_event(event)
                if method in {"turn/completed", "turn/complete", "turn/done"}:
                    return params
                if method in {"turn/failed", "turn/error"}:
                    raise RuntimeError(f"Codex turn failed: {params!r}")
            elif "error" in message:
                raise RuntimeError(f"Codex app-server stream error: {message['error']!r}")

    def _send(self, process: subprocess.Popen, message: dict) -> None:
        if process.stdin is None:
            raise RuntimeError("Codex app-server stdin is closed")
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def _read(self, process: subprocess.Popen) -> dict:
        if process.stdout is None:
            raise RuntimeError("Codex app-server stdout is closed")
        line = process.stdout.readline()
        if line == "":
            raise RuntimeError(f"Codex app-server exited with status {process.poll()}")
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return {"method": "log", "params": {"message": line.rstrip()}}


def normalize_event_type(method: str) -> str:
    return method.replace("/", "_").replace("-", "_")
