# Symphonz Task Reports, Runtime History, and LAN Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release Symphonz 0.4.0 with persistent task history and errors, safe implementation-report pages linked from Linear, and a session-authenticated LAN dashboard.

**Architecture:** Add one transactional SQLite repository shared by the orchestrator, report publisher, dashboard, and authentication service. Codex publishes validated structured reports through a new dynamic tool; Symphonz renders escaped HTML, persists it outside workspaces, and idempotently synchronizes a dedicated Linear comment. Split the current monolithic dashboard into routing and deterministic template modules while preserving the standard-library-only runtime.

**Tech Stack:** Python 3.9+ standard library, `sqlite3`, `hashlib.scrypt`, `http.server`, `unittest`, HTML/CSS/vanilla JavaScript, fake Linear/Codex fixtures, browser screenshot verification.

## Global Constraints

- Target release is `0.4.0`.
- The runtime remains Python standard-library only.
- Reports contain engineering rationale and evidence, never private chain-of-thought.
- AI output is treated as untrusted text and never served as executable HTML or JavaScript.
- Non-loopback dashboard binding requires configured authentication.
- LAN HTTP is allowed with an explicit unencrypted-connection warning.
- Existing GitHub, GitLab, Linear, attempt-budget, and workspace-cleanup behavior must remain compatible.
- Project-specific credentials and URLs come from interactive installation or environment flags, never repository templates.
- Runtime reports, auth data, histories, sessions, errors, and workspaces are ignored by Git.

---

### Task 1: Persistent Runtime Repository and Error Archive

**Files:**
- Create: `symphonz/service/runtime_store.py`
- Modify: `symphonz/service/event_log.py`
- Modify: `symphonz/service/models.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `RuntimeStore(path: Path)`, `record_event(event: RuntimeEvent)`, `upsert_issue(entry: dict)`, `record_error(error: RuntimeErrorRecord)`, `list_tasks(...)`, `get_task(identifier)`, `list_events(...)`, `list_errors(...)`, `save_report(...)`, session and login-attempt primitives.
- Produces: `CompositeEventSink(*sinks)` and `ErrorJsonlLog(path)`.
- Consumes: existing `RuntimeEvent` and runtime entry dictionaries.

- [ ] **Step 1: Write failing persistence and filtering tests**

```python
def test_runtime_store_persists_tasks_events_and_errors_across_instances(self):
    store = RuntimeStore(path)
    store.upsert_issue({"issue_identifier": "SYM-1", "status": "running"})
    store.record_event(RuntimeEvent("issue_started", "Started", "SYM-1"))
    store.record_error(RuntimeErrorRecord(issue_identifier="SYM-1", stage="codex", message="boom"))
    reopened = RuntimeStore(path)
    self.assertEqual(reopened.get_task("SYM-1")["status"], "running")
    self.assertEqual(reopened.list_errors(issue_identifier="SYM-1")["items"][0]["message"], "boom")
```

- [ ] **Step 2: Run the focused test and verify missing-module failure**

Run: `python3 -m unittest tests.test_symphonz_service.RuntimeStoreTests -v`
Expected: FAIL because `symphonz.service.runtime_store` does not exist.

- [ ] **Step 3: Implement schema, transactions, pagination, redaction, and JSONL composition**

```python
class RuntimeStore:
    def __init__(self, path: Path): ...
    def upsert_issue(self, entry: dict) -> None: ...
    def record_event(self, event: RuntimeEvent) -> None: ...
    def record_error(self, error: RuntimeErrorRecord) -> int: ...
    def list_tasks(self, *, status: str | None, query: str | None, cursor: int | None, limit: int) -> dict: ...
    def get_task(self, issue_identifier: str) -> dict | None: ...
```

- [ ] **Step 4: Verify WAL concurrency, secret redaction, error JSONL, pagination, and restart tests pass**

Run: `python3 -m unittest tests.test_symphonz_service.RuntimeStoreTests -v`
Expected: PASS.

- [ ] **Step 5: Commit the repository slice**

```bash
git add symphonz/service/runtime_store.py symphonz/service/event_log.py symphonz/service/models.py tests/test_symphonz_service.py
git commit -m "feat(runtime): persist task history and errors"
```

### Task 2: LAN Credentials and Persistent Sessions

**Files:**
- Create: `symphonz/service/auth.py`
- Modify: `symphonz/install.py`
- Test: `tests/test_symphonz_auth.py`
- Test: `tests/test_symphonz_cli.py`

**Interfaces:**
- Produces: `hash_password(password) -> PasswordRecord`, `verify_password(...) -> bool`, `AuthService(store, username, password_record, session_days, secure_cookie)`, `authenticate_cookie(header)`, `login(username, password, client_key)`, `logout(token)`.
- Produces: `write_auth_config(path, password)` and `read_dashboard_auth(project_root)`.
- Consumes: `RuntimeStore` session and login-attempt operations from Task 1.

- [ ] **Step 1: Write failing password, session, expiry, logout, and lockout tests**

```python
def test_login_session_survives_auth_service_restart(self):
    token = first.login("admin", "correct", "192.168.1.8").token
    second = AuthService(RuntimeStore(db), "admin", record, session_days=30)
    self.assertEqual(second.authenticate(token).username, "admin")
```

- [ ] **Step 2: Run auth tests and verify missing-module failure**

Run: `python3 -m unittest tests.test_symphonz_auth -v`
Expected: FAIL because `symphonz.service.auth` does not exist.

- [ ] **Step 3: Implement scrypt records, constant-time verification, token hashing, persistent expiry, logout, and five-attempt lockout**

```python
@dataclass(frozen=True)
class PasswordRecord:
    algorithm: str
    salt: str
    password_hash: str

class AuthService:
    def login(self, username: str, password: str, client_key: str) -> LoginResult: ...
    def authenticate(self, token: str) -> Session | None: ...
    def logout(self, token: str) -> None: ...
```

- [ ] **Step 4: Add auth-file permissions and Git-ignore tests**

Run: `python3 -m unittest tests.test_symphonz_auth tests.test_symphonz_cli.ConfigTests -v`
Expected: PASS, including mode `0600` and no plaintext password.

- [ ] **Step 5: Commit the authentication slice**

```bash
git add symphonz/service/auth.py symphonz/install.py tests/test_symphonz_auth.py tests/test_symphonz_cli.py
git commit -m "feat(auth): add persistent LAN dashboard sessions"
```

### Task 3: Structured Reports and Safe HTML Rendering

**Files:**
- Create: `symphonz/service/reporting.py`
- Modify: `symphonz/service/dynamic_tools.py`
- Test: `tests/test_symphonz_reporting.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `report_tool_spec() -> dict`, `ReportPublisher.publish(arguments) -> dict`, `validate_report(payload) -> ReportDocument`, `render_report(document) -> str`, `sync_pending(linear_client, now) -> int`.
- Consumes: RuntimeStore report rows, artifact root, public base URL, and Linear client.
- Extends dynamic tool routing so `linear_graphql` and `symphonz_report` are both advertised and executed.

- [ ] **Step 1: Write failing schema, escaping, stable URL, atomic replacement, and idempotent Linear-comment tests**

```python
def test_report_renderer_escapes_agent_text_and_contains_required_sections(self):
    html = render_report(valid_report(summary='<script>alert(1)</script>'))
    self.assertNotIn("<script>", html)
    self.assertIn("&lt;script&gt;", html)
    self.assertIn("Architecture", html)
    self.assertIn("Validation", html)
```

- [ ] **Step 2: Run report tests and verify missing-module failure**

Run: `python3 -m unittest tests.test_symphonz_reporting -v`
Expected: FAIL because `symphonz.service.reporting` does not exist.

- [ ] **Step 3: Implement strict dataclass validation and deterministic report renderer**

```python
class ReportPublisher:
    def publish(self, arguments: dict) -> dict:
        document = validate_report(arguments)
        html = render_report(document)
        self._atomic_write(document, html)
        self.store.save_report(..., linear_sync_status="pending")
        return {"success": True, "report_url": self.report_url(document.issue_identifier)}
```

- [ ] **Step 4: Implement dedicated `## Symphonz Implementation Report` comment upsert and pending retry state**

Run: `python3 -m unittest tests.test_symphonz_reporting -v`
Expected: PASS for create, update, temporary Linear failure, and retry.

- [ ] **Step 5: Commit the report slice**

```bash
git add symphonz/service/reporting.py symphonz/service/dynamic_tools.py tests/test_symphonz_reporting.py tests/test_symphonz_service.py
git commit -m "feat(reporting): publish safe implementation reports"
```

### Task 4: Authenticated Dashboard and Task Operations UI

**Files:**
- Create: `symphonz/service/web_templates.py`
- Rewrite: `symphonz/service/dashboard.py`
- Test: `tests/test_symphonz_dashboard.py`

**Interfaces:**
- Produces: `DashboardServer(host, port, store, auth_service, artifacts_root, insecure_warning)`.
- Produces HTML routes `/`, `/tasks`, `/issues/{identifier}`, `/issues/{identifier}/report`, `/errors`, `/login`.
- Produces authenticated JSON routes `/api/overview`, `/api/tasks`, `/api/issues/{identifier}`, `/api/issues/{identifier}/events`, `/api/issues/{identifier}/errors`, `/api/errors`.
- Consumes: RuntimeStore query APIs, AuthService, and report HTML artifacts.

- [ ] **Step 1: Write failing HTTP tests for auth gates, safe redirect, cookies, pages, APIs, report serving, and 404s**

```python
def test_unauthenticated_report_redirects_to_login_and_returns_after_login(self):
    response = self.get("/issues/SYM-1/report")
    self.assertEqual(response.status, 303)
    self.assertEqual(response.headers["Location"], "/login?next=%2Fissues%2FSYM-1%2Freport")
```

- [ ] **Step 2: Run dashboard tests and verify expected route failures**

Run: `python3 -m unittest tests.test_symphonz_dashboard -v`
Expected: FAIL because the existing server has no authentication or persistent routes.

- [ ] **Step 3: Implement routing, authentication middleware, request limits, cookies, and deterministic templates**

```python
ROUTES = {
    "/": render_overview_page,
    "/tasks": render_tasks_page,
    "/errors": render_errors_page,
}
```

- [ ] **Step 4: Implement Linear-like overview, task detail tabs, timeline filters, report navigation, error details, responsive layout, and progressive refresh**

Run: `python3 -m unittest tests.test_symphonz_dashboard -v`
Expected: PASS with no unauthenticated task data exposure.

- [ ] **Step 5: Commit the dashboard slice**

```bash
git add symphonz/service/dashboard.py symphonz/service/web_templates.py tests/test_symphonz_dashboard.py
git commit -m "feat(dashboard): add authenticated task operations UI"
```

### Task 5: Runtime and Orchestrator Integration

**Files:**
- Modify: `symphonz/service/runner.py`
- Modify: `symphonz/service/orchestrator.py`
- Modify: `symphonz/service/codex_app_server.py`
- Modify: `symphonz/service/models.py`
- Test: `tests/test_symphonz_service.py`
- Test: `tests/test_symphonz_e2e.py`

**Interfaces:**
- Consumes: RuntimeStore, ReportPublisher, AuthService, DashboardServer, `linear_graphql_tool_spec()`, and `report_tool_spec()`.
- Produces: structured lifecycle persistence, dynamic report tool execution, pending report sync retries, and report metadata in issue entries.

- [ ] **Step 1: Write failing orchestration tests for task persistence, normalized events, error capture, report-tool advertisement, and pending sync retry**

```python
def test_orchestrator_persists_issue_lifecycle_and_retries_pending_report_sync(self):
    orchestrator.tick()
    orchestrator.wait_for_idle(timeout=2)
    self.assertEqual(store.get_task("SYM-1")["status"], "completed")
    self.assertEqual(publisher.sync_calls, 1)
```

- [ ] **Step 2: Run focused service and E2E tests and verify failures**

Run: `python3 -m unittest tests.test_symphonz_service.OrchestratorPersistenceTests tests.test_symphonz_e2e -v`
Expected: FAIL because stores and report tools are not wired into the runtime.

- [ ] **Step 3: Wire shared services and persist lifecycle transitions at dispatch, retry, block, cancel, complete, and cleanup boundaries**

```python
dynamic_tools = {
    "linear_graphql": lambda arguments: execute_linear_graphql(linear, arguments),
    "symphonz_report": publisher.publish,
}
```

- [ ] **Step 4: Filter noisy Codex deltas from dashboard history while retaining full runtime JSONL diagnostics**

Run: `python3 -m unittest tests.test_symphonz_service tests.test_symphonz_e2e -v`
Expected: PASS, including restart persistence and terminal workspace cleanup with retained report.

- [ ] **Step 5: Commit runtime integration**

```bash
git add symphonz/service/runner.py symphonz/service/orchestrator.py symphonz/service/codex_app_server.py symphonz/service/models.py tests/test_symphonz_service.py tests/test_symphonz_e2e.py
git commit -m "feat(runtime): integrate reports and persistent task history"
```

### Task 6: Installation, Migration, Workflow, and Version 0.4.0

**Files:**
- Modify: `symphonz/install.py`
- Modify: `symphonz/cli.py`
- Modify: `symphonz/runtime.py`
- Modify: `symphonz/workflow.py`
- Modify: `WORKFLOW.md`
- Modify: `README.md`
- Modify: `symphonz/__init__.py`
- Test: `tests/test_symphonz_cli.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: dashboard fields on `InstallConfig`, `configure_dashboard(...)`, `symphonz configure-dashboard`, `run --host`, configured host/port/public URL propagation, and generated report workflow rules.

- [ ] **Step 1: Write failing install, configure, migration, run-command, workflow, and version tests**

```python
def test_configure_dashboard_preserves_existing_workflow_and_git_config(self):
    before = workflow.read_text()
    configure_dashboard(root, username="admin", password="secret", public_base_url="http://192.168.1.20:4000")
    self.assertEqual(workflow.read_text(), before)
    self.assertEqual(read_config(config)["git"]["remote"], original_remote)
```

- [ ] **Step 2: Run CLI tests and verify missing arguments and command failures**

Run: `python3 -m unittest tests.test_symphonz_cli -v`
Expected: FAIL because dashboard configuration and `configure-dashboard` do not exist.

- [ ] **Step 3: Implement interactive/non-interactive dashboard configuration and safe auth-file generation**

```python
configure = subcommands.add_parser("configure-dashboard")
configure.add_argument("--host")
configure.add_argument("--port", type=int)
configure.add_argument("--public-base-url")
configure.add_argument("--username")
configure.add_argument("--session-days", type=int)
```

- [ ] **Step 4: Update WORKFLOW to require `symphonz_report` after review creation and before Human Review**

Run: `python3 -m unittest tests.test_symphonz_cli tests.test_symphonz_service.WorkflowServiceTests -v`
Expected: PASS and generated workflow contains no personal credentials or URLs.

- [ ] **Step 5: Set version 0.4.0 and document upgrade/configuration commands**

Run: `./bin/symphonz version`
Expected: `symphonz 0.4.0`.

- [ ] **Step 6: Commit installation and workflow changes**

```bash
git add symphonz/install.py symphonz/cli.py symphonz/runtime.py symphonz/workflow.py WORKFLOW.md README.md symphonz/__init__.py tests/test_symphonz_cli.py tests/test_symphonz_service.py
git commit -m "feat(cli): configure reports and LAN dashboard auth"
```

### Task 7: Full Verification and Visual QA

**Files:**
- Modify: `tests/test_symphonz_e2e.py`
- Modify: `tests/test_developer_guide.py`
- Modify: `docs/index.html`
- Test: all test modules

**Interfaces:**
- Consumes every prior public interface.
- Produces release evidence, updated developer documentation, and desktop/mobile screenshots.

- [ ] **Step 1: Extend fake Linear and fake Codex flow through report publication, Linear report-comment sync, restart, dashboard login, and terminal cleanup**

```python
self.assertEqual(linear.report_comments["issue-1"].count("## Symphonz Implementation Report"), 1)
self.assertTrue(report_path.exists())
self.assertFalse(workspace_path.exists())
```

- [ ] **Step 2: Run full automated verification**

Run: `python3 -m unittest discover -v`
Expected: all tests pass.

Run: `PYTHONPYCACHEPREFIX=/tmp/symphonz-pycache python3 -m py_compile symphonz/*.py symphonz/service/*.py tests/*.py`
Expected: exit 0.

Run: `sh -n install.sh && git diff --check`
Expected: exit 0.

- [ ] **Step 3: Run a temporary authenticated dashboard with seeded task/report/error fixtures**

Run: `./bin/symphonz service <fixture-workflow> --logs-root <fixture-logs> --host 127.0.0.1 --port 0`
Expected: server prints an assigned local URL and all authenticated routes respond.

- [ ] **Step 4: Capture and inspect desktop and mobile browser screenshots**

Viewports: `1440x1000`, `1024x768`, and `390x844`.
Expected: no blank views, horizontal page overflow, clipped controls, overlapping text, inaccessible report navigation, or missing error details.

- [ ] **Step 5: Run an independent code review and address correctness findings**

Review focus: authentication bypass, session leakage, path traversal, report XSS, Linear idempotency, SQLite concurrency, workspace cleanup, compatibility, and missing tests.

- [ ] **Step 6: Commit release verification and documentation**

```bash
git add tests/test_symphonz_e2e.py tests/test_developer_guide.py docs/index.html
git commit -m "test: verify authenticated report workflow"
```

## Final Release Gate

- [ ] Every acceptance criterion in the design spec maps to a passing automated or visual test.
- [ ] `symphonz version` prints `0.4.0`.
- [ ] The feature branch is clean and contains no generated credentials, databases, reports, logs, screenshots, or temporary fixtures.
- [ ] Commit history and final summary name tests, migration commands, LAN security limits, and report URL behavior.
