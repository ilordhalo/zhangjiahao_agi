# Symphonz Workflow Case Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Chinese static developer guide with a rationale-driven explanation of `WORKFLOW.md` and a four-turn PAY-214 Linear/Codex lifecycle simulation.

**Architecture:** Keep the guide as one offline `docs/index.html` document. Add semantic, progressively readable sections with native `<details>` for each simulated turn, and protect the content/state contract with focused Python tests before visual browser verification.

**Tech Stack:** HTML5, CSS, native `<details>`, Python `unittest`, Codex in-app browser

## Global Constraints

- The simulation is offline and must not claim that real Linear or GitHub writes occurred.
- The guide must distinguish Python Runtime actions, Codex actions, and human/external state changes.
- Human Review does not automatically trigger Codex under the default `active_states`.
- Each turn creates a new Codex app-server thread and turn; no automatic retry is shown.
- The page remains Chinese, self-contained, and free of remote resources.
- This documentation release changes `symphonz version` from `0.2.0` to `0.2.1`.

---

### Task 1: Workflow and Simulation Contracts

**Files:**
- Modify: `tests/test_developer_guide.py`
- Modify: `tests/test_symphonz_cli.py`

**Interfaces:**
- Consumes: `docs/index.html`, `symphonz.__version__`
- Produces: regression contracts for section structure, workflow rationale, turn records, disclaimers, and version output

- [x] **Step 1: Write failing page contract tests**

Extend `DeveloperGuideTests` to require section ids `workflow-anatomy` and `real-case-simulation`; nine `data-workflow-part` entries; exactly four `data-case-turn` entries (`todo`, `done`, `rework`, `merging`); and per-turn hooks `prompt-block`, `linear-sync`, and `review-sync`.

Assert the content includes `PAY-214`, `symphonz/PAY-214-prevent-duplicate-payment`, `## Symphonz Workpad`, the full state path `Todo → In Progress → Done → Human Review → Rework → Human Review → Merging → Closed`, the Human Review pause explanation, and the disclaimer `离线真实结构模拟，不访问 Linear 或 GitHub，不产生外部写操作`.

- [x] **Step 2: Update version expectations to 0.2.1**

Change every `symphonz 0.2.0` assertion in `tests/test_symphonz_cli.py` to `symphonz 0.2.1`.

- [x] **Step 3: Verify RED**

Run: `python3 -m unittest tests.test_developer_guide tests.test_symphonz_cli -v`

Expected: new page contract tests fail because the sections do not exist, and four CLI tests fail because the package still reports `0.2.0`.

### Task 2: WORKFLOW Rationale and PAY-214 Simulation

**Files:**
- Modify: `docs/index.html`
- Modify: `symphonz/__init__.py`

**Interfaces:**
- Consumes: current root `WORKFLOW.md` and the approved design specification
- Produces: `#workflow-anatomy`, `#real-case-simulation`, and `symphonz 0.2.1`

- [x] **Step 1: Add navigation and WORKFLOW anatomy**

Add `WORKFLOW` and `真实案例` navigation anchors. Implement nine compact rows for tracker, polling/workspace/hooks, codex, issue context, operating rules, single Workpad, status map, branch/review convention, and guardrails. Each row shows a real excerpt, its rationale, ownership, and current implementation limitation.

- [x] **Step 2: Add the PAY-214 case header and lifecycle rail**

Introduce the fixed issue facts from the design and the exact state rail:

```text
Todo → In Progress → Done → Human Review → Rework → Human Review → Merging → Closed
```

Mark the `Done`, `Rework`, and `Merging` transitions as human or external state changes, while Codex owns workpad/Git/provider actions inside an active turn.

- [x] **Step 3: Add four complete turn records**

For Todo, Done, Rework, and Merging, add a native `<details data-case-turn="...">`. Every turn contains:

```html
<pre class="prompt-block">dynamic Issue context + status route + action list</pre>
<div class="linear-sync">before/after state and exact Workpad delta</div>
<div class="review-sync">workspace, branch, commit, push, PR, CI changes</div>
```

Use one shared `固定 Prompt 框架` block to explain that Operating Rules, tools/auth, Workpad format, branch convention, validation, publish/rework/merge steps, and guardrails accompany every dynamic turn excerpt.

- [x] **Step 4: Add responsive styling and bump version**

Use a two-column workflow explanation on desktop and one column below 900px. Make each turn a stable vertical record and ensure code blocks scroll internally. Change `symphonz/__init__.py` to `__version__ = "0.2.1"` and all visible page version labels to `v0.2.1`.

- [x] **Step 5: Verify GREEN**

Run: `python3 -m unittest tests.test_developer_guide tests.test_symphonz_cli -v`

Expected: all focused tests pass.

### Task 3: Browser, Review, and Delivery

**Files:**
- Modify if findings require it: `docs/index.html`
- Modify: `docs/superpowers/plans/2026-07-11-symphonz-workflow-case-study.md`

**Interfaces:**
- Consumes: completed guide page
- Produces: responsive/interaction evidence, checked plan, implementation commit, and pushed main branch

- [x] **Step 1: Verify desktop content and details behavior**

Serve the repository locally, navigate to both new anchors, expand all four turn records, and verify Prompt, Linear, Git/Review regions are readable without overlap or layout shifts.

- [x] **Step 2: Verify mobile layout and console**

At 390px width, verify document `scrollWidth == clientWidth`, code blocks contain their own overflow, details summaries wrap, and the browser console has no errors.

- [x] **Step 3: Run complete verification**

Run:

```bash
python3 -m unittest discover -v
PYTHONPYCACHEPREFIX=/tmp/symphonz-pycache python3 -m py_compile symphonz/cli.py symphonz/install.py symphonz/runtime.py symphonz/service/*.py
git diff --check
./bin/symphonz version
```

Expected: all tests pass, no syntax/whitespace errors, and `symphonz 0.2.1`.

- [x] **Step 4: Request read-only code review**

Ask a reviewer to compare the case against `WORKFLOW.md`, focusing on Runtime/Codex/human ownership, Workpad continuity, state transitions, and whether any simulated action is presented as a real write.

- [x] **Step 5: Commit and push**

Stage only the page, tests, package version, this plan, and review fixes. Commit as `feat(docs): explain workflow with case study`, then push `main` to `origin`.
