# Symphonz Install Design

Date: 2026-07-08
Status: approved for initial documentation pass

## Goal

`symphonz` is a command line installer and launcher that makes the Symphony orchestration workflow portable across arbitrary Git projects.

The target user experience is:

```bash
symphonz install
symphonz run
```

After installation, the current project owns a `.symphonz` directory containing the workflow contract, runtime metadata, workspace root, logs, and either an embedded Symphony runtime or a pointer to a global one.

## Selected Approach

Use the hybrid runtime model:

- Default: embedded runtime. `symphonz install` downloads and builds Symphony into `.symphonz/runtime`, then exposes `.symphonz/bin/symphony`.
- Optional: global runtime. `symphonz install --runtime global` writes only config, workflow, workspace, and logs, then expects a global `symphony` command or configured runtime path.

This keeps first-time setup self-contained while allowing teams with managed developer machines to avoid repeated runtime copies.

## Installed Layout

Embedded mode:

```text
.symphonz/
  WORKFLOW.md
  config.toml
  bin/
    symphony
  runtime/
    symphony/
  workspace/
    <issue_identifier>/
  logs/
```

Global mode:

```text
.symphonz/
  WORKFLOW.md
  config.toml
  workspace/
    <issue_identifier>/
  logs/
```

The `workspace` directory is intentionally under `.symphonz` so every Linear issue gets a deterministic isolated project copy at `.symphonz/workspace/<issue_identifier>`.

## Install Flow

`symphonz install` runs from the target project root and performs these steps:

1. Verify the current directory is a Git repository.
2. Detect the primary remote URL and default branch.
3. Prompt for required orchestration inputs:
   - Linear API key environment variable name, default `LINEAR_API_KEY`.
   - Linear project slug or project ID.
   - Git provider, default `gitlab`.
   - Git remote URL.
   - GitLab base URL, default `https://zhangjiahao.me:9011` for this environment.
   - Base branch and merge request target branch, default `main`.
   - Runtime mode, default `embedded`.
4. Create `.symphonz` directories.
5. Generate `.symphonz/config.toml`.
6. Render `.symphonz/WORKFLOW.md` from this repository's default workflow template.
7. In embedded mode, download/build Symphony under `.symphonz/runtime/symphony` and create `.symphonz/bin/symphony`.
8. Add recommended ignore rules for `.symphonz/workspace`, `.symphonz/logs`, and `.symphonz/runtime`.
9. Print the launch command: `symphonz run`.

## Configuration Contract

Example `.symphonz/config.toml`:

```toml
[runtime]
mode = "embedded"
command = ".symphonz/bin/symphony"

[linear]
api_key_env = "LINEAR_API_KEY"
project_slug = "zhangjiahao-agi-186a15c896ac"

[git]
provider = "gitlab"
remote = "https://github.com/ilordhalo/zhangjiahao_agi.git"
base_branch = "main"
mr_target = "main"
gitlab_base_url = "https://zhangjiahao.me:9011"

[workspace]
root = ".symphonz/workspace"

[logs]
root = ".symphonz/logs"
```

`symphonz run` reads this file, exports the matching environment variables, and starts the runtime with `.symphonz/WORKFLOW.md`.

## Runtime Behavior

`symphonz run` starts Symphony in one of two ways.

Embedded mode:

```bash
.symphonz/bin/symphony .symphonz/WORKFLOW.md --logs-root .symphonz/logs
```

Global mode:

```bash
symphony .symphonz/WORKFLOW.md --logs-root .symphonz/logs
```

Before launching, `symphonz run` exports at least:

- `LINEAR_API_KEY` or the configured Linear API key variable.
- `SYMPHONZ_REPO_URL`.
- `SYMPHONZ_BASE_BRANCH`.
- `SYMPHONZ_MR_TARGET`.
- `SYMPHONZ_GIT_PROVIDER`.
- `GITLAB_BASE_URL`.

## Issue Lifecycle

The workflow treats Linear states as orchestration signals:

- `Todo`: claim issue and move to `In Progress`.
- `In Progress`: implement and validate in `.symphonz/workspace/<issue_identifier>`.
- `Done`: publish trigger. Push the issue branch, create/update a GitLab merge request, attach it to Linear, then move to `Human Review`.
- `Human Review`: wait for review, pipeline, and approval.
- `Rework`: address review feedback and return to `Human Review`.
- `Merging`: merge the GitLab merge request after validation and green pipeline, then move Linear to `Closed`.
- `Closed`, `Cancelled`, `Canceled`, `Duplicate`: terminal states.

This differs from the upstream Symphony example: `Done` is deliberately not terminal for `symphonz` because the requested behavior is to publish workspace changes when an issue is marked done.

## Branch and Merge Request Policy

Each issue uses one deterministic branch:

```text
symphonz/<issue_identifier>-<short-title-slug>
```

The branch starts from `origin/main` unless configured otherwise. The merge request targets `main` by default. The MR title is:

```text
<issue_identifier>: <issue title>
```

The MR description includes:

- Linear issue URL.
- Summary of changes.
- Validation commands and outcomes.
- Known blockers or follow-up issues.

## WORKFLOW.md Changes

The root `WORKFLOW.md` in this repository is now the default `symphonz` workflow template. The installer should render it into `.symphonz/WORKFLOW.md` for target projects.

Key changes from the previous workflow:

- Workspace root moved to `.symphonz/workspace`.
- `after_create` clones the configured target repo, not the OpenAI Symphony example.
- GitLab MR publication is first-class.
- `Done` is an active publish trigger, not a terminal state.
- The workpad is renamed to `## Symphonz Workpad`.
- The prompt documents the installed runtime context and expected environment variables.

## Risks and Open Implementation Details

- Current Symphony hook commands do not receive issue fields as environment variables. Branch creation therefore belongs in the agent prompt, not `after_create`.
- GitLab support depends on either `glab` or `GITLAB_TOKEN` plus API implementation.
- Embedded runtime setup should prefer release artifacts when available; source clone/build is the fallback.
- The CLI should make install idempotent: existing `.symphonz/config.toml` and `.symphonz/WORKFLOW.md` should be backed up or updated deliberately, not overwritten silently.
- Secrets should be referenced by environment variable name instead of written directly to `.symphonz/config.toml`.

## Acceptance Criteria

- `symphonz install` creates `.symphonz` with workflow, config, workspace, logs, and runtime assets according to the selected runtime mode.
- `symphonz install --runtime global` skips runtime download/build and records global command usage.
- Generated `WORKFLOW.md` uses the target project's repo, Linear project, workspace root, and GitLab settings.
- Each active Linear issue maps to `.symphonz/workspace/<issue_identifier>`.
- Marking an issue `Done` causes the workflow to publish a branch and create/update a GitLab merge request instead of treating the issue as terminal.
- Terminal states cleanly stop orchestration and allow workspace cleanup.

## Spec Self-Review

- No placeholder values are required for the design to be understandable; concrete defaults are documented.
- The `Done` state behavior is intentionally different from upstream Symphony and is called out in both workflow and design.
- The hook limitation is explicitly documented so implementation does not rely on unavailable issue environment variables.
- Scope is limited to installer, runtime selection, workflow generation, workspace convention, and GitLab/Linear lifecycle.
