from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess

from symphonz.service.models import Issue, WorkflowDefinition


def safe_identifier(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", identifier or "issue")


def prepare_workspace(project_root: Path, workflow: WorkflowDefinition, issue: Issue) -> Path:
    workspace_root = Path(workflow.config.get("workspace", {}).get("root", ".symphonz/workspace"))
    if not workspace_root.is_absolute():
        workspace_root = project_root / workspace_root
    workspace = workspace_root / safe_identifier(issue.identifier)
    created = not workspace.exists()
    workspace.mkdir(parents=True, exist_ok=True)
    if created:
        run_after_create_hook(workspace, workflow, issue)
    return workspace


def run_after_create_hook(workspace: Path, workflow: WorkflowDefinition, issue: Issue) -> None:
    hook = workflow.config.get("hooks", {}).get("after_create")
    if not hook:
        return
    env = os.environ.copy()
    env.update(
        {
            "SYMPHONZ_ISSUE_ID": issue.id,
            "SYMPHONZ_ISSUE_IDENTIFIER": issue.identifier,
            "SYMPHONZ_ISSUE_TITLE": issue.title,
            "SYMPHONZ_ISSUE_STATE": issue.state or "",
            "SYMPHONZ_ISSUE_URL": issue.url or "",
        }
    )
    subprocess.run(hook, cwd=workspace, shell=True, check=True, env=env)

