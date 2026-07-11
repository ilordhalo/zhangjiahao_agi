# Symphonz Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Symphonz 0.3.0 with a bounded concurrent scheduler, reliable Codex sessions, guaranteed Linear tooling, safe workspace lifecycle, automated installation, and stateful end-to-end coverage.

**Architecture:** Keep the dependency-free Python package. A lock-protected Orchestrator owns scheduling state and delegates blocking issue runs to a `ThreadPoolExecutor`; the Codex client owns one app-server process/thread per worker and exposes `linear_graphql`; Linear, workspace, logging, and configuration remain focused adapters.

**Tech Stack:** Python 3 standard library, `unittest`, JSON-RPC over stdio, Linear GraphQL, HTML/CSS/JavaScript dashboard, POSIX shell installer

## Global Constraints

- Work only on branch `codex/runtime-hardening` in `/tmp/symphonz-runtime-hardening`.
- Keep the runtime dependency-free and compatible with the repository's current Python baseline.
- Use TDD for every production behavior change and observe each focused test fail before implementation.
- Preserve GitHub and GitLab provider support.
- Generated lifecycle is `Todo -> In Progress -> Ready to Publish -> Human Review -> Rework/Merging -> Done`.
- `Done`, `Closed`, `Cancelled`, `Canceled`, and `Duplicate` are terminal states.
- Never persist Linear, GitHub, or GitLab secrets inside the target repository.
- Release version is `0.3.0`.

---

### Task 1: Linear Adapter and Domain Contracts

**Files:**
- Modify: `symphonz/service/models.py`
- Modify: `symphonz/service/linear.py`
- Create: `symphonz/service/dynamic_tools.py`
- Modify: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `BlockerRef`, paginated `LinearClient.fetch_candidate_issues`, `fetch_issues_by_states`, `execute_linear_graphql(client, arguments)`, and `linear_graphql_tool_spec()`.
- Consumes: existing `Issue`, `LinearClient.graphql`, and file fixture endpoint.

- [x] **Step 1: Write failing pagination, blocker, and tool tests**

Add tests that require two GraphQL pages to be joined, normalize `inverseRelations` of type `blocks`, reject multiple operations, and return a structured successful mutation result:

```python
self.assertEqual([issue.identifier for issue in issues], ["PAY-1", "PAY-2"])
self.assertEqual(issues[1].blocked_by[0].identifier, "PAY-1")
self.assertFalse(execute_linear_graphql(client, {"query": "query A {x} query B {y}"})["success"])
self.assertTrue(execute_linear_graphql(client, {"query": "mutation Move($id: ID!) { issueUpdate(id: $id) { success } }", "variables": {"id": "1"}})["success"])
```

- [x] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_symphonz_service.LinearAndWorkspaceTests -v`

Expected: failures for missing pagination, `blocked_by`, and dynamic tool functions.

- [x] **Step 3: Implement the adapter contracts**

Use GraphQL `pageInfo { hasNextPage endCursor }`, an `$after: String` variable, page size 50, and a loop that rejects `hasNextPage=true` without an end cursor. `execute_linear_graphql` accepts one operation and returns:

```python
{"success": True, "output": json.dumps(body), "contentItems": [{"type": "inputText", "text": json.dumps(body)}]}
```

- [x] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_symphonz_service.LinearAndWorkspaceTests -v`

Expected: all Linear tests pass.

### Task 2: Safe Workspace Lifecycle

**Files:**
- Modify: `symphonz/service/workspace.py`
- Modify: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `workspace_path(project_root, workflow, issue)`, `prepare_workspace`, `run_before_run_hook`, `run_after_run_hook`, and `remove_workspace`.
- Consumes: workflow `workspace.root`, `hooks.{after_create,before_run,after_run,before_remove,timeout_ms}`.

- [x] **Step 1: Write failing lifecycle and path safety tests**

Require hook order `after_create -> before_run -> after_run -> before_remove`, timeout failure for fatal hooks, ignored cleanup-hook failure, partial-directory cleanup, and rejection of a pre-existing workspace symlink escaping the root.

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_symphonz_service.WorkspaceLifecycleTests -v`

Expected: missing lifecycle APIs and symlink containment failure.

- [x] **Step 3: Implement lifecycle functions**

Run hooks with `subprocess.run(..., timeout=timeout_ms / 1000)`. Resolve root and workspace canonically and require `workspace.relative_to(root)` to succeed after resolution. Remove a newly created partial directory only when `after_create` fails.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_symphonz_service.WorkspaceLifecycleTests -v`

Expected: all workspace lifecycle tests pass.

### Task 3: Resilient Codex App-Server Session

**Files:**
- Modify: `symphonz/service/codex_app_server.py`
- Modify: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `CodexAppServer.run_turns(..., max_turns, should_continue, continuation_prompt, cancel_event)`.
- Consumes: `linear_graphql_tool_spec`, a dynamic-tool executor, and timeout settings.

- [x] **Step 1: Write failing protocol tests**

Fake app-server scenarios must assert:

```python
self.assertEqual(result["turn_count"], 2)
self.assertEqual(fake.thread_ids, ["thread-1", "thread-1"])
self.assertEqual(thread_start["params"]["dynamicTools"][0]["name"], "linear_graphql")
```

Also require `response_timeout`, `turn_timeout`, `stall_timeout`, cancellation, valid tool-call replies, unsupported-tool replies, and user-input-required failure.

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_symphonz_service.CodexAppServerTests -v`

Expected: missing multi-turn, timeout, cancellation, and tool behavior.

- [x] **Step 3: Implement queue-backed protocol processing**

Start one daemon reader thread per subprocess. Queue decoded lines and an EOF sentinel. Bound `_request` by `read_timeout_ms`; bound each turn by `turn_timeout_ms`; fail when no message arrives for `stall_timeout_ms`; check `cancel_event` while waiting. Handle `item/tool/call` by replying on the same JSON-RPC `id`.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_symphonz_service.CodexAppServerTests -v`

Expected: all protocol tests pass without hanging.

### Task 4: Concurrent Orchestration, Reconciliation, and Retry

**Files:**
- Modify: `symphonz/service/models.py`
- Rewrite: `symphonz/service/orchestrator.py`
- Modify: `symphonz/service/runner.py`
- Create: `symphonz/service/event_log.py`
- Modify: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `Orchestrator.tick()`, `wait_for_idle(timeout)`, `startup_cleanup()`, and `shutdown()`.
- Consumes: Linear state refresh, workspace lifecycle APIs, and `CodexAppServer.run_turns`.

- [ ] **Step 1: Write failing scheduler tests**

Cover two workers overlapping under a concurrency limit, duplicate claim prevention, priority order, blocked issue suppression, exponential retry metadata, one-second clean continuation, state-change cancellation, terminal cleanup, and JSONL event output.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_symphonz_service.OrchestratorHardeningTests -v`

Expected: current synchronous Orchestrator fails concurrency/retry/reconciliation contracts.

- [ ] **Step 3: Implement single-authority scheduling**

Use `ThreadPoolExecutor`, `threading.RLock`, and per-run `threading.Event`. State keys use stable Linear issue IDs internally and preserve identifiers for display. Retry delay is:

```python
min(10 * (2 ** (attempt - 1)), max_retry_backoff_ms / 1000)
```

The service loop calls `tick()`, sleeps the effective poll interval, and always calls `shutdown()` in `finally`. `--once` dispatches candidates and waits until all first-attempt workers finish.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_symphonz_service.OrchestratorHardeningTests -v`

Expected: all scheduler tests pass deterministically.

### Task 5: Workflow, Installer, Configuration, and Version 0.3.0

**Files:**
- Modify: `WORKFLOW.md`
- Modify: `symphonz/install.py`
- Modify: `symphonz/cli.py`
- Modify: `symphonz/workflow.py`
- Modify: `symphonz/__init__.py`
- Modify: `install.sh`
- Rename: `symphony_readme.md` to `README.md`
- Modify: `tests/test_symphonz_cli.py`
- Modify: `tests/test_developer_guide.py`

**Interfaces:**
- Produces: non-interactive install flags/environment fallback, safe branch continuation instructions, terminal `Done`, and CLI version `0.3.0`.
- Consumes: existing `InstallConfig`, generated `.symphonz/config.toml`, and packaged WORKFLOW template.

- [ ] **Step 1: Write failing CLI/workflow contract tests**

Require `install --yes --linear-project quality-project`, `SYMPHONZ_LINEAR_PROJECT` fallback, missing-field error, generated `Ready to Publish`, terminal `Done`, absence of `git checkout -B` continuation reset, README discovery, and version `0.3.0`.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_symphonz_cli tests.test_developer_guide -v`

Expected: flags are rejected and old workflow/version assertions fail.

- [ ] **Step 3: Implement CLI and workflow changes**

Pass optional CLI fields into `collect_install_config`. Resolve each value in order: explicit flag, `SYMPHONZ_*` environment, detected default. Keep secret input as an environment-variable name. Update the shell installer through a staging directory before replacing the installed library.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_symphonz_cli tests.test_developer_guide -v`

Expected: all CLI, installer, workflow, page, and version tests pass.

### Task 6: Stateful Lifecycle E2E and Dashboard

**Files:**
- Modify: `symphonz/service/dashboard.py`
- Rewrite: `tests/test_symphonz_e2e.py`
- Modify: `docs/index.html`
- Modify: `README.md`

**Interfaces:**
- Produces: dashboard/API visibility for claims/retries/turns and a fake full lifecycle through terminal cleanup.
- Consumes: hardened RuntimeState snapshot and fake Linear GraphQL mutation records.

- [ ] **Step 1: Write failing stateful E2E and dashboard tests**

The fake app-server must call `linear_graphql` to mutate Todo to In Progress and then Human Review. Subsequent fixture states exercise Ready to Publish, Rework, Merging, and Done. Assert one Workpad identity, one branch/review identity, state history, retry visibility, and terminal workspace deletion.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_symphonz_e2e tests.test_symphonz_service.OrchestratorAndDashboardTests -v`

Expected: current fake is read-only and current dashboard lacks hardened fields.

- [ ] **Step 3: Implement fixtures, dashboard, and documentation**

Update the Dashboard to render `claimed`, `turn_count`, retry `attempt/due_at`, and cancellation/error details. Update the developer guide and README to accurately describe guaranteed Linear tooling, trusted-environment policy, new state path, CLI automation, and upgrade behavior.

- [ ] **Step 4: Verify GREEN and full regression**

Run:

```bash
python3 -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/symphonz-pycache python3 -m py_compile symphonz/*.py symphonz/service/*.py
git diff --check
./bin/symphonz version
```

Expected: all tests pass, syntax and whitespace checks pass, and output is `symphonz 0.3.0`.

### Task 7: Review and Branch Delivery

**Files:**
- Modify if findings require it: all files above
- Modify: `docs/superpowers/plans/2026-07-11-symphonz-runtime-hardening.md`

**Interfaces:**
- Produces: reviewed commits pushed to `origin/codex/runtime-hardening`.

- [ ] **Step 1: Request independent code review**

Review runtime correctness, deadlocks, cancellation races, subprocess cleanup, path containment, secrets, lifecycle semantics, and test realism.

- [ ] **Step 2: Address findings with focused RED/GREEN tests**

Run the smallest affected test class after each finding, then the full suite.

- [ ] **Step 3: Commit and push**

Commit coherent changes with conventional messages, verify a clean worktree, and push:

```bash
git push -u origin codex/runtime-hardening
```
