# Symphonz Task Reports, Runtime History, and LAN Authentication Design

Date: 2026-07-16
Status: approved for implementation
Target release: 0.4.0

## Summary

Symphonz 0.4.0 will turn the current live-only dashboard into a persistent task operations surface. Each Codex task will publish a structured implementation report that Symphonz renders as safe, branded HTML. The report, merge request, execution history, and errors remain available after the issue workspace is removed. Symphonz will place the report URL in Linear automatically.

The dashboard is intended for a trusted LAN in this release. It may bind to a LAN interface, but public Internet TLS termination, account registration, multiple users, password recovery, and authorization roles are out of scope.

## Goals

- Give reviewers a coherent implementation narrative in addition to a raw Git diff.
- Generate a stable HTML report URL for every completed implementation.
- Add the report URL and merge request URL to Linear without relying on a human.
- Show current and historical task execution, milestones, sessions, reports, and errors on port 4000.
- Persist task history and reports across service restarts and workspace cleanup.
- Store errors separately in a queryable database and `.symphonz/logs/errors.jsonl`.
- Protect all task and report pages with one configured LAN user and persistent sessions.
- Preserve the standard-library-only runtime and existing GitHub/GitLab behavior.

## Non-Goals

- Exposing private model chain-of-thought. Reports contain concise engineering rationale, alternatives, evidence, and decisions only.
- Hosting AI-authored arbitrary HTML or JavaScript.
- Public Internet deployment, certificate issuance, TLS renewal, SSO, OAuth, registration, roles, or password recovery.
- Replacing GitHub pull requests or GitLab merge requests as the code review authority.
- Building a general Markdown or Mermaid implementation.

## Considered Approaches

### AI-authored HTML

Codex could write a self-contained HTML file into the issue workspace. This is rejected because issue text can influence generated scripts, same-origin JavaScript could access authenticated dashboard data, presentation would drift between tasks, and the file would disappear with workspace cleanup.

### AI-authored Markdown

Codex could write Markdown and Symphonz could convert it to HTML. This improves safety but still requires a non-trivial Markdown parser, produces inconsistent information architecture, and cannot reliably validate that required report sections exist.

### Structured report tool and deterministic renderer

This is the selected approach. Symphonz advertises a `symphonz_report` Codex dynamic tool. Codex submits validated JSON fields. Symphonz persists the report outside the workspace and renders escaped, deterministic HTML with a shared design system. The same publish operation records the merge request and queues Linear synchronization.

## System Architecture

```text
Linear issue
    |
    v
Orchestrator ---- lifecycle events ----> RuntimeStore (SQLite)
    |                                      |       |
    |                                      |       +--> errors.jsonl
    v                                      v
Codex app-server                    Dashboard / API
    |                                      |
    +--> linear_graphql                    +--> task detail and timeline
    |
    +--> symphonz_report --> ReportPublisher --> versioned JSON + rendered HTML bundle
                                  |
                                  +--> pending Linear report comment synchronization
```

The runtime owns report storage, rendering, URL generation, and Linear synchronization. Codex supplies report content but never supplies executable HTML.

## Installed Layout

```text
.symphonz/
  WORKFLOW.md
  config.toml
  auth.toml
  artifacts/
    ZHA-9/
      report-<generation>.json
      report-<generation>.html
  logs/
    attempts.sqlite3
    runtime.sqlite3
    runtime.jsonl
    errors.jsonl
  workspace/
    ZHA-9/
```

`.symphonz/auth.toml`, `.symphonz/artifacts/`, `.symphonz/logs/`, and `.symphonz/workspace/` are ignored by Git. Auth I/O first pins `.symphonz` as a non-symlink directory file descriptor. Temporary creation, destination checks, reads, replacement, cleanup, and directory sync then use relative `dir_fd` operations so a parent-path swap cannot redirect them. `auth.toml` is atomically replaced from an exclusive same-directory `0600` temporary file after file and directory sync. Reads require a regular non-symlink file with exact mode `0600`; malformed or unsupported records fail closed with a configuration-regeneration error. Reports survive terminal workspace cleanup.

## Configuration

Non-secret dashboard settings live in `.symphonz/config.toml`:

```toml
[dashboard]
host = "0.0.0.0"
port = "4000"
public_base_url = "http://192.168.1.20:4000"
username = "admin"
session_days = "30"
```

Secret authentication material lives in `.symphonz/auth.toml`:

```toml
[auth]
algorithm = "scrypt-v1"
salt = "<base64>"
password_hash = "<base64>"
session_secret = "<base64>"
```

The installer asks for the dashboard host, port, LAN base URL, username, password, and session duration. Password input uses `getpass` and is never echoed. Non-interactive installation accepts `SYMPHONZ_DASHBOARD_*` values and requires the password through `SYMPHONZ_DASHBOARD_PASSWORD` when authentication is enabled.

Existing projects use:

```bash
symphonz configure-dashboard
```

This command updates only `[dashboard]`, writes `auth.toml`, and updates `.gitignore`. It does not regenerate `WORKFLOW.md` or modify Linear and Git settings.

`symphonz run` uses configured host and port. `--host` and `--port` are temporary process overrides. A report link always uses `public_base_url`; a mismatch between the effective port and URL produces a startup warning.

## LAN Authentication

The dashboard has one configured user. Password records are versioned and use only Python standard-library KDFs. New records prefer `scrypt-v1` through `hashlib.scrypt` with a random 16-byte salt, `n=2**14`, `r=8`, `p=1`, and a 32-byte output. When that API is unavailable, new records use `pbkdf2-sha256-v1` through `hashlib.pbkdf2_hmac` with exactly 600,000 iterations and a 32-byte output. Verification dispatches by the recorded algorithm and uses `hmac.compare_digest`; no Node or subprocess KDF is permitted. Loading a `scrypt-v1` record on a runtime without `hashlib.scrypt` fails startup with an actionable error.

Successful login creates a random 32-byte token. Only its SHA-256 digest is stored in the `dashboard_sessions` SQLite table. The browser receives the raw token in a `symphonz_session` cookie with `HttpOnly`, `SameSite=Lax`, `Path=/`, and `Max-Age` matching `session_days`. `Secure` is set automatically when `public_base_url` is HTTPS.

Sessions store an irreversible SHA-256 configuration generation derived only from the decoded 32-byte `session_secret`. The normalized configured username is stored as separate session metadata and checked independently with `hmac.compare_digest`. Sessions survive restarts only while both values remain unchanged; session-secret or username rotation invalidates existing sessions. Sessions expire after the configured duration and are deleted on logout.

The single configured account uses exactly 64 stable client buckets derived from the submitted client address; submitted usernames never create bucket keys. Each request must atomically create a uniquely identified reservation for one of five slots before password KDF work begins. Requests without a reservation are rejected without executing the KDF, and the SQLite write transaction ends before KDF work. Completion is conditional on that reservation identity: success clears completed failures but preserves other in-flight reservations, while failure releases only its own reservation. The fifth reservation locks the bucket for fifteen minutes within the five-minute attempt window. Bounded transactional cleanup removes expired reservations and stale bucket rows.

Every route except `GET /login`, `POST /login`, and `GET /healthz` requires authentication. The login handler accepts only form-encoded bodies within a small size limit. A validated `next` value may contain only an absolute path beginning with one `/` and must reject every character below U+0020 plus U+007F; this prevents open redirects and control-character response splitting. Logout is a POST operation.

Because 0.4.0 is LAN-only, HTTP is permitted. The UI displays a persistent unencrypted-connection warning when the request is not HTTPS. Documentation states that Internet exposure requires a later TLS/reverse-proxy deployment.

## Report Contract

The `symphonz_report` tool accepts one `publish` operation with these required fields:

- `issue_id`, `issue_identifier`, `title`, and `summary`
- `goal` and `scope`
- `architecture.nodes[]` and `architecture.edges[]`
- `implementation[]`
- `decisions[]`, each with decision, rationale, alternatives, and trade-offs
- `changed_files[]`
- `validation[]`, each with command, result, and evidence
- `risks[]` and `follow_ups[]`
- `review.provider`, `review.url`, `review.branch`, `review.commit`, and `review.target`

All strings have explicit length limits, collections have item limits, issue identifiers must match the active issue, and URLs allow only `http` or `https`. Unknown fields are rejected. Text is escaped during rendering. Architecture nodes and edges render as responsive HTML/CSS rather than executable Mermaid.

Publishing is idempotent for an issue. The publisher pins non-symlink artifact-root and issue-directory file descriptors, writes a new `report-<generation>.json` and `report-<generation>.html` bundle through relative `dir_fd` operations, and fsyncs both files and the directory. Only after both files exist does RuntimeStore atomically switch its authoritative JSON/HTML paths. A failed second write leaves the previous paths unchanged; a successful commit removes the superseded generation. The dashboard resolves the stable route from the current RuntimeStore HTML path rather than a fixed filename:

```text
{public_base_url}/issues/{issue_identifier}/report
```

The report page includes summary, goal and scope, architecture, implementation, decisions, changed files, validation evidence, risks, follow-ups, Linear, branch, commit, and review request links. It has sticky section navigation, collapsible detail sections, copy-link, and print styles.

## Linear Synchronization

Publishing creates or updates one runtime-owned Linear comment headed:

```md
## Symphonz Implementation Report
```

The comment contains the report URL, review request URL, branch, commit, last publication time, and a one-paragraph summary. It is separate from the agent-owned `## Symphonz Workpad`, so retries cannot corrupt the workpad checklist.

The report row is first committed locally with `linear_sync_status = pending` and bounded index metadata only. Synchronization claims an issue-scoped transactional SQLite lease, reloads and validates the full report from the authoritative JSON artifact, paginates Linear comments until the runtime-owned heading is found, and requires mutation `success` plus a valid comment ID. Failed or corrupt/missing-artifact synchronization emits to the dedicated error sink and remains pending with bounded exponential backoff; later success resolves prior report-sync database errors. `Orchestrator.tick()` retries the concrete publisher in Task 5. The publish tool returns the report URL even when Linear is temporarily unavailable and clearly reports the pending synchronization state.

The workflow requires report publication after the review request exists and before moving the issue to `Human Review`. A missing report is a publication blocker.

## Runtime Persistence

`.symphonz/logs/runtime.sqlite3` contains these logical tables:

### `issue_runs`

- Issue ID, identifier, title, Linear state, runtime status, attempt, and workspace.
- Start, update, completion, and cancellation timestamps.
- Codex process, thread, turn, and session identifiers.
- Branch, commit, review URL, report URL, and report publication time.
- Latest error summary and total error count.

### `runtime_events`

- Monotonic integer ID, timestamp, severity, category, type, issue identity, and message.
- A bounded redacted JSON detail object for diagnostic fields.
- Indexes on issue identifier, timestamp, severity, and type.

### `runtime_errors`

- Error ID, issue identity, session, stage, error type, message, retryability, attempt, timestamp, and redacted context.
- Resolution timestamp and resolving event when the operation later succeeds.

### `reports`

- Issue identity, report version, JSON path, HTML path, URL, review metadata, Linear comment ID, synchronization status, retry count, next retry time, and timestamps.

### `dashboard_sessions` and `login_attempts`

- Hashed session tokens and expiry metadata.
- Login rate-limit keys, failure counts, windows, and lock expiration.

SQLite uses WAL mode, foreign keys, transactions, and a busy timeout. Schema setup is idempotent. Runtime data is not reconstructed from `runtime.jsonl` on startup.

## Event and Error Policy

The existing `runtime.jsonl` remains the full diagnostic stream. The dashboard store records normalized milestones instead of every agent-message delta and token update. Important milestones include service lifecycle, issue start, workspace creation, Codex session start, tool call completion, report publication, Linear synchronization, retries, cancellation, block, completion, and cleanup.

Events ending in `_failed`, failed dynamic tool calls, worker exceptions, timeout events, blocked runs, and report synchronization failures create a separate error record and append one redacted line to `errors.jsonl`. Error records include the issue and session identifiers whenever available. Known secret-like keys and authorization values are replaced with `[REDACTED]` recursively before persistence.

## Dashboard Information Architecture

### Overview

- Service health, uptime, running, queued, blocked, retrying, completed, and unresolved error counts.
- Current runs and recently completed reports.
- Latest significant events, excluding token and text deltas.

### Tasks

- Persistent task list with search and status filters.
- Issue, state, runtime status, attempt, last activity, review request, report, and error count.
- Pagination backed by SQLite rather than the in-memory 200-event window.

### Task Detail

- Header with Linear, branch, commit, review request, report, workspace, and session links or identifiers.
- `Overview`, `Timeline`, `Report`, and `Errors` tabs.
- Timeline cursor pagination and filters for lifecycle, Codex, Git, Linear, report, and error events.

### Errors

- Unresolved-first error list with issue, session, stage, attempt, retryability, message, and timestamp.
- Filters by issue, stage, type, and resolved state.
- Error detail shows redacted context and nearby timeline events.

The interface remains restrained and work-focused: compact rows, predictable navigation, 8px-or-smaller radii, neutral surfaces, semantic status colors, responsive layouts, keyboard-visible focus, and no decorative gradients or nested cards.

## HTTP Routes

```text
GET  /healthz
GET  /login
POST /login
POST /logout
GET  /
GET  /tasks
GET  /issues/{identifier}
GET  /issues/{identifier}/report
GET  /errors
GET  /api/overview
GET  /api/tasks
GET  /api/issues/{identifier}
GET  /api/issues/{identifier}/events
GET  /api/issues/{identifier}/errors
GET  /api/errors
```

HTML routes render complete first content for resilience. JavaScript progressively refreshes metrics, filters, tabs, pagination, copy-link, and collapsible report sections. APIs return consistent JSON errors and correct HTTP status codes. Static assets are served from embedded package data with content hashes and cache headers.

## Code Boundaries

- `service/runtime_store.py`: SQLite schema, task/event/error/report/session repositories.
- `service/auth.py`: password hashing, credential verification, sessions, cookies, and rate limiting.
- `service/reporting.py`: report validation, atomic persistence, rendering, URL generation, and Linear synchronization state.
- `service/dashboard.py`: HTTP routing, authentication middleware, request parsing, and responses.
- `service/web_templates.py`: escaped page and component rendering plus embedded CSS/JavaScript.
- `service/event_log.py`: composite sinks, redaction, full JSONL, and dedicated error JSONL.
- `service/runner.py`: constructs shared stores, publisher, dynamic tools, orchestrator, and dashboard.
- `service/orchestrator.py`: emits structured lifecycle updates and retries pending report synchronization.
- `install.py`, `cli.py`, and `runtime.py`: dashboard configuration, auth material, command options, and migration.
- `WORKFLOW.md`: requires safe report publication and Linear report-link synchronization.

## Failure Handling

- Invalid report payload: reject without changing the previous report.
- Interrupted report write: retain authoritative RuntimeStore paths until both versioned files and the issue directory are fsynced, then clean the superseded generation after commit.
- Report rendering failure: retain the previous report and record an error.
- Linear synchronization failure: keep the report available, mark pending, retry, and show the failure in Errors.
- SQLite lock: honor the busy timeout, record a JSONL fallback error, and keep the service alive when possible.
- Corrupt auth file or database migration failure: fail startup with a specific recovery message rather than silently disabling authentication.
- Missing `public_base_url`: allow localhost dashboard operation but reject report publication because no shareable Linear URL can be produced.
- Missing authentication on a non-loopback host: refuse startup.

## Migration and Compatibility

- Existing configs without `[dashboard]` remain loopback-only and preserve the current explicit `--port` behavior.
- `configure-dashboard` upgrades existing projects without overwriting `WORKFLOW.md`.
- Existing `runtime.jsonl` and `attempts.sqlite3` remain valid.
- Runtime database creation is automatic and non-destructive.
- Existing GitHub and GitLab provider flows are unchanged except for mandatory report publication before `Human Review`.
- The CLI version becomes `0.4.0`, and `symphonz version` reports it.

## Verification Strategy

- Unit tests for versioned scrypt/PBKDF2 verification, bounded random-username rate limiting, unique concurrent attempt reservation and expiry, success/failure overlap, session-generation rotation, expiration, logout, parent-directory-safe auth-file handling, cookie flags, and safe redirects including all ASCII controls.
- Unit tests for strict report schema limits, escaping, pinned `dir_fd` artifact I/O, versioned bundle commit, stable URLs, SQLite sync leases, comment pagination, Markdown neutralization, and report-sync error recovery.
- Unit tests for RuntimeStore migrations, pagination, filtering, redaction, error resolution, and concurrent access.
- HTTP tests for login gates, authenticated APIs, task pages, report pages, logout, and missing resources.
- Orchestrator tests for structured lifecycle persistence and pending Linear report synchronization retries.
- Workflow tests requiring report publication before `Human Review`.
- Stateful end-to-end tests with fake Linear, fake Codex, report publication, report comment synchronization, dashboard login, restart persistence, and terminal workspace cleanup.
- Browser screenshots at desktop and mobile widths, plus checks for overflow, blank content, inaccessible controls, and report navigation.

## Acceptance Criteria

- A completed implementation has a stable authenticated HTML report URL and a review request URL.
- Linear contains one updated `## Symphonz Implementation Report` comment with both links.
- The report remains available after service restart and issue workspace removal.
- Dashboard history is available after restart and does not depend on `RuntimeState.events`.
- Every runtime failure is queryable in the Errors view and appended to `errors.jsonl`.
- A configured session remains valid for the configured duration across service restarts.
- Unauthenticated users cannot access task, report, error, or API content.
- Non-loopback startup without configured authentication is rejected.
- Existing projects can enable the feature with `symphonz configure-dashboard`.
- All automated tests and desktop/mobile visual checks pass before release 0.4.0.
