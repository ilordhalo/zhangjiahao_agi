# Built-In Symphonz Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the OpenAI Symphony runtime dependency with a Python runtime built into the `symphonz` CLI package.

**Architecture:** `symphonz install` creates project configuration and workflow files only. `symphonz run` loads `.symphonz/WORKFLOW.md`, polls Linear, creates per-issue workspaces, runs Codex app-server turns, records state/logs, and serves a modern built-in dashboard/API.

**Tech Stack:** Python 3 standard library, Linear GraphQL over `urllib.request`, local subprocesses for Git hooks and Codex app-server, `http.server` for dashboard/API, existing `unittest` suite.

## Global Constraints

- Do not require users to install `mise`, Elixir, Erlang, `escript`, or clone OpenAI Symphony.
- Keep `curl | sh`, `symphonz install`, and `symphonz run` as the public user flow.
- Use only Python standard library modules.
- Keep real credentials out of project files; Linear API key remains environment-backed.
- Preserve `.symphonz/workspace/<issue_identifier>` and `.symphonz/logs` layout.
- Support GitHub and GitLab projects through workflow prompt/provider environment.

---

### Task 1: Internal Runtime Command Contract

**Files:**
- Modify: `symphonz/cli.py`
- Modify: `symphonz/install.py`
- Modify: `symphonz/runtime.py`
- Test: `tests/test_symphonz_cli.py`

**Interfaces:**
- Produces: `symphonz run --port <port>`, `build_run_command(project_root, port=None) -> tuple[list[str], dict[str, str]]`
- Produces: embedded install config uses `runtime.command = "symphonz-internal"`

- [ ] Write failing tests that embedded install no longer creates `.symphonz/runtime`, and `symphonz run --print-command --port 4100` prints an internal service command.
- [ ] Run targeted tests and confirm failure.
- [ ] Change install/runtime code so embedded mode is internal and no external Symphony download occurs.
- [ ] Run targeted tests and confirm pass.

### Task 2: Workflow Parser and Prompt Renderer

**Files:**
- Create: `symphonz/service/workflow.py`
- Create: `symphonz/service/models.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `load_workflow(path: Path) -> WorkflowDefinition`
- Produces: `render_prompt(template: str, issue: Issue, attempt: int | None = None) -> str`

- [ ] Write failing tests for parsing current `WORKFLOW.md` front matter, literal hook blocks, lists, and prompt variables.
- [ ] Run targeted tests and confirm failure.
- [ ] Implement a small YAML subset parser for the current workflow shape.
- [ ] Implement prompt rendering for `{{ issue.* }}` and `{% if issue.description %}` blocks.
- [ ] Run targeted tests and confirm pass.

### Task 3: Linear Client and Workspace Manager

**Files:**
- Create: `symphonz/service/linear.py`
- Create: `symphonz/service/workspace.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `LinearClient.fetch_candidate_issues(active_states: list[str]) -> list[Issue]`
- Produces: `LinearClient.fetch_issues_by_ids(ids: list[str]) -> list[Issue]`
- Produces: `prepare_workspace(project_root: Path, workflow: WorkflowDefinition, issue: Issue) -> Path`

- [ ] Write failing tests for Linear response normalization and workspace creation/hook execution.
- [ ] Run targeted tests and confirm failure.
- [ ] Implement GraphQL request/normalization with environment-backed API key.
- [ ] Implement safe workspace path creation and `hooks.after_create`.
- [ ] Run targeted tests and confirm pass.

### Task 4: Codex App-Server Client

**Files:**
- Create: `symphonz/service/codex_app_server.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `CodexAppServer.run_turn(workspace: Path, prompt: str, title: str, on_event: Callable[[dict], None]) -> dict`

- [ ] Write failing tests using a fake app-server subprocess that speaks JSON-RPC lines.
- [ ] Run targeted tests and confirm failure.
- [ ] Implement initialize, initialized, thread/start, turn/start, event streaming, and graceful shutdown.
- [ ] Run targeted tests and confirm pass.

### Task 5: Orchestrator and Dashboard

**Files:**
- Create: `symphonz/service/orchestrator.py`
- Create: `symphonz/service/dashboard.py`
- Create: `symphonz/service/runner.py`
- Modify: `symphonz/runtime.py`
- Test: `tests/test_symphonz_service.py`

**Interfaces:**
- Produces: `run_service(project_root: Path, workflow_path: Path, logs_root: Path, port: int | None, once: bool = False) -> int`
- Produces: dashboard routes `/`, `/api/state`, `/api/issues/<issue_identifier>`

- [ ] Write failing tests for one-shot orchestration with fake Linear/Codex dependencies and dashboard JSON.
- [ ] Run targeted tests and confirm failure.
- [ ] Implement runtime state, one poll cycle, retry-safe logging, and dashboard/API server.
- [ ] Wire `symphonz run` to `run_service`.
- [ ] Run full tests and confirm pass.

### Task 6: Documentation and End-to-End Verification

**Files:**
- Modify: `symphony_readme.md`
- Modify: `install.sh` if packaging needs new service files
- Test: `tests/test_symphonz_cli.py`

**Interfaces:**
- Produces: installed CLI package includes `symphonz/service/**`

- [ ] Write failing packaging test that installed CLI can import `symphonz.service.runner`.
- [ ] Run targeted tests and confirm failure if packaging misses files.
- [ ] Update installer copy behavior if needed.
- [ ] Run `python3 -m unittest discover -v`.
- [ ] Run local install smoke test with `symphonz run --print-command`.
- [ ] Commit and push.

