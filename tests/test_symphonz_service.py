from pathlib import Path
import json
import os
import tempfile
import unittest


class WorkflowServiceTests(unittest.TestCase):
    def test_load_workflow_parses_current_front_matter(self):
        from symphonz.service.workflow import load_workflow

        workflow = load_workflow(Path("WORKFLOW.md"))

        self.assertEqual(workflow.config["tracker"]["kind"], "linear")
        self.assertEqual(workflow.config["workspace"]["root"], ".symphonz/workspace")
        self.assertIn("Todo", workflow.config["tracker"]["active_states"])
        self.assertIn("git clone --depth 1", workflow.config["hooks"]["after_create"])
        self.assertIn("{{ issue.identifier }}", workflow.prompt_template)

    def test_render_prompt_supports_issue_variables_and_description_condition(self):
        from symphonz.service.models import Issue
        from symphonz.service.workflow import render_prompt

        issue = Issue(
            id="issue-id",
            identifier="SYM-123",
            title="Implement runtime",
            description="Build the built-in runtime.",
            state="Todo",
            labels=["codex-ready"],
            url="https://linear.app/example/issue/SYM-123",
        )
        template = (
            "Issue {{ issue.identifier }}\n"
            "Title: {{ issue.title }}\n"
            "{% if issue.description %}\n"
            "{{ issue.description }}\n"
            "{% else %}\n"
            "No description provided.\n"
            "{% endif %}"
        )

        rendered = render_prompt(template, issue)

        self.assertIn("Issue SYM-123", rendered)
        self.assertIn("Title: Implement runtime", rendered)
        self.assertIn("Build the built-in runtime.", rendered)
        self.assertNotIn("No description provided.", rendered)

    def test_render_prompt_uses_else_branch_for_empty_description(self):
        from symphonz.service.models import Issue
        from symphonz.service.workflow import render_prompt

        issue = Issue(id="issue-id", identifier="SYM-124", title="No body", description=None, state="Todo")
        template = "{% if issue.description %}{{ issue.description }}{% else %}No description provided.{% endif %}"

        self.assertEqual(render_prompt(template, issue), "No description provided.")


class LinearAndWorkspaceTests(unittest.TestCase):
    def test_normalize_linear_issue_response(self):
        from symphonz.service.linear import normalize_issue

        issue = normalize_issue(
            {
                "id": "id-1",
                "identifier": "SYM-1",
                "title": "Build runtime",
                "description": "Body",
                "priority": 2,
                "state": {"name": "Todo"},
                "branchName": "feature/runtime",
                "url": "https://linear.app/example/issue/SYM-1",
                "labels": {"nodes": [{"name": "Codex Ready"}, {"name": "Backend"}]},
                "createdAt": "2026-07-09T00:00:00Z",
                "updatedAt": "2026-07-09T01:00:00Z",
            }
        )

        self.assertEqual(issue.id, "id-1")
        self.assertEqual(issue.identifier, "SYM-1")
        self.assertEqual(issue.state, "Todo")
        self.assertEqual(issue.labels, ["codex ready", "backend"])
        self.assertEqual(issue.branch_name, "feature/runtime")

    def test_linear_file_fixture_records_graphql_request_and_returns_response(self):
        from symphonz.service.linear import LinearClient

        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            (fixture / "responses.json").write_text(
                json.dumps(
                    {
                        "SymphonzPoll": {
                            "data": {
                                "issues": {
                                    "nodes": [
                                        {
                                            "id": "id-1",
                                            "identifier": "SYM-1",
                                            "title": "Fixture issue",
                                            "state": {"name": "Todo"},
                                            "labels": {"nodes": []},
                                        }
                                    ]
                                }
                            }
                        }
                    }
                )
            )
            client = LinearClient(api_key="test-key", project_slug="quality-project", endpoint=fixture.as_uri())

            issues = client.fetch_candidate_issues(["Todo"])

            requests = [json.loads(line) for line in (fixture / "requests.jsonl").read_text().splitlines()]
            self.assertEqual(issues[0].identifier, "SYM-1")
            self.assertEqual(requests[0]["authorization"], "test-key")
            self.assertEqual(requests[0]["operation"], "SymphonzPoll")
            self.assertEqual(requests[0]["variables"]["projectSlug"], "quality-project")

    def test_build_linear_client_uses_endpoint_override_from_environment(self):
        from symphonz.service.runner import build_linear_client

        original_endpoint = os.environ.get("SYMPHONZ_LINEAR_ENDPOINT")
        original_key = os.environ.get("LINEAR_API_KEY")
        os.environ["SYMPHONZ_LINEAR_ENDPOINT"] = "file:///tmp/symphonz-linear-fixture"
        os.environ["LINEAR_API_KEY"] = "env-key"
        try:
            client = build_linear_client({"tracker": {"api_key": "$LINEAR_API_KEY", "project_slug": "quality-project"}})
        finally:
            if original_endpoint is None:
                os.environ.pop("SYMPHONZ_LINEAR_ENDPOINT", None)
            else:
                os.environ["SYMPHONZ_LINEAR_ENDPOINT"] = original_endpoint
            if original_key is None:
                os.environ.pop("LINEAR_API_KEY", None)
            else:
                os.environ["LINEAR_API_KEY"] = original_key

        self.assertEqual(client.endpoint, "file:///tmp/symphonz-linear-fixture")
        self.assertEqual(client.api_key, "env-key")

    def test_prepare_workspace_creates_safe_issue_dir_and_runs_after_create_hook(self):
        from symphonz.service.models import Issue, WorkflowDefinition
        from symphonz.service.workspace import prepare_workspace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowDefinition(
                path=root / ".symphonz" / "WORKFLOW.md",
                config={
                    "workspace": {"root": ".symphonz/workspace"},
                    "hooks": {"after_create": "printf '%s' \"$SYMPHONZ_ISSUE_IDENTIFIER\" > issue.txt\n"},
                },
                prompt_template="",
            )
            issue = Issue(id="id-1", identifier="SYM/1", title="Workspace", state="Todo")

            workspace = prepare_workspace(root, workflow, issue)

            self.assertEqual(workspace, root / ".symphonz" / "workspace" / "SYM_1")
            self.assertEqual((workspace / "issue.txt").read_text(), "SYM/1")


class CodexAppServerTests(unittest.TestCase):
    def test_codex_app_server_runs_single_turn(self):
        from symphonz.service.codex_app_server import CodexAppServer

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "fake_app_server.py"
            server.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line)\n"
                "    method = msg.get('method')\n"
                "    if method == 'initialize':\n"
                "        if 'clientInfo' not in msg.get('params', {}):\n"
                "            print(json.dumps({'id': msg['id'], 'error': {'code': -32600, 'message': 'Invalid request: missing field `clientInfo`'}}), flush=True)\n"
                "            continue\n"
                "        print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'initialized':\n"
                "        pass\n"
                "    elif method == 'thread/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        print(json.dumps({'method': 'turn/completed', 'params': {'usage': {'totalTokens': 5}}}), flush=True)\n"
            )
            events = []
            client = CodexAppServer(command=f"python3 {server}")

            result = client.run_turn(
                workspace=root,
                prompt="Do the work",
                title="SYM-1: Test",
                approval_policy="never",
                thread_sandbox="workspace-write",
                turn_sandbox_policy={"type": "workspaceWrite"},
                on_event=events.append,
            )

            self.assertEqual(result["thread_id"], "thread-1")
            self.assertEqual(result["turn_id"], "turn-1")
            self.assertEqual(result["session_id"], "thread-1-turn-1")
            self.assertTrue(any(event["type"] == "turn_completed" for event in events))


class OrchestratorAndDashboardTests(unittest.TestCase):
    def test_orchestrator_one_shot_runs_issue_and_records_state(self):
        from symphonz.service.models import Issue
        from symphonz.service.orchestrator import Orchestrator
        from symphonz.service.workflow import load_workflow

        class FakeLinear:
            def fetch_candidate_issues(self, active_states):
                return [Issue(id="id-1", identifier="SYM-1", title="Run task", description="Body", state="Todo")]

            def fetch_issues_by_ids(self, ids):
                return [Issue(id="id-1", identifier="SYM-1", title="Run task", description="Body", state="Closed")]

        class FakeCodex:
            def __init__(self):
                self.prompts = []

            def run_turn(self, workspace, prompt, title, approval_policy, thread_sandbox, turn_sandbox_policy, on_event):
                self.prompts.append(prompt)
                on_event({"type": "turn_completed", "params": {}})
                return {"session_id": "thread-turn", "thread_id": "thread", "turn_id": "turn", "result": {}}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow_path = root / "WORKFLOW.md"
            workflow_path.write_text(Path("WORKFLOW.md").read_text())
            workflow = load_workflow(workflow_path)
            workflow.config["hooks"]["after_create"] = "printf ready > ready.txt\n"
            codex = FakeCodex()
            orchestrator = Orchestrator(root, workflow, FakeLinear(), codex)

            orchestrator.poll_once()

            snapshot = orchestrator.state.snapshot()
            self.assertEqual(snapshot["counts"]["completed"], 1)
            self.assertEqual(snapshot["completed"][0]["issue_identifier"], "SYM-1")
            self.assertIn("Identifier: SYM-1", codex.prompts[0])

    def test_dashboard_serves_state_json(self):
        from symphonz.service.dashboard import find_issue, render_dashboard_html
        from symphonz.service.models import RuntimeState

        state = RuntimeState()
        state.add_event("started", "runtime started")
        state.completed["SYM-1"] = {"issue_identifier": "SYM-1", "status": "completed"}
        payload = state.snapshot()
        html = render_dashboard_html()

        self.assertEqual(payload["counts"]["running"], 0)
        self.assertEqual(payload["events"][0]["message"], "runtime started")
        self.assertEqual(find_issue(payload, "SYM-1")["status"], "completed")
        self.assertIn("Symphonz Runtime", html)
        self.assertIn("Issue Queue", html)
        self.assertIn("Activity Feed", html)
        self.assertIn("class=\"app-shell\"", html)
        self.assertIn("class=\"sidebar\"", html)
        self.assertIn("status-chip", html)
