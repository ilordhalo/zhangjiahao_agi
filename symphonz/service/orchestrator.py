from __future__ import annotations

from pathlib import Path
import time

from symphonz.service.codex_app_server import CodexAppServer
from symphonz.service.linear import LinearClient
from symphonz.service.models import Issue, RuntimeState, WorkflowDefinition
from symphonz.service.workflow import render_prompt
from symphonz.service.workspace import prepare_workspace


class Orchestrator:
    def __init__(
        self,
        project_root: Path,
        workflow: WorkflowDefinition,
        linear_client: LinearClient,
        codex_client: CodexAppServer,
        state: RuntimeState | None = None,
    ):
        self.project_root = project_root
        self.workflow = workflow
        self.linear_client = linear_client
        self.codex_client = codex_client
        self.state = state or RuntimeState()

    def poll_once(self) -> None:
        tracker = self.workflow.config.get("tracker", {})
        active_states = tracker.get("active_states") or ["Todo", "In Progress"]
        required_labels = [str(label).strip().lower() for label in tracker.get("required_labels", []) if str(label).strip()]
        issues = self.linear_client.fetch_candidate_issues(active_states)
        for issue in issues:
            if not self.issue_matches_required_labels(issue, required_labels):
                continue
            if issue.identifier in self.state.running:
                continue
            self.run_issue(issue)

    def run_issue(self, issue: Issue) -> None:
        workspace = prepare_workspace(self.project_root, self.workflow, issue)
        prompt = render_prompt(self.workflow.prompt_template, issue)
        entry = {
            "issue_identifier": issue.identifier,
            "issue_id": issue.id,
            "title": issue.title,
            "state": issue.state,
            "workspace": str(workspace),
            "started_at": time.time(),
            "url": issue.url,
        }
        self.state.running[issue.identifier] = entry
        self.state.add_event("issue_started", f"Started {issue.identifier}", issue.identifier, workspace=str(workspace))

        try:
            result = self.codex_client.run_turn(
                workspace=workspace,
                prompt=prompt,
                title=f"{issue.identifier}: {issue.title}",
                approval_policy=self.codex_approval_policy(),
                thread_sandbox=self.codex_thread_sandbox(),
                turn_sandbox_policy=self.codex_turn_sandbox_policy(workspace),
                on_event=lambda event: self.record_codex_event(issue, event),
            )
            completed = self.state.running.pop(issue.identifier)
            completed.update(
                {
                    "completed_at": time.time(),
                    "session_id": result.get("session_id"),
                    "status": "completed",
                }
            )
            self.state.completed[issue.identifier] = completed
            self.state.add_event("issue_completed", f"Completed {issue.identifier}", issue.identifier)
        except Exception as error:
            failed = self.state.running.pop(issue.identifier, entry)
            failed.update({"blocked_at": time.time(), "error": str(error), "status": "blocked"})
            self.state.blocked[issue.identifier] = failed
            self.state.add_event("issue_blocked", f"Blocked {issue.identifier}: {error}", issue.identifier)

    def record_codex_event(self, issue: Issue, event: dict) -> None:
        self.state.add_event("codex_event", event.get("type", "codex_event"), issue.identifier, event=event)

    def issue_matches_required_labels(self, issue: Issue, required_labels: list[str]) -> bool:
        if not required_labels:
            return True
        labels = set(label.strip().lower() for label in issue.labels)
        return all(label in labels for label in required_labels)

    def codex_approval_policy(self) -> str | dict:
        return self.workflow.config.get("codex", {}).get("approval_policy", "never")

    def codex_thread_sandbox(self) -> str:
        return self.workflow.config.get("codex", {}).get("thread_sandbox", "workspace-write")

    def codex_turn_sandbox_policy(self, workspace: Path) -> dict:
        policy = self.workflow.config.get("codex", {}).get("turn_sandbox_policy")
        if isinstance(policy, dict):
            return policy
        return {"type": "workspaceWrite", "writableRoots": [str(workspace)]}

