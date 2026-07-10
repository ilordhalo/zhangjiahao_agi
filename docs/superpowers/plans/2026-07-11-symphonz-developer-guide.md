# Symphonz Developer Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline Chinese static developer guide that accurately explains the implemented Linear-to-Codex-to-review-request workflow with interactive diagrams.

**Architecture:** Add one self-contained `docs/index.html` document with semantic sections, inline CSS, and progressive-enhancement JavaScript. Protect its content and offline contract with a focused Python unittest, then verify responsiveness and interaction in a real browser.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, Python `unittest`, Codex in-app browser

## Global Constraints

- The page is Chinese; code identifiers, paths, commands, and JSON-RPC methods remain English.
- The page has no remote CSS, JavaScript, image, or font dependencies.
- The page describes current runtime behavior and labels limitations explicitly.
- The page is readable when JavaScript is unavailable.
- The page supports desktop and mobile layouts and `prefers-reduced-motion`.
- This feature release changes `symphonz version` from `0.1.1` to `0.2.0`.

---

### Task 1: Static Guide Contract

**Files:**
- Create: `tests/test_developer_guide.py`
- Modify: `tests/test_symphonz_cli.py`

**Interfaces:**
- Consumes: repository-relative `docs/index.html` and `symphonz.__version__`
- Produces: content, structure, offline-dependency, and version regression tests

- [x] **Step 1: Write the failing guide tests**

Create `tests/test_developer_guide.py` with tests that read `docs/index.html`, assert the title `Symphonz 服务机制`, assert section ids `system-map`, `linear-polling`, `codex-trigger`, `issue-lifecycle`, `project-layout`, and `implementation-boundaries`, assert the methods `initialize`, `thread/start`, and `turn/start`, and reject `https://`, protocol-relative sources, external stylesheet links, and scripts with `src` attributes.

Update the two version assertions in `tests/test_symphonz_cli.py` to expect `symphonz 0.2.0`.

- [x] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests.test_developer_guide tests.test_symphonz_cli -v`

Expected: guide tests fail because `docs/index.html` does not exist, and version tests fail with `symphonz 0.1.1 != symphonz 0.2.0`.

### Task 2: Developer Guide Page

**Files:**
- Create: `docs/index.html`
- Modify: `symphonz/__init__.py`

**Interfaces:**
- Consumes: exact behavior from `runner.py`, `orchestrator.py`, `linear.py`, `workspace.py`, `workflow.py`, `codex_app_server.py`, and root `WORKFLOW.md`
- Produces: a directly openable static guide and `symphonz 0.2.0`

- [x] **Step 1: Implement semantic page structure**

Add a sticky top navigation and these sections:

```html
<main>
  <section id="overview">...</section>
  <section id="system-map">...</section>
  <section id="linear-polling">...</section>
  <section id="codex-trigger">...</section>
  <section id="issue-lifecycle">...</section>
  <section id="project-layout">...</section>
  <section id="implementation-boundaries">...</section>
</main>
```

The system map places `Symphonz Runtime` in the center and connects Linear GraphQL, `WORKFLOW.md`, Issue Workspace, Codex app-server, Runtime State / Dashboard, and GitHub / GitLab. Use solid connectors for direct Python calls and dashed connectors for actions performed by Codex from workflow instructions.

- [x] **Step 2: Add exact workflow content**

Document the poll loop and GraphQL filter, workspace creation hook, prompt rendering, JSON-RPC handshake, event stream, state routing, and Linear lifecycle. Include explicit callouts that execution is synchronous, runtime state is in memory, completed/blocked describe a turn rather than a Linear terminal state, active issues may be dispatched again, and provider publishing depends on Codex credentials.

- [x] **Step 3: Add interaction and responsive behavior**

Add data attributes to map nodes and sequence steps. Inline JavaScript should update an adjacent detail panel when a node is clicked or activated with Enter/Space, and should let the reader select one sequence step or play all steps. Keep complete labels visible in the HTML so the page remains understandable without JavaScript.

At `max-width: 900px`, stack map nodes and content columns. At `max-width: 640px`, collapse navigation links, make sequence steps vertical, and prevent code/path text overflow. Disable smooth scrolling and step animation under `prefers-reduced-motion: reduce`.

- [x] **Step 4: Bump the version**

Change `symphonz/__init__.py` to:

```python
__version__ = "0.2.0"
```

- [x] **Step 5: Run focused tests to verify GREEN**

Run: `python3 -m unittest tests.test_developer_guide tests.test_symphonz_cli -v`

Expected: all focused tests pass.

### Task 3: Browser and Full-Suite Verification

**Files:**
- Modify if required by findings: `docs/index.html`

**Interfaces:**
- Consumes: completed static page
- Produces: visual and behavioral verification evidence

- [x] **Step 1: Serve and inspect desktop layout**

Run a local static server from the repository and open `/docs/index.html` at a desktop viewport. Verify the system map is readable, connectors have a legend, all sections are reachable from navigation, and the browser console contains no errors.

- [x] **Step 2: Verify interaction**

Activate one system node and one sequence step. Verify the selected state and detail text change. Activate sequence playback and verify the current step advances without resizing or overlapping surrounding content.

- [x] **Step 3: Inspect mobile layout**

Set a phone-sized viewport. Verify no horizontal document overflow, diagram labels remain readable, navigation does not overlap the title, and code blocks scroll internally when necessary.

- [x] **Step 4: Run repository verification**

Run:

```bash
python3 -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/symphonz-pycache python3 -m py_compile symphonz/cli.py symphonz/install.py symphonz/runtime.py symphonz/service/*.py
git diff --check
./bin/symphonz version
```

Expected: 0 failures, no syntax errors, no whitespace errors, and `symphonz 0.2.0`.

- [x] **Step 5: Commit the implementation**

Stage only the page, tests, version update, spec cleanup, and this plan. Commit as `feat(docs): add developer workflow guide` with the validation commands in the body.
