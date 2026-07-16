from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from itertools import count
from pathlib import Path
import ctypes
import json
import os
import queue
import select
import signal
import subprocess
import threading
import time

from symphonz import __version__
from symphonz.service.dynamic_tools import linear_graphql_tool_spec


_STREAM_CLOSED = object()


class _ApprovalDeclined(RuntimeError):
    pass


class CodexAppServer:
    def __init__(
        self,
        command: str,
        *,
        dynamic_tool_specs: list[dict] | None = None,
        dynamic_tool_executor: Callable[[str, object], dict] | None = None,
        read_timeout_ms: int = 5000,
        turn_timeout_ms: int = 3_600_000,
        stall_timeout_ms: int = 300_000,
    ):
        self.command = command
        if dynamic_tool_specs is None:
            dynamic_tool_specs = [linear_graphql_tool_spec()] if dynamic_tool_executor else []
        self.dynamic_tool_specs = deepcopy(dynamic_tool_specs)
        self.dynamic_tool_executor = dynamic_tool_executor
        self.read_timeout_ms = read_timeout_ms
        self.turn_timeout_ms = turn_timeout_ms
        self.stall_timeout_ms = stall_timeout_ms

    def run_turn(
        self,
        workspace: Path,
        prompt: str,
        title: str,
        approval_policy: str | dict,
        thread_sandbox: str,
        turn_sandbox_policy: dict,
        on_event: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        dynamic_tool_specs: list[dict] | None = None,
        dynamic_tool_executor: Callable[[str, object], dict] | None = None,
    ) -> dict:
        return self.run_turns(
            workspace=workspace,
            prompt=prompt,
            title=title,
            approval_policy=approval_policy,
            thread_sandbox=thread_sandbox,
            turn_sandbox_policy=turn_sandbox_policy,
            max_turns=1,
            should_continue=lambda: False,
            continuation_prompt=lambda _turn: "Continue working from the current thread context.",
            on_event=on_event,
            cancel_event=cancel_event,
            dynamic_tool_specs=dynamic_tool_specs,
            dynamic_tool_executor=dynamic_tool_executor,
        )

    def run_turns(
        self,
        workspace: Path,
        prompt: str,
        title: str,
        approval_policy: str | dict,
        thread_sandbox: str,
        turn_sandbox_policy: dict,
        max_turns: int,
        should_continue: Callable[[], bool],
        continuation_prompt: Callable[[int], str],
        on_event: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        dynamic_tool_specs: list[dict] | None = None,
        dynamic_tool_executor: Callable[[str, object], dict] | None = None,
    ) -> dict:
        on_event = on_event or (lambda event: None)
        cancel_event = cancel_event or threading.Event()
        run_tool_specs = deepcopy(
            self.dynamic_tool_specs if dynamic_tool_specs is None else dynamic_tool_specs
        )
        run_tool_names = frozenset(
            spec["name"]
            for spec in run_tool_specs
            if isinstance(spec, dict) and isinstance(spec.get("name"), str)
        )
        run_tool_executor = (
            self.dynamic_tool_executor if dynamic_tool_executor is None else dynamic_tool_executor
        )
        request_ids = count(1)
        process = subprocess.Popen(
            self.command,
            cwd=workspace,
            shell=True,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            start_new_session=(os.name == "posix"),
        )
        process_group_id = _capture_process_group(process)
        stream = _JsonLineStream(process.stdout)
        stderr_lines: queue.Queue[object] = queue.Queue()
        stderr_closed = _start_line_reader(process.stderr, stderr_lines, decode_json=False)
        graceful_shutdown = False
        try:
            self._request(
                process,
                stream,
                "initialize",
                {
                    "clientInfo": {"name": "symphonz", "version": __version__},
                    "capabilities": {"experimentalApi": True},
                },
                cancel_event,
                request_ids,
            )
            self._notify(process, "initialized", {})
            thread_response = self._request(
                process,
                stream,
                "thread/start",
                {
                    "approvalPolicy": approval_policy,
                    "sandbox": thread_sandbox,
                    "cwd": str(workspace),
                    "dynamicTools": run_tool_specs,
                },
                cancel_event,
                request_ids,
            )
            thread_id = thread_response.get("thread", {}).get("id")
            if not thread_id:
                raise RuntimeError(f"invalid_thread_response: {thread_response!r}")

            turn_count = 0
            current_prompt = prompt
            last_result: dict = {}
            last_turn_id = "turn"
            while turn_count < max(1, int(max_turns)):
                turn_response = self._request(
                    process,
                    stream,
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": current_prompt}],
                        "cwd": str(workspace),
                        "title": title,
                        "approvalPolicy": approval_policy,
                        "sandboxPolicy": turn_sandbox_policy,
                    },
                    cancel_event,
                    request_ids,
                    on_message=lambda message: self._handle_pre_response_message(
                        process,
                        message,
                        on_event,
                        approval_policy,
                        run_tool_names,
                        run_tool_executor,
                    ),
                )
                last_turn_id = turn_response.get("turn", {}).get("id") or turn_response.get("turnId") or "turn"
                turn_count += 1
                session_id = f"{thread_id}-{last_turn_id}"
                on_event(
                    {
                        "type": "session_started",
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "turn_id": last_turn_id,
                        "turn_count": turn_count,
                        "process_id": process.pid,
                    }
                )
                last_result = self._await_completion(
                    process,
                    stream,
                    on_event,
                    cancel_event,
                    approval_policy,
                    run_tool_names,
                    run_tool_executor,
                )
                if turn_count >= max(1, int(max_turns)) or not should_continue():
                    break
                current_prompt = continuation_prompt(turn_count + 1)

            return {
                "thread_id": thread_id,
                "turn_id": last_turn_id,
                "session_id": f"{thread_id}-{last_turn_id}",
                "turn_count": turn_count,
                "result": last_result,
                "stderr": _drain_text_queue(stderr_lines),
            }
        except _ApprovalDeclined:
            graceful_shutdown = True
            raise
        finally:
            _stop_process(
                process,
                graceful=graceful_shutdown,
                process_group_id=process_group_id,
                pipe_closed=(stream.closed, stderr_closed),
            )

    def _request(
        self,
        process: subprocess.Popen,
        stream: "_JsonLineStream",
        method: str,
        params: dict,
        cancel_event: threading.Event,
        request_ids: Iterator[int],
        on_message: Callable[[dict], bool] | None = None,
    ) -> dict:
        request_id = next(request_ids)
        self._send(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + max(self.read_timeout_ms, 1) / 1000
        while True:
            message = stream.get_response(
                request_id,
                deadline=deadline,
                cancel_event=cancel_event,
                timeout_error="response_timeout",
                on_message=on_message,
            )
            if "error" in message:
                raise RuntimeError(f"response_error: {message['error']!r}")
            return message.get("result") or {}

    def _notify(self, process: subprocess.Popen, method: str, params: dict) -> None:
        self._send(process, {"jsonrpc": "2.0", "method": method, "params": params})

    def _handle_pre_response_message(
        self,
        process: subprocess.Popen,
        message: dict,
        on_event: Callable[[dict], None],
        approval_policy: str | dict,
        advertised_tool_names: frozenset[str],
        dynamic_tool_executor: Callable[[str, object], dict] | None,
    ) -> bool:
        method = message.get("method", "")
        params = message.get("params") or {}
        if method == "item/tool/call" and "id" in message:
            on_event(
                self._handle_tool_call(
                    process, message, advertised_tool_names, dynamic_tool_executor
                )
            )
            return True
        if method in {"item/tool/requestUserInput", "mcpServer/elicitation/request"} or _needs_input(method, message):
            on_event({"type": "turn_input_required", "method": method, "params": params})
            raise RuntimeError("turn_input_required")
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"} and "id" in message:
            self._handle_approval_request(process, message, on_event, approval_policy)
        return False

    def _await_completion(
        self,
        process: subprocess.Popen,
        stream: "_JsonLineStream",
        on_event: Callable[[dict], None],
        cancel_event: threading.Event,
        approval_policy: str | dict,
        advertised_tool_names: frozenset[str],
        dynamic_tool_executor: Callable[[str, object], dict] | None,
    ) -> dict:
        started = time.monotonic()
        last_activity = started
        turn_deadline = started + max(self.turn_timeout_ms, 1) / 1000
        while True:
            now = time.monotonic()
            if now >= turn_deadline:
                raise RuntimeError("turn_timeout")
            stall_deadline = (
                last_activity + self.stall_timeout_ms / 1000
                if self.stall_timeout_ms > 0
                else turn_deadline
            )
            deadline = min(turn_deadline, stall_deadline)
            try:
                message = stream.get(deadline=deadline, cancel_event=cancel_event, timeout_error="turn_timeout")
            except RuntimeError as error:
                if str(error) == "turn_timeout" and self.stall_timeout_ms > 0 and time.monotonic() >= stall_deadline:
                    raise RuntimeError("stall_timeout") from error
                raise
            last_activity = time.monotonic()
            method = message.get("method", "")
            params = message.get("params") or {}
            if method == "item/tool/call" and "id" in message:
                event = self._handle_tool_call(
                    process, message, advertised_tool_names, dynamic_tool_executor
                )
                on_event(event)
                continue
            if method in {"item/tool/requestUserInput", "mcpServer/elicitation/request"} or _needs_input(method, message):
                on_event({"type": "turn_input_required", "method": method, "params": params})
                raise RuntimeError("turn_input_required")
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"} and "id" in message:
                self._handle_approval_request(process, message, on_event, approval_policy)
            if method:
                event = {"type": normalize_event_type(method), "method": method, "params": params}
                on_event(event)
                if method in {"turn/completed", "turn/complete", "turn/done"}:
                    return params
                if method in {"turn/failed", "turn/error"}:
                    raise RuntimeError(f"turn_failed: {params!r}")
                if method == "turn/cancelled":
                    raise RuntimeError(f"turn_cancelled: {params!r}")
            elif "error" in message:
                raise RuntimeError(f"stream_error: {message['error']!r}")

    def _handle_approval_request(
        self,
        process: subprocess.Popen,
        message: dict,
        on_event: Callable[[dict], None],
        approval_policy: str | dict,
    ) -> None:
        method = message.get("method", "")
        params = message.get("params") or {}
        if approval_policy == "never":
            self._send(
                process,
                {"jsonrpc": "2.0", "id": message["id"], "result": {"decision": "decline"}},
            )
            try:
                on_event({"type": "approval_rejected", "method": method, "params": params})
            except Exception as error:
                raise _ApprovalDeclined("approval_required") from error
            raise _ApprovalDeclined("approval_required")
        raise RuntimeError("approval_required")

    def _handle_tool_call(
        self,
        process: subprocess.Popen,
        message: dict,
        advertised_tool_names: frozenset[str],
        dynamic_tool_executor: Callable[[str, object], dict] | None,
    ) -> dict:
        params = message.get("params") or {}
        tool_name = params.get("tool") or params.get("name")
        arguments = params.get("arguments") or {}
        if tool_name not in advertised_tool_names:
            result = _tool_failure(f"Unsupported dynamic tool: {tool_name or '<missing>'}")
            event_type = "unsupported_tool_call"
        elif dynamic_tool_executor is None:
            result = _tool_failure(f"Dynamic tool executor is unavailable: {tool_name}")
            event_type = "tool_call_failed"
        else:
            try:
                result = _normalize_tool_result(dynamic_tool_executor(tool_name, arguments))
            except Exception as error:
                result = _tool_failure(str(error))
            event_type = "tool_call_completed" if result["success"] else "tool_call_failed"
        self._send(process, {"jsonrpc": "2.0", "id": message["id"], "result": result})
        return {"type": event_type, "method": "item/tool/call", "tool": tool_name, "result": result}

    def _send(self, process: subprocess.Popen, message: dict) -> None:
        if process.stdin is None:
            raise RuntimeError("port_exit: stdin closed")
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()


class _JsonLineStream:
    def __init__(self, stdout):
        self.messages: queue.Queue[object] = queue.Queue()
        self.pending: list[dict] = []
        self.closed = _start_line_reader(stdout, self.messages, decode_json=True)

    def get(self, *, deadline: float, cancel_event: threading.Event, timeout_error: str) -> dict:
        if self.pending:
            return self.pending.pop(0)
        return self._get_queued(deadline=deadline, cancel_event=cancel_event, timeout_error=timeout_error)

    def get_response(
        self,
        request_id: int,
        *,
        deadline: float,
        cancel_event: threading.Event,
        timeout_error: str,
        on_message: Callable[[dict], bool] | None = None,
    ) -> dict:
        while True:
            message = self._get_queued(deadline=deadline, cancel_event=cancel_event, timeout_error=timeout_error)
            if message.get("id") == request_id and ("result" in message or "error" in message):
                return message
            if on_message is not None and on_message(message):
                continue
            self.pending.append(message)

    def _get_queued(self, *, deadline: float, cancel_event: threading.Event, timeout_error: str) -> dict:
        while True:
            if cancel_event.is_set():
                raise RuntimeError("turn_cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(timeout_error)
            try:
                item = self.messages.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if item is _STREAM_CLOSED:
                raise RuntimeError("port_exit")
            if isinstance(item, dict):
                return item


def _start_line_reader(
    stream, target: queue.Queue[object], *, decode_json: bool
) -> threading.Event:
    closed = threading.Event()

    def read_lines() -> None:
        if stream is None:
            closed.set()
            target.put(_STREAM_CLOSED)
            return
        try:
            for line in stream:
                text = line.rstrip("\n")
                if decode_json:
                    try:
                        target.put(json.loads(text))
                    except json.JSONDecodeError:
                        target.put({"method": "log", "params": {"message": text}})
                else:
                    target.put(text)
        finally:
            closed.set()
            target.put(_STREAM_CLOSED)

    threading.Thread(target=read_lines, daemon=True).start()
    return closed


def _normalize_tool_result(result: object) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
        return _tool_failure(repr(result))
    normalized = dict(result)
    output = normalized.get("output")
    if not isinstance(output, str):
        output = json.dumps(normalized, sort_keys=True)
        normalized["output"] = output
    if not isinstance(normalized.get("contentItems"), list):
        normalized["contentItems"] = [{"type": "inputText", "text": output}]
    return normalized


def _tool_failure(message: str) -> dict:
    return {
        "success": False,
        "output": message,
        "contentItems": [{"type": "inputText", "text": message}],
    }


def _needs_input(method: str, message: dict) -> bool:
    if method in {"turn/input_required", "turn/needs_input", "turn/request_input", "turn/approval_required"}:
        return True
    params = message.get("params") or {}
    return any(message.get(key) is True or params.get(key) is True for key in ("requiresInput", "needsInput", "inputRequired", "input_required"))


def _drain_text_queue(lines: queue.Queue[object]) -> list[str]:
    result: list[str] = []
    while True:
        try:
            item = lines.get_nowait()
        except queue.Empty:
            return result
        if isinstance(item, str):
            result.append(item)


def _capture_process_group(process: subprocess.Popen) -> int | None:
    if os.name != "posix":
        return None
    try:
        process_group_id = os.getpgid(process.pid)
    except ProcessLookupError:
        return None
    return process_group_id if process_group_id == process.pid else None


def _stop_process(
    process: subprocess.Popen,
    *,
    graceful: bool = False,
    process_group_id: int | None = None,
    pipe_closed: tuple[threading.Event, ...] = (),
) -> None:
    if os.name == "posix" and process_group_id is not None:
        _stop_process_group(
            process,
            process_group_id,
            graceful=graceful,
            pipe_closed=pipe_closed,
        )
        _close_process_streams(process)
        return

    if graceful and process.poll() is None and process.stdin is not None:
        try:
            process.stdin.close()
            process.wait(timeout=2)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    _close_process_streams(process)


def _stop_process_group(
    process: subprocess.Popen,
    process_group_id: int,
    *,
    graceful: bool,
    pipe_closed: tuple[threading.Event, ...],
) -> None:
    leader_exited = _wait_for_process_exit_without_reaping(
        process.pid, timeout=0, pipe_closed=pipe_closed
    )
    if graceful and process.stdin is not None:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        leader_exited = leader_exited or _wait_for_process_exit_without_reaping(
            process.pid, timeout=2, pipe_closed=pipe_closed
        )

    pipes_closed = all(event.is_set() for event in pipe_closed)
    if not leader_exited or not pipes_closed:
        _signal_process_group(process_group_id, signal.SIGTERM)
        deadline = time.monotonic() + 2
        leader_exited = leader_exited or _wait_for_process_exit_without_reaping(
            process.pid,
            timeout=max(0, deadline - time.monotonic()),
            pipe_closed=pipe_closed,
        )
        pipes_closed = _wait_for_events(pipe_closed, deadline=deadline)

        if not leader_exited or not pipes_closed:
            # The unreaped session leader keeps this group ID reserved until all
            # group signals complete, so a recycled group cannot be targeted.
            _signal_process_group(process_group_id, signal.SIGKILL)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    _wait_for_events(pipe_closed, deadline=time.monotonic() + 2)


def _wait_for_process_exit_without_reaping(
    process_id: int,
    *,
    timeout: float,
    pipe_closed: tuple[threading.Event, ...],
) -> bool:
    timeout = max(0, timeout)
    pidfd_open = getattr(os, "pidfd_open", None)
    if callable(pidfd_open):
        try:
            descriptor = pidfd_open(process_id)
        except ProcessLookupError:
            return True
        except (OSError, NotImplementedError):
            pass
        else:
            try:
                ready, _, _ = select.select([descriptor], [], [], timeout)
                return bool(ready)
            except (OSError, NotImplementedError):
                pass
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    kqueue = getattr(select, "kqueue", None)
    if callable(kqueue):
        try:
            watcher = kqueue()
        except (OSError, NotImplementedError):
            pass
        else:
            try:
                event = select.kevent(
                    process_id,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
                return bool(watcher.control([event], 1, timeout))
            except ProcessLookupError:
                return True
            except (OSError, NotImplementedError):
                pass
            finally:
                try:
                    watcher.close()
                except OSError:
                    pass

    waitid_result = _wait_with_os_waitid(process_id, timeout=timeout)
    if waitid_result is not None:
        return waitid_result
    waitid_result = _wait_with_libc_waitid(process_id, timeout=timeout)
    if waitid_result is not None:
        return waitid_result

    # With no non-reaping process primitive, use pipe EOF only to end the
    # bounded grace period. It is not proof of leader exit, so return false and
    # let the caller signal the still-reserved process group before reaping.
    _wait_for_events(pipe_closed, deadline=time.monotonic() + timeout)
    return False


def _wait_with_os_waitid(process_id: int, *, timeout: float) -> bool | None:
    waitid = getattr(os, "waitid", None)
    required = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    if not callable(waitid) or not all(hasattr(os, name) for name in required):
        return None

    try:
        status = waitid(
            os.P_PID,
            process_id,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except (ChildProcessError, ProcessLookupError):
        return True
    except (OSError, NotImplementedError):
        return None
    if status is not None or timeout <= 0:
        return status is not None

    return _wait_for_blocking_waitid(
        lambda: waitid(os.P_PID, process_id, os.WEXITED | os.WNOWAIT),
        timeout=timeout,
    )


def _wait_with_libc_waitid(process_id: int, *, timeout: float) -> bool | None:
    required = ("P_PID", "WEXITED", "WNOWAIT")
    if timeout <= 0 or not all(hasattr(os, name) for name in required):
        return None
    try:
        waitid = ctypes.CDLL(None, use_errno=True).waitid
        waitid.argtypes = (ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
        waitid.restype = ctypes.c_int
    except (AttributeError, OSError):
        return None

    def observe() -> None:
        status = ctypes.create_string_buffer(256)
        if waitid(
            os.P_PID,
            process_id,
            ctypes.byref(status),
            os.WEXITED | os.WNOWAIT,
        ) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))

    return _wait_for_blocking_waitid(observe, timeout=timeout)


def _wait_for_blocking_waitid(
    observe: Callable[[], object], *, timeout: float
) -> bool | None:
    completed = threading.Event()
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            observe()
        except (ChildProcessError, ProcessLookupError):
            outcome["exited"] = True
        except (OSError, NotImplementedError):
            outcome["unavailable"] = True
        else:
            outcome["exited"] = True
        finally:
            completed.set()

    threading.Thread(target=run, daemon=True).start()
    if not completed.wait(timeout=max(0, timeout)):
        return False
    if outcome.get("unavailable"):
        return None
    return bool(outcome.get("exited"))


def _wait_for_events(events: tuple[threading.Event, ...], *, deadline: float) -> bool:
    for event in events:
        if not event.wait(timeout=max(0, deadline - time.monotonic())):
            return False
    return True


def _signal_process_group(process_group_id: int, signal_number: int) -> None:
    try:
        os.killpg(process_group_id, signal_number)
    except OSError:
        pass


def _close_process_streams(process: subprocess.Popen) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (BrokenPipeError, OSError):
                pass


def normalize_event_type(method: str) -> str:
    return method.replace("/", "_").replace("-", "_")
