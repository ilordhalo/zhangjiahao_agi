from pathlib import Path
from typing import Optional
from contextlib import redirect_stderr
import io
import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
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
    def test_linear_client_paginates_candidates_and_normalizes_blockers(self):
        from symphonz.service.linear import LinearClient

        pages = {
            None: {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "pay-1",
                                "identifier": "PAY-1",
                                "title": "First payment task",
                                "state": {"name": "Todo"},
                                "labels": {"nodes": []},
                                "inverseRelations": {"nodes": []},
                            }
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next-page"},
                    }
                }
            },
            "next-page": {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "pay-2",
                                "identifier": "PAY-2",
                                "title": "Second payment task",
                                "state": {"name": "Todo"},
                                "labels": {"nodes": []},
                                "inverseRelations": {
                                    "nodes": [
                                        {
                                            "type": "blocks",
                                            "issue": {
                                                "id": "pay-1",
                                                "identifier": "PAY-1",
                                                "state": {"name": "In Progress"},
                                            },
                                        }
                                    ]
                                },
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        }
        client = LinearClient(api_key="test-key", project_slug="payments")
        requests = []

        def graphql(query, variables):
            requests.append((query, variables))
            return pages[variables["after"]]

        client.graphql = graphql

        issues = client.fetch_candidate_issues(["Todo"])

        self.assertEqual([issue.identifier for issue in issues], ["PAY-1", "PAY-2"])
        self.assertEqual(issues[1].blocked_by[0].identifier, "PAY-1")
        self.assertEqual(issues[1].blocked_by[0].state, "In Progress")
        self.assertEqual([request[1]["after"] for request in requests], [None, "next-page"])
        self.assertTrue(all("pageInfo" in request[0] for request in requests))

    def test_linear_client_fetches_issues_by_states_across_pages(self):
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        cursors = []

        def graphql(query, variables):
            cursors.append(variables["after"])
            identifier = "PAY-1" if variables["after"] is None else "PAY-2"
            return {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": identifier.lower(),
                                "identifier": identifier,
                                "title": identifier,
                                "state": {"name": "In Progress"},
                                "labels": {"nodes": []},
                                "inverseRelations": {"nodes": []},
                            }
                        ],
                        "pageInfo": {
                            "hasNextPage": variables["after"] is None,
                            "endCursor": "page-2" if variables["after"] is None else None,
                        },
                    }
                }
            }

        client.graphql = graphql

        issues = client.fetch_issues_by_states(["In Progress"])

        self.assertEqual([issue.identifier for issue in issues], ["PAY-1", "PAY-2"])
        self.assertEqual(cursors, [None, "page-2"])

    def test_linear_client_rejects_page_without_end_cursor(self):
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {
            "data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}}
        }

        with self.assertRaisesRegex(RuntimeError, "end cursor"):
            client.fetch_candidate_issues(["Todo"])

    def test_linear_graphql_tool_rejects_multiple_operations(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")

        result = execute_linear_graphql(client, {"query": "query A {x} query B {y}"})

        self.assertFalse(result["success"])
        self.assertEqual(result["contentItems"], [])

    def test_linear_graphql_tool_rejects_subscription_operations(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {"data": {"issueUpdated": {"id": "1"}}}

        result = execute_linear_graphql(client, {"query": "subscription WatchIssue { issueUpdated { id } }"})

        self.assertFalse(result["success"])
        self.assertEqual(result["contentItems"], [])

    def test_linear_graphql_tool_rejects_empty_selection_set(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {"data": {}}

        result = execute_linear_graphql(client, {"query": "query Empty {}"})

        self.assertFalse(result["success"])
        self.assertEqual(result["contentItems"], [])

    def test_linear_graphql_tool_rejects_unbalanced_operation_block(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {"data": {"issue": {"id": "1"}}}

        result = execute_linear_graphql(client, {"query": "query Broken { issue"})

        self.assertFalse(result["success"])
        self.assertEqual(result["contentItems"], [])

    def test_linear_graphql_tool_rejects_anonymous_query_shorthand(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {"data": {"viewer": {"id": "1"}}}

        result = execute_linear_graphql(client, {"query": "{ viewer { id } }"})

        self.assertFalse(result["success"])
        self.assertEqual(result["contentItems"], [])

    def test_linear_graphql_tool_returns_structured_mutation_response(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql, linear_graphql_tool_spec
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {"data": {"issueUpdate": {"success": True}}}

        result = execute_linear_graphql(
            client,
            {
                "query": "mutation Move($id: ID!) { issueUpdate(id: $id) { success } }",
                "variables": {"id": "1"},
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(json.loads(result["output"]), {"data": {"issueUpdate": {"success": True}}})
        self.assertEqual(result["contentItems"], [{"type": "inputText", "text": result["output"]}])
        self.assertEqual(linear_graphql_tool_spec()["name"], "linear_graphql")

    def test_linear_graphql_tool_preserves_graphql_errors_as_failure(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql

        class ErrorClient:
            def graphql(self, query, variables):
                return {"data": None, "errors": [{"message": "permission denied"}]}

        result = execute_linear_graphql(
            ErrorClient(),
            {"query": "mutation Move { issueUpdate { success } }"},
        )

        self.assertFalse(result["success"])
        self.assertIn("permission denied", result["output"])

    def test_linear_graphql_tool_allows_keywords_inside_string_literals(self):
        from symphonz.service.dynamic_tools import execute_linear_graphql
        from symphonz.service.linear import LinearClient

        client = LinearClient(api_key="test-key", project_slug="payments")
        client.graphql = lambda query, variables: {"data": {"issueSearch": {"nodes": []}}}

        result = execute_linear_graphql(
            client,
            {
                "query": (
                    'query SearchIssues { issueSearch(query: "mutation blocked issue") { nodes { id } } }'
                )
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(json.loads(result["output"]), {"data": {"issueSearch": {"nodes": []}}})

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


class WorkspaceLifecycleTests(unittest.TestCase):
    def setUp(self):
        from symphonz.service.models import Issue

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace_root = self.root / ".symphonz" / "workspace"
        self.log_path = self.root / "workspace-hooks.log"
        self.issue = Issue(id="id-1", identifier="SYM/1", title="Workspace", state="Todo")

    def workflow(self, hooks: Optional[dict] = None):
        from symphonz.service.models import WorkflowDefinition

        return WorkflowDefinition(
            path=self.root / ".symphonz" / "WORKFLOW.md",
            config={
                "workspace": {"root": ".symphonz/workspace"},
                "hooks": hooks or {},
            },
            prompt_template="",
        )

    def append_hook(self, label: str, suffix: str = "") -> str:
        return f"printf '%s\\n' {shlex.quote(label)} >> {shlex.quote(str(self.log_path))}\n{suffix}".rstrip()

    def test_workspace_lifecycle_runs_hooks_in_order_and_ignores_cleanup_failures(self):
        from symphonz.service.workspace import (
            prepare_workspace,
            remove_workspace,
            run_after_run_hook,
            run_before_run_hook,
            workspace_path,
        )

        workflow = self.workflow(
            {
                "after_create": self.append_hook("after_create"),
                "before_run": self.append_hook("before_run"),
                "after_run": self.append_hook("after_run", "exit 7"),
                "before_remove": self.append_hook("before_remove", "exit 9"),
            }
        )

        workspace = prepare_workspace(self.root, workflow, self.issue)
        self.assertEqual(workspace, workspace_path(self.root, workflow, self.issue))

        run_before_run_hook(workspace, workflow, self.issue)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            run_after_run_hook(workspace, workflow, self.issue)
            remove_workspace(workspace, workflow, self.issue)

        self.assertFalse(workspace.exists())
        self.assertEqual(
            self.log_path.read_text().splitlines(),
            ["after_create", "before_run", "after_run", "before_remove"],
        )
        self.assertIn("Ignoring after_run hook failure", stderr.getvalue())
        self.assertIn("Ignoring before_remove hook failure", stderr.getvalue())

    def test_fatal_hook_timeout_raises(self):
        from symphonz.service.workspace import prepare_workspace, run_before_run_hook

        workflow = self.workflow(
            {
                "timeout_ms": 10,
                "before_run": "python3 -c 'import time; time.sleep(0.1)'",
            }
        )

        workspace = prepare_workspace(self.root, workflow, self.issue)

        with self.assertRaises(subprocess.TimeoutExpired):
            run_before_run_hook(workspace, workflow, self.issue)

    def test_prepare_workspace_removes_partial_directory_when_after_create_fails(self):
        from symphonz.service.workspace import prepare_workspace, workspace_path

        workflow = self.workflow({"after_create": self.append_hook("after_create", "mkdir partial\nexit 3")})
        workspace = workspace_path(self.root, workflow, self.issue)

        with self.assertRaises(Exception):
            prepare_workspace(self.root, workflow, self.issue)

        self.assertFalse(workspace.exists())

    def test_prepare_workspace_rejects_preexisting_symlink_escaping_root(self):
        from symphonz.service.workspace import prepare_workspace, workspace_path

        workflow = self.workflow()
        outside = self.root / "outside"
        outside.mkdir()
        workspace = workspace_path(self.root, workflow, self.issue)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        workspace.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "Workspace path escapes root"):
            prepare_workspace(self.root, workflow, self.issue)


class CodexAppServerTests(unittest.TestCase):
    def write_server(self, root: Path, body: str) -> tuple[Path, Path]:
        server = root / "fake_app_server.py"
        records = root / "records.jsonl"
        server.write_text(body)
        return server, records

    def client(self, server: Path, **kwargs):
        from symphonz.service.codex_app_server import CodexAppServer

        return CodexAppServer(command=f"python3 {server}", **kwargs)

    def run_kwargs(self, root: Path) -> dict:
        return {
            "workspace": root,
            "prompt": "Do the work",
            "title": "SYM-1: Test",
            "approval_policy": "never",
            "thread_sandbox": "workspace-write",
            "turn_sandbox_policy": {"type": "workspaceWrite"},
        }

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

    def test_codex_app_server_reuses_thread_and_executes_dynamic_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "records.jsonl"
            server, _ = self.write_server(
                root,
                "import json, sys\n"
                f"records = open({str(records)!r}, 'a')\n"
                "turn = 0\n"
                "pending_tool = False\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); records.write(json.dumps(msg) + '\\n'); records.flush()\n"
                "    method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        turn += 1\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': f'turn-{turn}'}}}), flush=True)\n"
                "        if turn == 1:\n"
                "            pending_tool = True\n"
                "            print(json.dumps({'jsonrpc': '2.0', 'id': 900, 'method': 'item/tool/call', 'params': {'tool': 'linear_graphql', 'arguments': {'query': 'query One { viewer { id } }'}}}), flush=True)\n"
                "        else: print(json.dumps({'method': 'turn/completed', 'params': {}}), flush=True)\n"
                "    elif pending_tool and msg.get('id') == 900:\n"
                "        pending_tool = False\n"
                "        print(json.dumps({'method': 'turn/completed', 'params': {}}), flush=True)\n",
            )
            events = []
            executor_calls = []
            client = self.client(
                server,
                dynamic_tool_executor=lambda name, arguments: executor_calls.append((name, arguments))
                or {"success": True, "output": "ok", "contentItems": []},
            )

            result = client.run_turns(
                **self.run_kwargs(root),
                max_turns=2,
                should_continue=lambda: True,
                continuation_prompt=lambda turn: f"Continue turn {turn}",
                on_event=events.append,
            )

            messages = [json.loads(line) for line in records.read_text().splitlines()]
            thread_start = next(message for message in messages if message.get("method") == "thread/start")
            turn_starts = [message for message in messages if message.get("method") == "turn/start"]
            tool_reply = next(message for message in messages if message.get("id") == 900 and "result" in message)
            self.assertEqual(result["turn_count"], 2)
            self.assertEqual([message["params"]["threadId"] for message in turn_starts], ["thread-1", "thread-1"])
            self.assertEqual(thread_start["params"]["dynamicTools"][0]["name"], "linear_graphql")
            self.assertEqual(executor_calls[0][0], "linear_graphql")
            self.assertTrue(tool_reply["result"]["success"])

    def test_codex_app_server_times_out_waiting_for_initialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server, _ = self.write_server(root, "import time\ntime.sleep(5)\n")
            client = self.client(server, read_timeout_ms=30)

            with self.assertRaisesRegex(RuntimeError, "response_timeout"):
                client.run_turn(**self.run_kwargs(root))

    def test_codex_app_server_enforces_turn_and_stall_timeouts(self):
        server_source = (
            "import json, sys, time\n"
            "for line in sys.stdin:\n"
            "    msg = json.loads(line); method = msg.get('method')\n"
            "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
            "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
            "    elif method == 'turn/start':\n"
            "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
            "        time.sleep(5)\n"
        )
        cases = [(40, 1000, "turn_timeout"), (1000, 40, "stall_timeout")]
        for turn_timeout_ms, stall_timeout_ms, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                server, _ = self.write_server(root, server_source)
                client = self.client(server, turn_timeout_ms=turn_timeout_ms, stall_timeout_ms=stall_timeout_ms)
                with self.assertRaisesRegex(RuntimeError, expected):
                    client.run_turn(**self.run_kwargs(root))

    def test_codex_app_server_cancels_waiting_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server, _ = self.write_server(
                root,
                "import json, sys, time\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        time.sleep(5)\n",
            )
            cancel = threading.Event()
            timer = threading.Timer(0.05, cancel.set)
            timer.start()
            self.addCleanup(timer.cancel)
            client = self.client(server, turn_timeout_ms=2000, stall_timeout_ms=2000)

            with self.assertRaisesRegex(RuntimeError, "turn_cancelled"):
                client.run_turn(**self.run_kwargs(root), cancel_event=cancel)

    def test_codex_app_server_replies_to_unsupported_tool_then_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "records.jsonl"
            server, _ = self.write_server(
                root,
                "import json, sys\n"
                f"records = open({str(records)!r}, 'a')\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); records.write(json.dumps(msg) + '\\n'); records.flush(); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        print(json.dumps({'id': 901, 'method': 'item/tool/call', 'params': {'tool': 'unknown', 'arguments': {}}}), flush=True)\n"
                "    elif msg.get('id') == 901: print(json.dumps({'method': 'turn/completed', 'params': {}}), flush=True)\n",
            )
            events = []
            result = self.client(server).run_turn(**self.run_kwargs(root), on_event=events.append)

            replies = [json.loads(line) for line in records.read_text().splitlines() if json.loads(line).get("id") == 901]
            self.assertEqual(result["turn_id"], "turn-1")
            self.assertFalse(replies[-1]["result"]["success"])
            self.assertTrue(any(event["type"] == "unsupported_tool_call" for event in events))

    def test_codex_app_server_fails_noninteractive_user_input_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server, _ = self.write_server(
                root,
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        print(json.dumps({'id': 902, 'method': 'item/tool/requestUserInput', 'params': {'questions': []}}), flush=True)\n",
            )

            with self.assertRaisesRegex(RuntimeError, "turn_input_required"):
                self.client(server).run_turn(**self.run_kwargs(root))


class OrchestratorHardeningTests(unittest.TestCase):
    def setUp(self):
        from symphonz.service.models import WorkflowDefinition

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workflow = WorkflowDefinition(
            path=self.root / "WORKFLOW.md",
            config={
                "tracker": {
                    "active_states": ["Todo", "In Progress", "Ready to Publish", "Rework", "Merging"],
                    "terminal_states": ["Done", "Closed", "Cancelled", "Canceled", "Duplicate"],
                    "required_labels": [],
                },
                "workspace": {"root": ".symphonz/workspace"},
                "hooks": {},
                "agent": {"max_concurrent_agents": 2, "max_turns": 1, "max_retry_backoff_ms": 300000},
                "codex": {},
            },
            prompt_template="Work on {{ issue.identifier }}{% if attempt %} attempt {{ attempt }}{% endif %}",
        )

    def issue(self, number: int, state: str = "Todo", priority: int = 1):
        from symphonz.service.models import Issue

        return Issue(
            id=f"id-{number}",
            identifier=f"SYM-{number}",
            title=f"Issue {number}",
            state=state,
            priority=priority,
            created_at=f"2026-01-{number:02d}T00:00:00Z",
        )

    class FakeLinear:
        def __init__(self, issues):
            self.issues = {issue.id: issue for issue in issues}

        def fetch_candidate_issues(self, active_states):
            return [issue for issue in self.issues.values() if issue.state in active_states]

        def fetch_issues_by_ids(self, ids):
            return [self.issues[issue_id] for issue_id in ids if issue_id in self.issues]

        def fetch_issues_by_states(self, states):
            return [issue for issue in self.issues.values() if issue.state in states]

    class RecordingCodex:
        def __init__(self, wait_for_cancel=False, fail_first=False, sleep_seconds=0.03):
            self.wait_for_cancel = wait_for_cancel
            self.fail_first = fail_first
            self.sleep_seconds = sleep_seconds
            self.calls = []
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def run_turns(self, **kwargs):
            with self.lock:
                self.calls.append(kwargs)
                call_number = len(self.calls)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                kwargs["on_event"]({"type": "session_started", "session_id": f"thread-{call_number}-turn-1", "turn_count": 1})
                if self.wait_for_cancel:
                    if not kwargs["cancel_event"].wait(timeout=2):
                        raise RuntimeError("cancel was not requested")
                    raise RuntimeError("turn_cancelled")
                time.sleep(self.sleep_seconds)
                if self.fail_first and call_number == 1:
                    raise RuntimeError("temporary failure")
                return {
                    "session_id": f"thread-{call_number}-turn-1",
                    "thread_id": f"thread-{call_number}",
                    "turn_id": "turn-1",
                    "turn_count": 1,
                    "result": {},
                }
            finally:
                with self.lock:
                    self.active -= 1

    def test_tick_dispatches_with_bounded_concurrency_and_no_duplicate_claims(self):
        from symphonz.service.orchestrator import Orchestrator

        issues = [self.issue(1), self.issue(2)]
        linear = self.FakeLinear(issues)
        codex = self.RecordingCodex(sleep_seconds=0.08)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        self.assertEqual(len(codex.calls), 2)
        self.assertEqual(codex.max_active, 2)
        self.assertEqual(orchestrator.state.snapshot()["counts"]["claimed"], 2)

    def test_tick_prioritizes_unblocked_issues(self):
        from dataclasses import replace
        from symphonz.service.models import BlockerRef
        from symphonz.service.orchestrator import Orchestrator

        self.workflow.config["agent"]["max_concurrent_agents"] = 1
        lower_priority = self.issue(1, priority=3)
        higher_priority = self.issue(2, priority=1)
        selected = self.issue(3, priority=2)
        blocked = replace(higher_priority, blocked_by=[BlockerRef("blocker", "SYM-9", "In Progress")])
        linear = self.FakeLinear([lower_priority, blocked, selected])
        codex = self.RecordingCodex(sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        self.assertEqual(codex.calls[0]["title"], "SYM-3: Issue 3")

    def test_input_required_blocks_without_immediate_redispatch(self):
        from symphonz.service.orchestrator import Orchestrator

        class BlockedCodex(self.RecordingCodex):
            def run_turns(self, **kwargs):
                self.calls.append(kwargs)
                raise RuntimeError("turn_input_required")

        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        codex = BlockedCodex()
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        snapshot = orchestrator.state.snapshot()
        self.assertEqual(len(codex.calls), 1)
        self.assertEqual(snapshot["counts"]["blocked"], 1)
        self.assertEqual(snapshot["counts"]["claimed"], 1)

    def test_failure_uses_exponential_retry_and_passes_attempt_to_prompt(self):
        from symphonz.service.orchestrator import Orchestrator

        now = [100.0]
        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        codex = self.RecordingCodex(fail_first=True, sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex, clock=lambda: now[0])
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        retry = orchestrator.state.snapshot()["retrying"][0]
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["due_at"], 110.0)

        now[0] = 109.0
        orchestrator.tick()
        self.assertEqual(len(codex.calls), 1)
        now[0] = 110.0
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        self.assertEqual(len(codex.calls), 2)
        self.assertIn("attempt 1", codex.calls[1]["prompt"])

    def test_reconciliation_cancels_terminal_run_and_removes_workspace(self):
        from dataclasses import replace
        from symphonz.service.orchestrator import Orchestrator
        from symphonz.service.workspace import workspace_path

        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        codex = self.RecordingCodex(wait_for_cancel=True)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        deadline = time.time() + 2
        while not orchestrator.state.snapshot()["running"] and time.time() < deadline:
            time.sleep(0.01)
        workspace = workspace_path(self.root, self.workflow, issue)
        while not workspace.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(workspace.exists())

        linear.issues[issue.id] = replace(issue, state="Done")
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        self.assertFalse(workspace.exists())
        self.assertEqual(orchestrator.state.snapshot()["counts"]["claimed"], 0)

    def test_runtime_events_are_written_as_json_lines(self):
        from symphonz.service.event_log import JsonlEventLog
        from symphonz.service.models import RuntimeState

        path = self.root / "logs" / "runtime.jsonl"
        state = RuntimeState(event_sink=JsonlEventLog(path).write)

        state.add_event("service_started", "started", issue_identifier="SYM-1", detail="value")

        payload = json.loads(path.read_text().strip())
        self.assertEqual(payload["type"], "service_started")
        self.assertEqual(payload["issue_identifier"], "SYM-1")
        self.assertEqual(payload["data"], {"detail": "value"})

    def test_shutdown_releases_workers_without_scheduling_retry(self):
        from symphonz.service.orchestrator import Orchestrator

        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        codex = self.RecordingCodex(wait_for_cancel=True)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        orchestrator.tick()

        orchestrator.shutdown()

        snapshot = orchestrator.state.snapshot()
        self.assertEqual(snapshot["counts"]["claimed"], 0)
        self.assertEqual(snapshot["counts"]["retrying"], 0)


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

            def run_turns(self, workspace, prompt, title, approval_policy, thread_sandbox, turn_sandbox_policy, on_event, **kwargs):
                self.prompts.append(prompt)
                on_event({"type": "turn_completed", "params": {}})
                return {
                    "session_id": "thread-turn",
                    "thread_id": "thread",
                    "turn_id": "turn",
                    "turn_count": 1,
                    "result": {},
                }

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
