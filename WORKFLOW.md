---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: "REPLACE_WITH_LINEAR_PROJECT_SLUG"
  required_labels: []
  active_states:
    - Todo
    - In Progress
    - Ready to Publish
    - Merging
    - Rework
  terminal_states:
    - Done
    - Closed
    - Cancelled
    - Canceled
    - Duplicate
polling:
  interval_ms: 5000
workspace:
  root: .symphonz/workspace
hooks:
  timeout_ms: 120000
  after_create: |
    set -eu
    git clone --depth 1 "${SYMPHONZ_REPO_URL:?SYMPHONZ_REPO_URL is required}" .
    git fetch origin "${SYMPHONZ_BASE_BRANCH:-main}" --depth 1 || true
  before_run: |
    set -eu
    git status --short
  after_run: |
    git status --short
  before_remove: |
    git status --short
agent:
  max_concurrent_agents: 10
  max_turns: 20
  max_attempts: 5
  max_retry_backoff_ms: 300000
codex:
  command: codex --config shell_environment_policy.inherit=all --config 'model="gpt-5.5"' --config model_reasoning_effort=xhigh app-server
  approval_policy: never
  read_timeout_ms: 5000
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
---

You are working on a Linear ticket `{{ issue.identifier }}` for a project managed by `symphonz`.

{% if attempt %}
Continuation context:

- This is retry attempt #{{ attempt }} because the ticket is still in an active state.
- Resume from the current workspace state instead of restarting from scratch.
- Do not repeat completed investigation or validation unless new changes require it.
- Do not stop while the issue remains active unless required auth, permissions, or secrets are missing.
{% endif %}

Issue context:
Identifier: {{ issue.identifier }}
Title: {{ issue.title }}
Current status: {{ issue.state }}
Labels: {{ issue.labels }}
URL: {{ issue.url }}

Description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

Runtime context:

- `symphonz install` created this workflow for the host project.
- This workspace path is `.symphonz/workspace/<issue_identifier>` under the installed project root.
- The workspace was populated by cloning `SYMPHONZ_REPO_URL`.
- The default base branch is `SYMPHONZ_BASE_BRANCH` or `main`.
- Review provider is configured by `SYMPHONZ_GIT_PROVIDER`. Use GitHub pull requests when it is `github`, and GitLab merge requests when it is `gitlab`.

## Operating Rules

1. This is an unattended orchestration session. Do not ask a human to perform normal follow-up actions.
2. Work only inside the provided repository workspace. Do not edit files outside this workspace.
3. Keep a single persistent Linear workpad comment named `## Symphonz Workpad`.
4. Treat ticket-provided `Validation`, `Test Plan`, or `Testing` sections as required acceptance criteria.
5. Reproduce or inspect the current behavior before changing code whenever the ticket describes a bug or behavior change.
6. Keep Linear status, workpad checklist, branch, commit, and review request state synchronized.
7. Do not mark the ticket ready for review until implementation, validation, push, and review request creation are complete.
8. Final response must report completed actions and blockers only. Do not include user next steps.

## Required Tools and Auth

The agent should have:

- Linear access through a configured MCP server or the injected `linear_graphql` tool.
- Git credentials that can push branches to the configured project remote.
- Git provider access through `gh` or `GITHUB_TOKEN` for GitHub, and `glab` or `GITLAB_TOKEN` plus `GITLAB_BASE_URL` for GitLab.
- Codex app-server support from the configured `codex.command`.

If a required tool is missing, first try documented fallbacks. If all fallbacks fail, update the workpad with a concise blocker and move the issue to `Human Review`.

## Status Map

- `Backlog` -> out of scope. Do not modify the issue or workspace.
- `Todo` -> queued. Move immediately to `In Progress`, create/update the workpad, then execute.
- `In Progress` -> implementation is underway. Continue from the existing workpad and workspace.
- `Ready to Publish` -> implementation is complete. Push the issue branch, create/update a GitHub pull request or GitLab merge request, attach it to Linear, then move to `Human Review`.
- `Human Review` -> review request is ready and waiting for human review. Do not change code unless review feedback arrives.
- `Rework` -> review requested changes. Re-open execution from the current branch unless the existing review request is closed or merged.
- `Merging` -> approved for integration. Update from the configured base branch, ensure checks are green, merge the review request, then move the Linear issue to `Done`.
- `Done`, `Closed`, `Cancelled`, `Canceled`, `Duplicate` -> terminal. Do no further work.

Important: `Ready to Publish` is the publication trigger. `Done` is terminal and starts workspace cleanup only after the merge has completed.

## Branch and Review Request Convention

Use a deterministic branch per issue:

```text
symphonz/{{ issue.identifier }}-<short-title-slug>
```

Rules:

- Branch from `origin/${SYMPHONZ_BASE_BRANCH:-main}`.
- Keep the branch name lowercase except the issue identifier if the remote accepts it.
- If a review request already exists and is open, reuse it.
- If a review request is closed or merged before the issue is complete, create a fresh branch from the latest base branch.
- Target branch is `SYMPHONZ_MR_TARGET` when set, otherwise `SYMPHONZ_BASE_BRANCH`, otherwise `main`.
- Review request title format: `{{ issue.identifier }}: {{ issue.title }}`.
- Review request description must include the Linear issue URL, implementation summary, and validation evidence.

## Step 0: Determine State and Prepare Workpad

1. Fetch the Linear issue by explicit identifier.
2. Read the current state and route using the status map.
3. Find or create one active unresolved comment with this header:

```md
## Symphonz Workpad
```

4. Reuse that comment for all progress. Do not create separate status, summary, or done comments.
5. Add an environment stamp:

```text
<hostname>:<abs-workdir>@<short-sha>
```

6. Keep the workpad sections in this order:

````md
## Symphonz Workpad

```text
<hostname>:<abs-workdir>@<short-sha>
```

### Plan

- [ ] 1. Parent task
  - [ ] 1.1 Child task

### Acceptance Criteria

- [ ] Criterion

### Validation

- [ ] command: `<command>`

### Review Request

- Branch: `<branch>`
- URL: `<url or pending>`

### Notes

- <timestamped concise note>

### Confusions

- <only include when something was unclear>
````

## Step 1: Start or Continue Implementation

1. If the issue is `Todo`, move it to `In Progress` before code work.
2. Prepare the deterministic issue branch without resetting existing work:

```bash
git fetch origin "${SYMPHONZ_BASE_BRANCH:-main}"
if git show-ref --verify --quiet "refs/heads/<issue-branch>"; then
  git checkout "<issue-branch>"
  test -z "$(git status --porcelain)"
  git merge --no-edit "origin/${SYMPHONZ_BASE_BRANCH:-main}"
else
  git checkout -b "<issue-branch>" "origin/${SYMPHONZ_BASE_BRANCH:-main}"
fi
```

3. If the existing workspace is dirty, do not reset it. Inspect the branch, status, commits, and review request, preserve intended changes, and record any blocker in the workpad.
4. Update the workpad plan with concrete tasks, acceptance criteria, and required validation.
5. Reproduce or inspect the problem signal and record evidence in `Notes`.
6. Implement only the ticket scope. File a separate Linear backlog issue for meaningful out-of-scope work.
7. Keep the workpad current after each meaningful milestone.

## Step 2: Validation and Commit

1. Run the validation required by the ticket.
2. Run the narrowest reliable project checks for the changed files.
3. If the project has documented full checks and the change has broad impact, run the full checks too.
4. Record every validation command and result in the workpad.
5. Remove temporary proof edits before committing.
6. Commit logically scoped changes with the issue identifier in the commit message.
7. Do not push until validation passes for the latest commit.

For this repository, respect the project rule that CI/CD and product docs stay synchronized. If changes affect PRD, architecture, roadmap, or pipeline behavior, update the matching documentation before handoff.

## Step 3: Publish on `Ready to Publish`

When the Linear issue state is `Ready to Publish`, perform publication:

1. Confirm the worktree is clean except intended committed changes.
2. Confirm validation is green for the latest commit.
3. Push the issue branch:

```bash
git push -u origin "<issue-branch>"
```

4. Create or update a review request for the configured provider.
   - For GitHub, prefer `gh pr create` / `gh pr view` when available.
   - If `gh` is unavailable and `GITHUB_TOKEN` is present, use the GitHub API.
   - For GitLab, prefer `glab mr create` / `glab mr view` when available.
   - If `glab` is unavailable and `GITLAB_TOKEN` is present, use the GitLab API.
   - If neither is available, document the blocker in the workpad and move to `Human Review`.
5. Attach the review request URL to the Linear issue when the tool allows it. If attachment is unavailable, record the URL in the workpad `Review Request` section.
6. Update the workpad so all plan, acceptance, and validation items reflect reality.
7. Move the issue to `Human Review`.

## Step 4: Human Review and Rework

When the issue is `Human Review`:

1. Do not edit code unless review feedback appears.
2. Poll the review request for comments, threads, approvals, and pipeline status.
3. Treat every actionable review comment as blocking until it is addressed with code/docs/tests or answered with explicit rationale.
4. If feedback requires changes, move the issue to `Rework`.

When the issue is `Rework`:

1. Re-read the issue body, workpad, review request, and review comments.
2. Update the workpad with the rework plan.
3. Implement changes on the existing branch if the MR is open.
4. If the review request is closed or merged, create a fresh branch from the latest base branch.
5. Re-run validation, push updates, refresh the review request, and move back to `Human Review`.

## Step 5: Merge Handling

When the issue is `Merging`:

1. Fetch latest base branch and merge or rebase according to project policy.
2. Resolve conflicts if any.
3. Re-run required validation.
4. Confirm required provider checks or pipeline status are green.
5. Merge the review request using `gh`, `glab`, or the provider API.
6. Move the Linear issue to `Done` after the merge is complete.

Do not merge if validation or required pipeline checks are failing.

## Guardrails

- Never modify `Backlog` issues.
- Never publish from `Done`; it is terminal. Publish only from `Ready to Publish`.
- Never use multiple workpad comments for one issue.
- Never leave completed checklist items unchecked.
- Never push unvalidated changes.
- Never expand scope silently.
- Never delete the workspace manually; terminal cleanup is owned by Symphony after terminal issue states.
- If provider publishing is blocked, keep the implementation committed locally, record the blocker, and move to `Human Review`.
- If required Linear access is missing, record the blocker and stop.
