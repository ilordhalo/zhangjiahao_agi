from __future__ import annotations

from pathlib import Path
import re

from symphonz.install import InstallConfig


def template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "WORKFLOW.md"


def render_workflow(template: str, config: InstallConfig) -> str:
    rendered = re.sub(
        r'project_slug: "[^"]*"',
        f'project_slug: "{config.linear_project_slug}"',
        template,
        count=1,
    )
    rendered = re.sub(
        r"api_key: \$[A-Z0-9_]+",
        f"api_key: ${config.linear_api_key_env}",
        rendered,
        count=1,
    )
    rendered = re.sub(
        r"workspace:\n  root: [^\n]+",
        f"workspace:\n  root: {config.workspace_root}",
        rendered,
        count=1,
    )
    rendered = re.sub(
        r'\$\{SYMPHONZ_REPO_URL:-[^}]+\}',
        "${SYMPHONZ_REPO_URL:-" + config.repo_url + "}",
        rendered,
        count=1,
    )
    return rendered


def write_workflow(project_root: Path, config: InstallConfig) -> Path:
    destination = project_root / ".symphonz" / "WORKFLOW.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_workflow(template_path().read_text(), config)
    destination.write_text(rendered)
    return destination
