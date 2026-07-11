from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys

from symphonz.service.models import Issue, WorkflowDefinition


def safe_identifier(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", identifier or "issue")


def workspace_path(project_root: Path, workflow: WorkflowDefinition, issue: Issue) -> Path:
    workspace_root = Path(workflow.config.get("workspace", {}).get("root", ".symphonz/workspace"))
    if not workspace_root.is_absolute():
        workspace_root = project_root / workspace_root
    return workspace_root / safe_identifier(issue.identifier)


def prepare_workspace(project_root: Path, workflow: WorkflowDefinition, issue: Issue) -> Path:
    workspace = workspace_path(project_root, workflow, issue)
    _validate_workspace_containment(workspace)
    created = not os.path.lexists(workspace)
    if created:
        workspace.mkdir(parents=True, exist_ok=False)
    elif not workspace.is_dir():
        raise RuntimeError(f"Workspace path is not a directory: {workspace}")
    if created:
        try:
            run_after_create_hook(workspace, workflow, issue)
        except Exception:
            _cleanup_partial_workspace(workspace)
            raise
    return workspace


def run_after_create_hook(workspace: Path, workflow: WorkflowDefinition, issue: Issue) -> None:
    _run_hook("after_create", workspace, workflow, issue, fatal=True)


def run_before_run_hook(workspace: Path, workflow: WorkflowDefinition, issue: Issue) -> None:
    _run_hook("before_run", workspace, workflow, issue, fatal=True)


def run_after_run_hook(workspace: Path, workflow: WorkflowDefinition, issue: Issue) -> None:
    _run_hook("after_run", workspace, workflow, issue, fatal=False)


def remove_workspace(workspace: Path, workflow: WorkflowDefinition, issue: Issue) -> None:
    _run_hook("before_remove", workspace, workflow, issue, fatal=False)
    if not os.path.lexists(workspace):
        return
    canonical_workspace = _validate_workspace_containment(workspace)
    workspace_is_symlink = workspace.is_symlink()
    try:
        shutil.rmtree(canonical_workspace)
    except FileNotFoundError:
        pass
    if workspace_is_symlink:
        try:
            workspace.unlink()
        except FileNotFoundError:
            pass


def _cleanup_partial_workspace(workspace: Path) -> None:
    try:
        remove_workspace(workspace, WorkflowDefinition(path=workspace, config={}, prompt_template=""), Issue("", "", ""))
    except Exception:
        # Cleanup is best-effort here; preserve the original hook failure.
        pass


def _run_hook(name: str, workspace: Path, workflow: WorkflowDefinition, issue: Issue, *, fatal: bool) -> None:
    hook = workflow.config.get("hooks", {}).get(name)
    if not hook:
        return
    _validate_workspace_containment(workspace)
    try:
        subprocess.run(
            hook,
            cwd=workspace,
            shell=True,
            check=True,
            env=_hook_env(issue, workspace),
            timeout=_hook_timeout_seconds(workflow),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        if fatal:
            raise
        print(f"Ignoring {name} hook failure for {workspace}: {error}", file=sys.stderr)


def _hook_timeout_seconds(workflow: WorkflowDefinition) -> float:
    timeout_ms = workflow.config.get("hooks", {}).get("timeout_ms", 60000)
    return max(float(timeout_ms), 1.0) / 1000.0


def _hook_env(issue: Issue, workspace: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "SYMPHONZ_ISSUE_ID": issue.id,
            "SYMPHONZ_ISSUE_IDENTIFIER": issue.identifier,
            "SYMPHONZ_ISSUE_TITLE": issue.title,
            "SYMPHONZ_ISSUE_STATE": issue.state or "",
            "SYMPHONZ_ISSUE_URL": issue.url or "",
            "SYMPHONZ_WORKSPACE": str(workspace),
        }
    )
    return env


def _validate_workspace_containment(workspace: Path) -> Path:
    workspace_root = workspace.parent
    canonical_root = workspace_root.resolve(strict=False)
    canonical_workspace = workspace.resolve(strict=False)
    try:
        canonical_workspace.relative_to(canonical_root)
    except ValueError as error:
        raise RuntimeError(f"Workspace path escapes root: {workspace}") from error
    return canonical_workspace
