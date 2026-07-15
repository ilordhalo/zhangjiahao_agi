# Symphonz Runtime Hardening Design

## Objective

Upgrade the built-in Python runtime from a single-turn polling MVP to a reliable,
portable issue orchestrator while keeping the one-command installation model and
GitHub/GitLab provider support. The release version is `0.3.0`.

## Decisions

Three implementation directions were evaluated:

1. Reuse the OpenAI Elixir runtime. This has the strongest reference behavior but
   reintroduces Elixir/Erlang/mise, which conflicts with Symphonz portability.
2. Keep the synchronous Python loop and add isolated patches. This is small but
   cannot provide reconciliation, bounded concurrency, or cancellation safely.
3. Harden the embedded Python runtime with a single-authority scheduler and
   standard-library worker threads. This preserves installation simplicity while
   implementing the important Symphony coordination contracts.

Option 3 is selected. Python threads are appropriate because workers mostly wait
on subprocess and HTTP I/O. The Orchestrator remains the only scheduling authority;
worker threads report outcomes and never dispatch other work.

## Lifecycle Policy

Symphonz will stop overloading `Done` as a publication command. The generated
workflow uses:

```text
Todo -> In Progress -> Ready to Publish -> Human Review
     -> Rework -> Human Review -> Merging -> Done
```

`Done`, `Closed`, `Cancelled`, `Canceled`, and `Duplicate` are terminal. Projects
that prefer a different publication-state name can edit `active_states` and the
prompt, but generated installs use `Ready to Publish`. Rework reuses an open branch
and review request by default, preserving the existing Symphonz product decision;
closed or merged review requests require a fresh branch.

## Runtime Architecture

### Orchestrator

The Orchestrator owns `claimed`, `running`, `retrying`, `completed`, and `blocked`
state behind a lock. Each tick performs:

1. Collect completed worker futures and convert outcomes to completion or retry.
2. Refresh running issue states and cancel workers that are no longer eligible.
3. Remove terminal workspaces after cancellation/worker exit.
4. Dispatch due retries while slots are available.
5. Fetch, normalize, prioritize, and dispatch new candidate issues.

The configured `agent.max_concurrent_agents` bounds a `ThreadPoolExecutor`.
Failures use exponential backoff starting at 10 seconds and capped by
`agent.max_retry_backoff_ms` (default 300 seconds). Clean completion while an issue
is still active schedules a one-second continuation retry. A blocked/input-required
outcome stays claimed and is not retried until restart or a state change.

### Codex Session

One worker starts one app-server process and one thread. The first turn receives
the rendered workflow prompt. If Linear still reports an eligible active issue,
continuation turns use the same thread and a compact continuation instruction, up
to `agent.max_turns`.

The app-server transport uses a reader thread and bounded queue so startup reads,
turn duration, stall duration, cancellation, and subprocess exit are observable.
Defaults are `read_timeout_ms=5000`, `turn_timeout_ms=3600000`, and
`stall_timeout_ms=300000`.

### Linear Dynamic Tool

The runtime advertises `linear_graphql` in `thread/start.dynamicTools`. Calls arrive
as `item/tool/call`; the runtime validates exactly one non-empty GraphQL operation,
requires an object for variables, executes it through the configured `LinearClient`,
and returns structured `success`, `output`, and `contentItems` fields. Unsupported
tools return a failure response rather than stalling the turn.

This is an additional guaranteed path. Existing global Linear MCP and shell/API
fallbacks remain usable.

### Linear Adapter

Candidate and state queries paginate in pages of 50 using `pageInfo`. Results keep
API order and are sorted for dispatch by priority, then creation time and identifier.
The normalized issue model includes `blocked_by`; issues with non-terminal blockers
are not dispatched.

### Workspace Lifecycle

The workspace manager supports:

- `after_create`: fatal on failure for a newly created directory.
- `before_run`: fatal for the current attempt.
- `after_run`: logged and ignored after every attempt.
- `before_remove`: logged and ignored before terminal cleanup.
- `hooks.timeout_ms`: default 60000.

Canonical paths must remain below the canonical workspace root, including symlink
resolution. Terminal cleanup is idempotent. A failed `after_create` removes only the
new partial workspace.

## Configuration and Installation

`WORKFLOW.md` remains the runtime source of truth. `.symphonz/config.toml` remains
installation metadata for repository/provider settings and command launch, but it
does not duplicate tracker runtime behavior beyond values used to render the
workflow.

The CLI adds non-interactive install flags:

```text
--linear-project
--linear-api-key-env
--git-provider
--repo-url
--base-branch
--target-branch
--gitlab-base-url
```

`--yes` consumes flags or environment values and fails with a field-specific error
only when a required value cannot be detected. Interactive installation continues
to request an environment-variable name rather than writing a secret into the
project. Installation finishes with a Linear read preflight when the referenced key
is available; otherwise it prints the exact export command without persisting the
token.

The shell installer supports `SYMPHONZ_REF` for version pinning, installs atomically
through a staging directory, and documents the trust boundary of `curl | sh`.

## Workflow Safety

Branch preparation distinguishes first run from continuation. It never uses
`git checkout -B` on an existing issue branch. The generated workflow checks for a
dirty tree and unpushed commits before base synchronization. PR/MR publishing,
feedback handling, and merging remain provider-neutral.

The documented trust posture is a trusted developer environment with
`approval_policy: never`, workspace-write filesystem access, network access, and
inherited host credentials.

## Observability

Every runtime event is appended as JSON Lines under
`.symphonz/logs/runtime.jsonl` while remaining available to the dashboard. Snapshot
state includes claimed count, turn count, retry attempt/due time, cancellation
reason, last event, and session identifiers. Logging failures are surfaced to
stderr but do not stop scheduling.

## Validation Strategy

Tests use deterministic fake Linear and app-server implementations. Required
coverage includes:

- two simultaneous issues respecting the concurrency bound;
- blocked issues not immediately redispatched;
- exponential retry and clean continuation scheduling;
- active-state reconciliation and terminal cancellation/cleanup;
- same-thread continuation turns and `max_turns`;
- startup, turn, stall, and cancellation behavior;
- valid/invalid/unsupported dynamic tool calls;
- paginated Linear queries and blocker normalization;
- all workspace hooks, hook timeouts, and symlink escapes;
- non-interactive install flags and environment fallbacks;
- a stateful lifecycle simulation through publication, review, rework, merge, and
  terminal cleanup using fake Linear and provider records.

The existing 36 tests remain green or are intentionally updated for the new
`0.3.0` lifecycle contract.

## Out of Scope

- Durable retry/session recovery across process restart.
- Distributed or SSH workers.
- A general provider API inside the orchestrator; GitHub/GitLab actions remain
  coding-agent responsibilities defined by the workflow.
- Automatic credential storage in the project.
