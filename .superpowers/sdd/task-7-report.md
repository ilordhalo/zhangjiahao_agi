# Task 7 Report: Automated E2E and Chinese Developer Guide

Date: 2026-07-17
Base HEAD: `0db7ff3`
Target release: `0.4.0`

## Summary

- Extended the installed-CLI E2E to use only a file-backed fake Linear service
  and a local fake Codex app-server.
- Replaced the stale 0.3.1 page with a Chinese 0.4.0 developer guide covering
  polling, routing, Codex JSON-RPC, dynamic tools, persistent history, reports,
  Dashboard authentication, Human Review, and terminal cleanup.
- Expanded developer-guide tests to enforce version, required sections,
  diagram semantics, realistic PAY-214 turns, ownership boundaries,
  accessibility, offline resources, safe inline scripting, and privacy.

No runtime source change was required. The existing 0.4.0 interfaces support
the requested process-level flow.

## E2E Coverage

`tests/test_symphonz_e2e.py` now verifies:

1. The locally installed CLI creates configured Dashboard values and a private
   mode-0600 `auth.toml` without putting the password in `config.toml`.
2. Fake Codex receives the real `initialize`, `initialized`, `thread/start`,
   and `turn/start` protocol and requires both `linear_graphql` and
   `symphonz_report` dynamic tools.
3. Todo, Ready to Publish, Rework, and Merging turns reuse one workspace,
   deterministic branch, Workpad comment, and review URL.
4. The first report-comment mutation fails locally, leaving a queryable
   `report_sync` error and a `pending` report in RuntimeStore.
5. The fake Agent observes `linear_sync_status=pending`, leaves Linear in Ready
   to Publish, and retries only after the fixture becomes available. The retry
   syncs the comment before the Human Review transition and resolves the error.
6. A service restart preserves the authenticated session, task history, and
   report artifact. Rework then republishes and updates the same runtime-owned
   Linear comment. The final fake Linear state contains one Workpad heading and
   one implementation-report heading.
7. Task, timeline, error, review, report, and synchronization state survive
   restart in `runtime.sqlite3`; the report-sync error is resolved rather than
   deleted after recovery.
8. Unauthenticated report access redirects to login. The authenticated report
   route works before restart, after restart with the original cookie, and
   after terminal workspace deletion.
9. Merging moves Linear to Done, persists `workspace_cleanup_status=removed`,
   removes the workspace, and preserves the authoritative JSON/HTML report.
10. Every fake Codex leader and descendant process is gone, and no delayed
    child-process sentinel is written.

All fake credentials, databases, auth files, logs, reports, workspaces, and
process audit files are created under `TemporaryDirectory` and are not part of
the repository diff.

## Developer Guide

`docs/index.html` is a self-contained Chinese 0.4.0 guide with:

- A component/data-flow map for Linear, WORKFLOW, Runtime, workspace, Codex,
  providers, ReportPublisher, RuntimeStore, and Dashboard/Auth.
- A 13-step protocol diagram from Linear polling and eligibility through
  app-server initialization, both dynamic tools, persistence, report sync,
  Human Review, and terminal cleanup.
- A section-by-section explanation of why `WORKFLOW.md` combines deterministic
  Runtime configuration with the complete Agent prompt.
- Explicit Runtime-owned versus Agent-owned boundaries for Workpad, report
  comments, artifacts, state, branch, and review operations.
- RuntimeStore, structured event/error, JSONL, atomic report bundle, stable
  route, pending retry, auth/session, rate-limit, and trusted-LAN behavior.
- A realistic four-turn PAY-214 simulation with prompts, Workpad deltas,
  commits, branch, PR, report URL, report-comment state, sync state, Human
  Review transitions, merge, and retained report artifacts.
- Responsive layouts, keyboard focus, reduced-motion handling, no remote
  assets, no unsafe DOM injection APIs, and no personal credentials.

## TDD Evidence

The new developer-guide tests were written first and run against the old page.
Four tests failed for the intended missing 0.4.0 sections, components, report
semantics, and ownership content; the existing offline/privacy test remained
green. After replacing the page, all five guide tests passed.

The E2E initially stopped at the managed sandbox's local-socket restriction.
With local-only escalation it exercised the complete flow. Two test-contract
issues were then corrected before final verification:

- Linear deliberately Markdown-neutralizes the Agent-supplied review URL in
  the runtime comment, so the assertion now checks the review target without
  requiring a clickable Markdown URL.
- Filesystem removal becomes visible immediately before the cleanup event is
  committed, so the E2E now waits for the durable `workspace_removed`
  milestone before stopping the service.
- A dynamic tool's business payload is JSON inside the app-server response's
  `output` field. The fake Agent now parses that real contract and proves that
  `linear_sync_status=pending` blocks the Human Review state mutation.

## Verification Evidence

- `python3 -m unittest -v tests.test_developer_guide`: 5 tests passed.
- `python3 -m unittest -v tests.test_symphonz_e2e`: 1 test passed with
  local-socket escalation.
- `python3 -m unittest -v tests.test_symphonz_auth tests.test_symphonz_cli
  tests.test_symphonz_reporting tests.test_symphonz_service`: 283 tests passed.
- `python3 -m unittest discover -v`: 314 of 315 tests passed. The existing
  `test_issue_publish_lock_serializes_cross_process_publication` forked child
  terminated with `SIGSEGV` only in the full-suite order. The same test passes
  alone and the complete reporting module passes in the 283-test non-Dashboard
  run. The macOS crash report identifies Apple system Python 3.9 crashing in
  `_os_log_preferences_refresh` while SQLite `openDatabase` runs on the child
  side of `fork`; no owned reporting/runtime file changed.
- `PYTHONPYCACHEPREFIX=/tmp/symphonz-pycache python3 -m py_compile
  symphonz/*.py symphonz/service/*.py tests/*.py`: passed.
- `sh -n install.sh`: passed.
- Offline/privacy scan of `docs/index.html`: passed.
- `git diff --check`: passed.

The controller explicitly owns final browser visual QA at 1440x1000,
1024x768, and 390x844; no browser screenshot or visual-QA artifact was created.

## Self-Review and Concern

- Reviewed authentication/session persistence, report idempotency, pending
  recovery, stable artifact reads, terminal cleanup timing, and child-process
  termination through observable interfaces.
- Checked the worktree for generated auth, database, report, log, screenshot,
  and temporary files; only the owned tests, guide, and this report are in
  scope.
- The implementation intentionally neutralizes the Agent-supplied review URL
  in the Linear report comment. The stable report URL remains clickable, and
  the actual review link remains available in task metadata and the rendered
  report. This differs from a literal reading of "both links are clickable in
  the Linear comment" and is documented in the guide rather than hidden.
- Full discovery on Apple system Python 3.9 retains the fork-after-runtime
  platform crash described above. Focused E2E, focused fork-lock, the complete
  reporting module, and all requested non-Dashboard modules pass independently.

## Files

- `tests/test_symphonz_e2e.py`
- `tests/test_developer_guide.py`
- `docs/index.html`
- `.superpowers/sdd/task-7-report.md`
