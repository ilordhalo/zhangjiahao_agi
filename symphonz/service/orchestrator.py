from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
import time

from symphonz.service.codex_app_server import CodexAppServer
from symphonz.service.linear import LinearClient
from symphonz.service.models import Issue, RuntimeState, WorkflowDefinition
from symphonz.service.workflow import render_prompt
from symphonz.service.workspace import (
    prepare_workspace,
    remove_workspace,
    run_after_run_hook,
    run_before_run_hook,
    workspace_path,
)


class Orchestrator:
    def __init__(
        self,
        project_root: Path,
        workflow: WorkflowDefinition,
        linear_client: LinearClient,
        codex_client: CodexAppServer,
        state: RuntimeState | None = None,
        clock=time.monotonic,
    ):
        self.project_root = project_root
        self.workflow = workflow
        self.linear_client = linear_client
        self.codex_client = codex_client
        self.state = state or RuntimeState()
        self.clock = clock
        self.max_concurrent_agents = max(1, int(self.workflow.config.get("agent", {}).get("max_concurrent_agents", 10)))
        self.executor = ThreadPoolExecutor(max_workers=self.max_concurrent_agents, thread_name_prefix="symphonz")
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._issues: dict[str, Issue] = {}
        self._retry_issues: dict[str, Issue] = {}
        self._cancel_reasons: dict[str, str] = {}
        self._closed = False

    def tick(self) -> None:
        if self._closed:
            return
        self._collect_finished()
        self._reconcile_running()
        self._collect_finished()
        self._reconcile_blocked()
        self._dispatch_due_retries()
        self._dispatch_candidates()

    def poll_once(self) -> None:
        self.tick()
        self.wait_for_idle(timeout=None)

    def wait_for_idle(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._collect_finished()
            with self.state.lock:
                if not self._futures:
                    return
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for Symphonz workers to finish")
            time.sleep(0.01)

    def startup_cleanup(self) -> None:
        terminal_states = self.terminal_states()
        try:
            terminal_issues = self.linear_client.fetch_issues_by_states(terminal_states)
        except Exception as error:
            self.state.add_event("startup_cleanup_failed", f"Startup cleanup failed: {error}")
            return
        for issue in terminal_issues:
            self._cleanup_terminal_workspace(issue)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self.state.lock:
            for event in self._cancel_events.values():
                event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self._collect_finished()

    def _dispatch_candidates(self) -> None:
        if self._available_slots() <= 0:
            return
        try:
            issues = self.linear_client.fetch_candidate_issues(self.active_states())
        except Exception as error:
            self.state.add_event("tracker_poll_failed", f"Linear candidate poll failed: {error}")
            return
        for issue in sorted(issues, key=self._dispatch_sort_key):
            if self._available_slots() <= 0:
                break
            if not self._eligible(issue):
                continue
            with self.state.lock:
                if issue.id in self.state.claimed:
                    continue
            self._dispatch(issue, attempt=0, already_claimed=False)

    def _dispatch_due_retries(self) -> None:
        now = self.clock()
        with self.state.lock:
            due_ids = [issue_id for issue_id, entry in self.state.retrying.items() if entry["due_at"] <= now]
        for issue_id in due_ids:
            if self._available_slots() <= 0:
                break
            with self.state.lock:
                entry = self.state.retrying.pop(issue_id, None)
                issue = self._retry_issues.pop(issue_id, None)
            if entry is None or issue is None:
                continue
            try:
                refreshed = self.linear_client.fetch_issues_by_ids([issue_id])
            except Exception as error:
                self._schedule_retry(issue, int(entry["attempt"]) + 1, str(error))
                continue
            if not refreshed:
                self._release(issue_id)
                continue
            issue = refreshed[0]
            if not self._eligible(issue):
                if self._terminal(issue):
                    self._cleanup_terminal_workspace(issue)
                self._release(issue_id)
                continue
            self._dispatch(issue, attempt=int(entry["attempt"]), already_claimed=True)

    def _dispatch(self, issue: Issue, *, attempt: int, already_claimed: bool) -> None:
        cancel_event = threading.Event()
        entry = self._entry(issue, status="running", attempt=attempt)
        with self.state.lock:
            if issue.id in self._futures:
                return
            if not already_claimed:
                self.state.claimed.add(issue.id)
            self.state.blocked.pop(issue.id, None)
            self.state.completed.pop(issue.id, None)
            self.state.running[issue.id] = entry
            self._issues[issue.id] = issue
            self._cancel_events[issue.id] = cancel_event
            future = self.executor.submit(self._run_issue, issue, attempt, cancel_event)
            self._futures[issue.id] = future
        self.state.add_event("issue_started", f"Started {issue.identifier}", issue.identifier, attempt=attempt)

    def _run_issue(self, issue: Issue, attempt: int, cancel_event: threading.Event) -> dict:
        workspace = prepare_workspace(self.project_root, self.workflow, issue)
        latest_issue = [issue]
        hook_started = False
        try:
            run_before_run_hook(workspace, self.workflow, issue)
            hook_started = True
            prompt = render_prompt(self.workflow.prompt_template, issue, attempt=attempt or None)

            def should_continue() -> bool:
                if cancel_event.is_set():
                    return False
                refreshed = self.linear_client.fetch_issues_by_ids([issue.id])
                if not refreshed:
                    return False
                latest_issue[0] = refreshed[0]
                return self._eligible(refreshed[0])

            result = self.codex_client.run_turns(
                workspace=workspace,
                prompt=prompt,
                title=f"{issue.identifier}: {issue.title}",
                approval_policy=self.codex_approval_policy(),
                thread_sandbox=self.codex_thread_sandbox(),
                turn_sandbox_policy=self.codex_turn_sandbox_policy(workspace),
                max_turns=self.max_turns(),
                should_continue=should_continue,
                continuation_prompt=lambda turn: (
                    f"Continue work on {issue.identifier} in the same workspace and thread. "
                    f"This is in-process continuation turn {turn}; re-read Linear and the Workpad before acting."
                ),
                on_event=lambda event: self.record_codex_event(issue, event),
                cancel_event=cancel_event,
            )
            still_active = should_continue()
            return {"result": result, "issue": latest_issue[0], "still_active": still_active}
        finally:
            if hook_started or workspace.exists():
                run_after_run_hook(workspace, self.workflow, latest_issue[0])

    def _collect_finished(self) -> None:
        with self.state.lock:
            done = [(issue_id, future) for issue_id, future in self._futures.items() if future.done()]
        for issue_id, future in done:
            with self.state.lock:
                if self._futures.get(issue_id) is not future:
                    continue
                self._futures.pop(issue_id, None)
                self._cancel_events.pop(issue_id, None)
                entry = self.state.running.pop(issue_id, self._entry(self._issues[issue_id], status="running"))
                issue = self._issues.get(issue_id)
                cancel_reason = self._cancel_reasons.pop(issue_id, None)
            try:
                outcome = future.result()
            except Exception as error:
                self._handle_worker_error(issue, entry, error, cancel_reason)
                continue
            completed_issue = outcome.get("issue") or issue
            if self._closed and not cancel_reason:
                self._finish_cancelled(completed_issue, entry, "shutdown")
            elif cancel_reason:
                self._finish_cancelled(completed_issue, entry, cancel_reason)
            elif outcome.get("still_active"):
                self._schedule_retry(completed_issue, max(int(entry.get("attempt", 0)), 1), None, delay=1.0)
                self.state.add_event("issue_continuing", f"Continuing {completed_issue.identifier}", completed_issue.identifier)
            else:
                if self._terminal(completed_issue):
                    self._cleanup_terminal_workspace(completed_issue)
                self._complete(completed_issue, entry, outcome.get("result") or {})

    def _handle_worker_error(self, issue: Issue, entry: dict, error: Exception, cancel_reason: str | None) -> None:
        message = str(error)
        if self._closed and not cancel_reason:
            self._finish_cancelled(issue, entry, "shutdown")
            return
        if cancel_reason:
            self._finish_cancelled(issue, entry, cancel_reason)
            return
        if "turn_input_required" in message or "approval_required" in message:
            blocked = dict(entry)
            blocked.update({"status": "blocked", "blocked_at": time.time(), "error": message, "state": issue.state})
            with self.state.lock:
                self.state.blocked[issue.id] = blocked
            self.state.add_event("issue_blocked", f"Blocked {issue.identifier}: {message}", issue.identifier)
            return
        attempt = int(entry.get("attempt", 0)) + 1
        self._schedule_retry(issue, attempt, message)
        self.state.add_event("issue_retrying", f"Retrying {issue.identifier}: {message}", issue.identifier, attempt=attempt)

    def _schedule_retry(
        self,
        issue: Issue,
        attempt: int,
        error: str | None,
        *,
        delay: float | None = None,
    ) -> None:
        if delay is None:
            max_delay = max(1, int(self.workflow.config.get("agent", {}).get("max_retry_backoff_ms", 300000))) / 1000
            delay = min(10 * (2 ** max(attempt - 1, 0)), max_delay)
        entry = self._entry(issue, status="retrying", attempt=attempt)
        entry.update({"due_at": self.clock() + delay, "due_at_epoch": time.time() + delay, "error": error})
        with self.state.lock:
            self.state.retrying[issue.id] = entry
            self._retry_issues[issue.id] = issue
            self.state.claimed.add(issue.id)

    def _reconcile_running(self) -> None:
        with self.state.lock:
            issue_ids = list(self._futures)
        if not issue_ids:
            return
        try:
            refreshed = {issue.id: issue for issue in self.linear_client.fetch_issues_by_ids(issue_ids)}
        except Exception as error:
            self.state.add_event("reconcile_failed", f"Running issue reconciliation failed: {error}")
            return
        for issue_id in issue_ids:
            issue = refreshed.get(issue_id)
            reason = None
            if issue is None:
                reason = "missing"
            elif self._terminal(issue):
                reason = "terminal"
            elif not self._eligible(issue):
                reason = "ineligible"
            if reason:
                with self.state.lock:
                    event = self._cancel_events.get(issue_id)
                    self._cancel_reasons[issue_id] = reason
                    running = self.state.running.get(issue_id)
                    if running is not None:
                        running["cancellation_reason"] = reason
                if event is not None:
                    event.set()

    def _reconcile_blocked(self) -> None:
        with self.state.lock:
            issue_ids = list(self.state.blocked)
        if not issue_ids:
            return
        try:
            refreshed = {issue.id: issue for issue in self.linear_client.fetch_issues_by_ids(issue_ids)}
        except Exception:
            return
        for issue_id in issue_ids:
            issue = refreshed.get(issue_id)
            with self.state.lock:
                blocked = self.state.blocked.get(issue_id)
            if blocked is None:
                continue
            if issue is None or not self._eligible(issue):
                with self.state.lock:
                    self.state.blocked.pop(issue_id, None)
                if issue is not None and self._terminal(issue):
                    self._cleanup_terminal_workspace(issue)
                self._release(issue_id)
            elif issue.state != blocked.get("state"):
                with self.state.lock:
                    self.state.blocked.pop(issue_id, None)
                self._schedule_retry(issue, int(blocked.get("attempt", 0)) + 1, "state changed", delay=0)

    def _finish_cancelled(self, issue: Issue, entry: dict, reason: str) -> None:
        if reason == "terminal" and issue is not None:
            self._cleanup_terminal_workspace(issue)
        if issue is not None:
            completed = dict(entry)
            completed.update({"status": "cancelled", "completed_at": time.time(), "cancellation_reason": reason})
            with self.state.lock:
                self.state.completed[issue.id] = completed
            self.state.add_event("issue_cancelled", f"Cancelled {issue.identifier}: {reason}", issue.identifier)
            self._release(issue.id)

    def _complete(self, issue: Issue, entry: dict, result: dict) -> None:
        completed = dict(entry)
        completed.update(
            {
                "completed_at": time.time(),
                "session_id": result.get("session_id"),
                "turn_count": result.get("turn_count", 1),
                "status": "completed",
                "state": issue.state,
            }
        )
        with self.state.lock:
            self.state.completed[issue.id] = completed
        self._release(issue.id)
        self.state.add_event("issue_completed", f"Completed {issue.identifier}", issue.identifier)

    def _cleanup_terminal_workspace(self, issue: Issue) -> None:
        workspace = workspace_path(self.project_root, self.workflow, issue)
        try:
            remove_workspace(workspace, self.workflow, issue)
            self.state.add_event("workspace_removed", f"Removed workspace for {issue.identifier}", issue.identifier)
        except Exception as error:
            self.state.add_event("workspace_cleanup_failed", f"Workspace cleanup failed for {issue.identifier}: {error}", issue.identifier)

    def _release(self, issue_id: str) -> None:
        with self.state.lock:
            self.state.claimed.discard(issue_id)
            self.state.retrying.pop(issue_id, None)
            self._retry_issues.pop(issue_id, None)
            self._issues.pop(issue_id, None)

    def record_codex_event(self, issue: Issue, event: dict) -> None:
        with self.state.lock:
            running = self.state.running.get(issue.id)
            if running is not None:
                for field in ("session_id", "thread_id", "turn_id", "turn_count", "process_id"):
                    if field in event:
                        running[field] = event[field]
                running["last_event"] = event.get("type", "codex_event")
                running["last_event_at"] = time.time()
        self.state.add_event("codex_event", event.get("type", "codex_event"), issue.identifier, event=event)

    def _eligible(self, issue: Issue) -> bool:
        if self._normalize_state(issue.state) not in {self._normalize_state(state) for state in self.active_states()}:
            return False
        required_labels = {
            str(label).strip().lower()
            for label in self.workflow.config.get("tracker", {}).get("required_labels", [])
            if str(label).strip()
        }
        if required_labels and not required_labels.issubset({label.strip().lower() for label in issue.labels}):
            return False
        terminal = {self._normalize_state(state) for state in self.terminal_states()}
        return not any(self._normalize_state(blocker.state) not in terminal for blocker in issue.blocked_by)

    def _terminal(self, issue: Issue) -> bool:
        return self._normalize_state(issue.state) in {self._normalize_state(state) for state in self.terminal_states()}

    def active_states(self) -> list[str]:
        return self.workflow.config.get("tracker", {}).get("active_states") or ["Todo", "In Progress"]

    def terminal_states(self) -> list[str]:
        return self.workflow.config.get("tracker", {}).get("terminal_states") or [
            "Done",
            "Closed",
            "Cancelled",
            "Canceled",
            "Duplicate",
        ]

    def max_turns(self) -> int:
        return max(1, int(self.workflow.config.get("agent", {}).get("max_turns", 20)))

    def codex_approval_policy(self) -> str | dict:
        return self.workflow.config.get("codex", {}).get("approval_policy", "never")

    def codex_thread_sandbox(self) -> str:
        return self.workflow.config.get("codex", {}).get("thread_sandbox", "workspace-write")

    def codex_turn_sandbox_policy(self, workspace: Path) -> dict:
        policy = self.workflow.config.get("codex", {}).get("turn_sandbox_policy")
        if isinstance(policy, dict):
            return policy
        return {"type": "workspaceWrite", "writableRoots": [str(workspace)]}

    def _available_slots(self) -> int:
        with self.state.lock:
            return max(self.max_concurrent_agents - len(self._futures), 0)

    def _entry(self, issue: Issue, *, status: str, attempt: int = 0) -> dict:
        return {
            "issue_identifier": issue.identifier,
            "issue_id": issue.id,
            "title": issue.title,
            "state": issue.state,
            "workspace": str(workspace_path(self.project_root, self.workflow, issue)),
            "started_at": time.time(),
            "url": issue.url,
            "status": status,
            "attempt": attempt,
        }

    @staticmethod
    def _dispatch_sort_key(issue: Issue) -> tuple:
        priority = issue.priority if isinstance(issue.priority, int) and issue.priority > 0 else 999
        return priority, issue.created_at or "", issue.identifier

    @staticmethod
    def _normalize_state(state: str | None) -> str:
        return (state or "").strip().lower()
