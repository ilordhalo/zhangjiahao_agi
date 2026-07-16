from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
import json
import os
import shlex
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


class WorkflowServiceTests(unittest.TestCase):
    def test_load_workflow_parses_current_front_matter(self):
        from symphonz.service.workflow import load_workflow

        workflow = load_workflow(Path("WORKFLOW.md"))

        self.assertEqual(workflow.config["tracker"]["kind"], "linear")
        self.assertEqual(workflow.config["workspace"]["root"], ".symphonz/workspace")
        self.assertEqual(workflow.config["agent"]["max_attempts"], 5)
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
    def test_dynamic_tool_router_advertises_and_dispatches_report_tool(self):
        from symphonz.service.dynamic_tools import dynamic_tool_specs, execute_dynamic_tool

        class Publisher:
            def publish(self, arguments):
                self.arguments = arguments
                return {"success": True, "report_url": "https://reports.example.test/issues/SYM-1/report"}

        publisher = Publisher()
        specs = dynamic_tool_specs(report_publisher=publisher)
        result = execute_dynamic_tool(
            "symphonz_report",
            {"operation": "publish"},
            linear_client=None,
            report_publisher=publisher,
        )

        self.assertEqual([spec["name"] for spec in specs], ["linear_graphql", "symphonz_report"])
        self.assertTrue(result["success"])
        self.assertEqual(publisher.arguments, {"operation": "publish"})

    def test_dynamic_tool_specs_keep_unavailable_report_publishing_explicit(self):
        from symphonz.service.dynamic_tools import dynamic_tool_specs, execute_dynamic_tool

        specs = dynamic_tool_specs(report_publisher=None)
        result = execute_dynamic_tool(
            "symphonz_report",
            {"operation": "publish"},
            linear_client=None,
            report_publisher=None,
        )

        self.assertEqual([spec["name"] for spec in specs], ["linear_graphql", "symphonz_report"])
        self.assertFalse(result["success"])
        self.assertIn("Report publisher is unavailable", result["output"])

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

    @unittest.skipUnless(os.name == "posix", "process-group timeout is POSIX-specific")
    def test_hook_timeout_terminates_descendant_processes(self):
        from symphonz.service.workspace import prepare_workspace, run_before_run_hook

        sentinel = self.root / "hook-child-survived.txt"
        child_code = (
            "import time, pathlib; time.sleep(0.3); "
            f"pathlib.Path({str(sentinel)!r}).write_text('survived')"
        )
        hook = f"python3 -c {shlex.quote(child_code)} & wait"
        workflow = self.workflow({"timeout_ms": 20, "before_run": hook})
        workspace = prepare_workspace(self.root, workflow, self.issue)

        with self.assertRaises(subprocess.TimeoutExpired):
            run_before_run_hook(workspace, workflow, self.issue)
        time.sleep(0.4)
        self.assertFalse(sentinel.exists())

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

        with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
            prepare_workspace(self.root, workflow, self.issue)

    def test_prepare_workspace_rejects_symlink_to_sibling_workspace(self):
        from symphonz.service.workspace import prepare_workspace, workspace_path

        workflow = self.workflow()
        sibling = self.workspace_root / "SYM-2"
        sibling.mkdir(parents=True)
        (sibling / "keep.txt").write_text("keep")
        workspace = workspace_path(self.root, workflow, self.issue)
        workspace.symlink_to(sibling, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
            prepare_workspace(self.root, workflow, self.issue)
        self.assertEqual((sibling / "keep.txt").read_text(), "keep")

    def test_remove_missing_workspace_is_idempotent_and_skips_hook(self):
        from symphonz.service.workspace import remove_workspace, workspace_path

        workflow = self.workflow({"before_remove": self.append_hook("before_remove")})
        workspace = workspace_path(self.root, workflow, self.issue)

        remove_workspace(workspace, workflow, self.issue)

        self.assertFalse(self.log_path.exists())


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

    def test_codex_app_server_preserves_completion_arriving_before_turn_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server, _ = self.write_server(
                root,
                "import json, sys\n"
                "turn_request = None\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        turn_request = msg['id']\n"
                "        print(json.dumps({'id': 777, 'method': 'item/tool/call', 'params': {'tool': 'linear_graphql', 'arguments': {'query': 'query Early { viewer { id } }'}}}), flush=True)\n"
                "    elif msg.get('id') == 777:\n"
                "        print(json.dumps({'method': 'turn/completed', 'params': {'usage': {'totalTokens': 3}}}), flush=True)\n"
                "        print(json.dumps({'id': turn_request, 'result': {'turn': {'id': 'turn-early'}}}), flush=True)\n",
            )
            calls = []

            result = self.client(
                server,
                read_timeout_ms=200,
                dynamic_tool_executor=lambda name, arguments: calls.append((name, arguments))
                or {"success": True, "output": "ok", "contentItems": []},
            ).run_turn(**self.run_kwargs(root))

            self.assertEqual(result["turn_id"], "turn-early")
            self.assertEqual(result["result"]["usage"]["totalTokens"], 3)
            self.assertEqual(calls[0][0], "linear_graphql")

    def test_never_approval_policy_rejects_unexpected_approval_request(self):
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
                "        print(json.dumps({'id': 950, 'method': 'item/commandExecution/requestApproval', 'params': {}}), flush=True)\n"
                "        tail = sys.stdin.read()\n"
                "        records.write(tail); records.flush()\n"
                "        break\n",
            )

            with self.assertRaisesRegex(RuntimeError, "approval_required"):
                self.client(server).run_turn(**self.run_kwargs(root))

            replies = [json.loads(line) for line in records.read_text().splitlines() if json.loads(line).get("id") == 950]
            self.assertEqual(replies[-1]["result"]["decision"], "decline")

    @unittest.skipUnless(os.name == "posix", "process watcher fallback is POSIX-specific")
    def test_approval_decline_survives_process_watcher_constructor_errors(self):
        import fcntl
        import signal
        import symphonz.service.codex_app_server as codex_app_server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / "descendant.lock"
            pid_path = root / "descendant.pid"
            records = root / "records.jsonl"
            server, _ = self.write_server(
                root,
                "import json, os, signal, subprocess, sys\n"
                f"lock_path = {str(lock_path)!r}\n"
                f"pid_path = {str(pid_path)!r}\n"
                f"records = open({str(records)!r}, 'a')\n"
                "child_code = '''import fcntl, os, signal, sys\n"
                "lock = open(sys.argv[1], 'w')\n"
                "fcntl.flock(lock, fcntl.LOCK_EX)\n"
                "os.write(int(sys.argv[2]), b'1')\n"
                "signal.pause()\n'''\n"
                "ready_read, ready_write = os.pipe()\n"
                "child = subprocess.Popen([sys.executable, '-c', child_code, lock_path, str(ready_write)], pass_fds=(ready_write,))\n"
                "os.close(ready_write)\n"
                "os.read(ready_read, 1); os.close(ready_read)\n"
                "with open(pid_path, 'w') as output: output.write(str(child.pid))\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); records.write(json.dumps(msg) + '\\n'); records.flush(); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        print(json.dumps({'id': 953, 'method': 'item/commandExecution/requestApproval', 'params': {}}), flush=True)\n"
                "        tail = sys.stdin.read()\n"
                "        records.write(tail); records.flush()\n"
                "        break\n",
            )

            try:
                with (
                    mock.patch.object(
                        codex_app_server.os,
                        "pidfd_open",
                        side_effect=OSError("pidfd unsupported"),
                        create=True,
                    ) as pidfd_open,
                    mock.patch.object(
                        codex_app_server.select,
                        "kqueue",
                        side_effect=OSError("kqueue unsupported"),
                        create=True,
                    ) as kqueue,
                ):
                    with self.assertRaisesRegex(RuntimeError, "approval_required"):
                        self.client(server).run_turn(**self.run_kwargs(root))

                pidfd_open.assert_called()
                kqueue.assert_called()
                replies = [
                    json.loads(line)
                    for line in records.read_text().splitlines()
                    if json.loads(line).get("id") == 953
                ]
                self.assertEqual(replies[-1]["result"]["decision"], "decline")
                with lock_path.open("w") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                if pid_path.exists():
                    try:
                        os.kill(int(pid_path.read_text()), signal.SIGKILL)
                    except (PermissionError, ProcessLookupError):
                        pass

    @unittest.skipUnless(os.name == "posix", "process watcher fallback is POSIX-specific")
    def test_approval_decline_uses_bounded_fallback_without_process_watchers(self):
        import symphonz.service.codex_app_server as codex_app_server

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
                "        print(json.dumps({'id': 954, 'method': 'item/commandExecution/requestApproval', 'params': {}}), flush=True)\n"
                "        tail = sys.stdin.read()\n"
                "        records.write(tail); records.flush()\n"
                "        break\n",
            )

            with (
                mock.patch.object(codex_app_server.os, "pidfd_open", None, create=True),
                mock.patch.object(codex_app_server.select, "kqueue", None, create=True),
                mock.patch.object(codex_app_server.os, "waitid", None, create=True),
                mock.patch.object(
                    codex_app_server,
                    "_wait_with_libc_waitid",
                    return_value=None,
                    create=True,
                ),
            ):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "approval_required"):
                    self.client(server).run_turn(**self.run_kwargs(root))
                elapsed = time.monotonic() - started

            replies = [
                json.loads(line)
                for line in records.read_text().splitlines()
                if json.loads(line).get("id") == 954
            ]
            self.assertEqual(replies[-1]["result"]["decision"], "decline")
            self.assertLess(elapsed, 1)

    def test_never_approval_policy_preserves_decline_when_event_callback_raises(self):
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
                "        print(json.dumps({'id': 951, 'method': 'item/commandExecution/requestApproval', 'params': {}}), flush=True)\n"
                "        tail = sys.stdin.read()\n"
                "        records.write(tail); records.flush()\n"
                "        break\n",
            )

            def raise_on_rejection(event):
                if event["type"] == "approval_rejected":
                    raise RuntimeError("event callback failed")

            with self.assertRaisesRegex(RuntimeError, "approval_required"):
                self.client(server).run_turn(
                    **self.run_kwargs(root), on_event=raise_on_rejection
                )

            replies = [
                json.loads(line)
                for line in records.read_text().splitlines()
                if json.loads(line).get("id") == 951
            ]
            self.assertEqual(replies[-1]["result"]["decision"], "decline")

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-specific")
    def test_graceful_approval_decline_terminates_pipe_holding_descendant(self):
        import fcntl
        import signal

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / "descendant.lock"
            pid_path = root / "descendant.pid"
            server, _ = self.write_server(
                root,
                "import json, os, signal, subprocess, sys\n"
                f"lock_path = {str(lock_path)!r}\n"
                f"pid_path = {str(pid_path)!r}\n"
                "child_code = '''import fcntl, os, signal, sys\n"
                "lock = open(sys.argv[1], 'w')\n"
                "fcntl.flock(lock, fcntl.LOCK_EX)\n"
                "os.write(int(sys.argv[2]), b'1')\n"
                "signal.pause()\n'''\n"
                "ready_read, ready_write = os.pipe()\n"
                "child = subprocess.Popen([sys.executable, '-c', child_code, lock_path, str(ready_write)], pass_fds=(ready_write,))\n"
                "os.close(ready_write)\n"
                "os.read(ready_read, 1); os.close(ready_read)\n"
                "with open(pid_path, 'w') as output: output.write(str(child.pid))\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        print(json.dumps({'id': 952, 'method': 'item/commandExecution/requestApproval', 'params': {}}), flush=True)\n"
                "        sys.stdin.read()\n"
                "        break\n",
            )
            approval_rejected = threading.Event()

            def capture_rejection(event):
                if event["type"] == "approval_rejected":
                    approval_rejected.set()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    self.client(server).run_turn,
                    **self.run_kwargs(root),
                    on_event=capture_rejection,
                )
                self.assertTrue(approval_rejected.wait(timeout=2))
                child_pid = int(pid_path.read_text())
                try:
                    with self.assertRaisesRegex(RuntimeError, "approval_required"):
                        future.result(timeout=2)
                finally:
                    if not future.done():
                        os.kill(child_pid, signal.SIGKILL)
                        try:
                            future.result(timeout=2)
                        except RuntimeError:
                            pass

            with lock_path.open("w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.kill(child_pid, signal.SIGKILL)
                    self.fail("descendant still holds its lifecycle lock")

    def test_codex_app_server_routes_concurrent_per_run_report_executors(self):
        from symphonz.service.dynamic_tools import dynamic_tool_specs, linear_graphql_tool_spec
        from symphonz.service.reporting import report_tool_spec

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "records.jsonl"
            server, _ = self.write_server(
                root,
                "import json, os, sys\n"
                f"records_path = {str(records)!r}\n"
                "tool_specs = []\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start':\n"
                "        tool_specs = msg['params']['dynamicTools']\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': f'thread-{os.getpid()}'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        issue = msg['params']['input'][0]['text']\n"
                "        with open(records_path, 'a') as output:\n"
                "            output.write(json.dumps({'issue': issue, 'dynamicTools': tool_specs}) + '\\n')\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        print(json.dumps({'id': 900, 'method': 'item/tool/call', 'params': {'tool': 'symphonz_report', 'arguments': {'issue': issue}}}), flush=True)\n"
                "    elif msg.get('id') == 900:\n"
                "        print(json.dumps({'method': 'turn/completed', 'params': {}}), flush=True)\n",
            )
            default_specs = [linear_graphql_tool_spec()]
            default_calls = []
            client = self.client(
                server,
                dynamic_tool_specs=default_specs,
                dynamic_tool_executor=lambda name, arguments: default_calls.append((name, arguments)),
            )
            barrier = threading.Barrier(2)
            calls = {"SYM-1": [], "SYM-2": []}

            def executor_for(issue):
                def execute(name, arguments):
                    barrier.wait(timeout=2)
                    calls[issue].append((name, arguments))
                    return {"success": True, "output": issue, "contentItems": []}

                return execute

            def run(issue, specs):
                kwargs = self.run_kwargs(root)
                kwargs["prompt"] = issue
                return client.run_turns(
                    **kwargs,
                    max_turns=1,
                    should_continue=lambda: False,
                    continuation_prompt=lambda _turn: "unused",
                    dynamic_tool_specs=specs,
                    dynamic_tool_executor=executor_for(issue),
                )

            specs_by_issue = {
                "SYM-1": [report_tool_spec()],
                "SYM-2": dynamic_tool_specs(report_publisher=None),
            }
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run, issue, specs) for issue, specs in specs_by_issue.items()]
                results = [future.result(timeout=5) for future in futures]

            advertised = {
                record["issue"]: record["dynamicTools"]
                for record in map(json.loads, records.read_text().splitlines())
            }
            self.assertEqual([result["turn_count"] for result in results], [1, 1])
            self.assertEqual(advertised, specs_by_issue)
            self.assertEqual(calls["SYM-1"], [("symphonz_report", {"issue": "SYM-1"})])
            self.assertEqual(calls["SYM-2"], [("symphonz_report", {"issue": "SYM-2"})])
            self.assertEqual(default_specs, [linear_graphql_tool_spec()])
            self.assertEqual(default_calls, [])

    def test_codex_app_server_rejects_unadvertised_tool_without_executor_call(self):
        from symphonz.service.dynamic_tools import linear_graphql_tool_spec

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
                "        print(json.dumps({'id': 903, 'method': 'item/tool/call', 'params': {'tool': 'symphonz_report', 'arguments': {'operation': 'publish'}}}), flush=True)\n"
                "    elif msg.get('id') == 903: print(json.dumps({'method': 'turn/completed', 'params': {}}), flush=True)\n",
            )
            executor_calls = []
            events = []

            result = self.client(server).run_turn(
                **self.run_kwargs(root),
                dynamic_tool_specs=[linear_graphql_tool_spec()],
                dynamic_tool_executor=lambda name, arguments: executor_calls.append((name, arguments)),
                on_event=events.append,
            )

            messages = [json.loads(line) for line in records.read_text().splitlines()]
            thread_start = next(message for message in messages if message.get("method") == "thread/start")
            tool_reply = next(message for message in messages if message.get("id") == 903 and "result" in message)
            self.assertEqual(result["turn_id"], "turn-1")
            self.assertEqual(thread_start["params"]["dynamicTools"], [linear_graphql_tool_spec()])
            self.assertEqual(executor_calls, [])
            self.assertEqual(
                tool_reply["result"],
                {
                    "success": False,
                    "output": "Unsupported dynamic tool: symphonz_report",
                    "contentItems": [
                        {"type": "inputText", "text": "Unsupported dynamic tool: symphonz_report"}
                    ],
                },
            )
            self.assertTrue(any(event["type"] == "unsupported_tool_call" for event in events))

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

    @unittest.skipUnless(os.name == "posix", "process-group cancellation is POSIX-specific")
    def test_codex_app_server_cancellation_terminates_child_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinel = root / "child-survived.txt"
            child_code = (
                "import time, pathlib; time.sleep(0.4); "
                f"pathlib.Path({str(sentinel)!r}).write_text('survived')"
            )
            server, _ = self.write_server(
                root,
                "import json, subprocess, sys, time\n"
                f"child = {child_code!r}\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line); method = msg.get('method')\n"
                "    if method == 'initialize': print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
                "    elif method == 'thread/start': print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
                "    elif method == 'turn/start':\n"
                "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
                "        subprocess.Popen([sys.executable, '-c', child])\n"
                "        time.sleep(5)\n",
            )
            cancel = threading.Event()
            timer = threading.Timer(0.05, cancel.set)
            timer.start()
            self.addCleanup(timer.cancel)

            with self.assertRaisesRegex(RuntimeError, "turn_cancelled"):
                self.client(server, turn_timeout_ms=2000).run_turn(
                    **self.run_kwargs(root), cancel_event=cancel
                )
            time.sleep(0.5)
            self.assertFalse(sentinel.exists())

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
                "agent": {
                    "max_concurrent_agents": 2,
                    "max_turns": 1,
                    "max_attempts": 5,
                    "max_retry_backoff_ms": 300000,
                },
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
        self.assertGreater(retry["due_at_epoch"], 1_000_000_000)

        now[0] = 109.0
        orchestrator.tick()
        self.assertEqual(len(codex.calls), 1)
        now[0] = 110.0
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        self.assertEqual(len(codex.calls), 2)
        self.assertIn("attempt 1", codex.calls[1]["prompt"])

    def test_failures_block_at_attempt_limit_until_issue_state_changes(self):
        from dataclasses import replace
        from symphonz.service.orchestrator import Orchestrator

        class AlwaysFailCodex(self.RecordingCodex):
            def run_turns(self, **kwargs):
                self.calls.append(kwargs)
                raise RuntimeError("persistent failure")

        self.workflow.config["agent"]["max_attempts"] = 2
        now = [0.0]
        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        codex = AlwaysFailCodex()
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex, clock=lambda: now[0])
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        now[0] = 10.0
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        snapshot = orchestrator.state.snapshot()
        self.assertEqual(len(codex.calls), 2)
        self.assertEqual(snapshot["counts"]["retrying"], 0)
        self.assertEqual(snapshot["blocked"][0]["error"], "attempt_limit_exceeded")
        orchestrator.tick()
        self.assertEqual(len(codex.calls), 2)

        linear.issues[issue.id] = replace(issue, state="In Progress")
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        self.assertEqual(len(codex.calls), 3)
        self.assertNotIn("attempt", codex.calls[-1]["prompt"])

    def test_clean_continuations_increment_attempt_and_respect_limit(self):
        from symphonz.service.orchestrator import Orchestrator

        self.workflow.config["agent"]["max_attempts"] = 2
        now = [0.0]
        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        codex = self.RecordingCodex(sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex, clock=lambda: now[0])
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        self.assertEqual(orchestrator.state.snapshot()["retrying"][0]["attempt"], 1)
        now[0] = 1.0
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)

        snapshot = orchestrator.state.snapshot()
        self.assertEqual(len(codex.calls), 2)
        self.assertEqual(snapshot["blocked"][0]["error"], "attempt_limit_exceeded")

    def test_active_state_change_resets_continuation_attempt_budget(self):
        from dataclasses import replace
        from symphonz.service.orchestrator import Orchestrator

        issue = self.issue(1)
        linear = self.FakeLinear([issue])

        class StateChangingCodex(self.RecordingCodex):
            def run_turns(inner_self, **kwargs):
                result = super().run_turns(**kwargs)
                linear.issues[issue.id] = replace(issue, state="In Progress")
                return result

        codex = StateChangingCodex(sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.poll_once()

        retry = orchestrator.state.snapshot()["retrying"][0]
        self.assertEqual(retry["state"], "In Progress")
        self.assertEqual(retry["attempt"], 0)

    def test_attempt_limit_survives_service_restart(self):
        from symphonz.service.attempt_store import AttemptStore
        from symphonz.service.orchestrator import Orchestrator

        self.workflow.config["agent"]["max_attempts"] = 1
        issue = self.issue(1)
        linear = self.FakeLinear([issue])
        store_path = self.root / ".symphonz" / "logs" / "attempts.sqlite3"

        first_codex = self.RecordingCodex(sleep_seconds=0)
        first = Orchestrator(
            self.root,
            self.workflow,
            linear,
            first_codex,
            attempt_store=AttemptStore(store_path),
        )
        first.tick()
        first.wait_for_idle(timeout=2)
        first.shutdown()

        second_codex = self.RecordingCodex(sleep_seconds=0)
        second = Orchestrator(
            self.root,
            self.workflow,
            linear,
            second_codex,
            attempt_store=AttemptStore(store_path),
        )
        self.addCleanup(second.shutdown)
        second.tick()

        self.assertEqual(len(first_codex.calls), 1)
        self.assertEqual(len(second_codex.calls), 0)
        self.assertEqual(second.state.snapshot()["blocked"][0]["error"], "attempt_limit_exceeded")

    def test_attempt_store_reservation_is_atomic_across_instances(self):
        from concurrent.futures import ThreadPoolExecutor
        from symphonz.service.attempt_store import AttemptStore

        store_path = self.root / ".symphonz" / "logs" / "attempts.sqlite3"
        stores = [AttemptStore(store_path), AttemptStore(store_path)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda store: store.consume("id-1", "todo", 1), stores))

        self.assertEqual(sorted(results, key=lambda value: value is None), [0, None])

    def test_attempt_limit_survives_eligibility_round_trip_in_same_state(self):
        from dataclasses import replace
        from symphonz.service.orchestrator import Orchestrator

        self.workflow.config["agent"]["max_attempts"] = 1
        self.workflow.config["tracker"]["required_labels"] = ["symphonz"]
        issue = replace(self.issue(1), labels=["symphonz"])
        linear = self.FakeLinear([issue])
        codex = self.RecordingCodex(sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        linear.issues[issue.id] = replace(issue, labels=[])
        orchestrator.tick()
        linear.issues[issue.id] = issue
        orchestrator.tick()

        self.assertEqual(len(codex.calls), 1)
        self.assertEqual(orchestrator.state.snapshot()["blocked"][0]["error"], "attempt_limit_exceeded")

    def test_linear_retry_refresh_failure_does_not_consume_codex_attempt(self):
        from symphonz.service.orchestrator import Orchestrator

        class FlakyRefreshLinear(self.FakeLinear):
            def __init__(inner_self, issues):
                super().__init__(issues)
                inner_self.fail_refresh = False

            def fetch_issues_by_ids(inner_self, ids):
                if inner_self.fail_refresh:
                    inner_self.fail_refresh = False
                    raise RuntimeError("Linear unavailable")
                return super().fetch_issues_by_ids(ids)

        self.workflow.config["agent"]["max_attempts"] = 2
        now = [0.0]
        issue = self.issue(1)
        linear = FlakyRefreshLinear([issue])
        codex = self.RecordingCodex(fail_first=True, sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, linear, codex, clock=lambda: now[0])
        self.addCleanup(orchestrator.shutdown)

        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        linear.fail_refresh = True
        now[0] = 10.0
        orchestrator.tick()

        retry = orchestrator.state.snapshot()["retrying"][0]
        self.assertEqual(len(codex.calls), 1)
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["tracker_retry_count"], 1)
        self.assertTrue(any(event["type"] == "tracker_poll_failed" for event in orchestrator.state.snapshot()["events"]))

        now[0] = retry["due_at"]
        orchestrator.tick()
        orchestrator.wait_for_idle(timeout=2)
        self.assertEqual(len(codex.calls), 2)

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

    def test_worker_completion_in_terminal_state_removes_workspace_immediately(self):
        from dataclasses import replace
        from symphonz.service.orchestrator import Orchestrator
        from symphonz.service.workspace import workspace_path

        issue = self.issue(1, state="Merging")
        terminal = replace(issue, state="Done")

        class LinearAtCompletion(self.FakeLinear):
            def fetch_candidate_issues(inner_self, active_states):
                return [issue]

            def fetch_issues_by_ids(inner_self, ids):
                return [terminal]

        codex = self.RecordingCodex(sleep_seconds=0)
        orchestrator = Orchestrator(self.root, self.workflow, LinearAtCompletion([issue]), codex)
        self.addCleanup(orchestrator.shutdown)

        orchestrator.poll_once()

        self.assertFalse(workspace_path(self.root, self.workflow, issue).exists())
        snapshot = orchestrator.state.snapshot()
        self.assertEqual(snapshot["completed"][0]["state"], "Done")
        self.assertTrue(any(event["type"] == "workspace_removed" for event in snapshot["events"]))

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


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "logs" / "runtime.sqlite3"

    def test_runtime_store_persists_tasks_events_and_errors_across_instances(self):
        from symphonz.service.models import RuntimeErrorRecord, RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.upsert_issue(
            {
                "issue_identifier": "SYM-1",
                "issue_id": "issue-1",
                "title": "Persist runtime history",
                "linear_state": "Todo",
                "status": "running",
                "attempt": 2,
                "workspace": "/tmp/SYM-1",
            }
        )
        store.record_event(RuntimeEvent("issue_started", "Started", "SYM-1", data={"category": "lifecycle"}))
        error_id = store.record_error(
            RuntimeErrorRecord(
                issue_identifier="SYM-1",
                session_id="session-1",
                stage="codex",
                error_type="RuntimeError",
                message="boom",
                retryable=True,
                attempt=2,
                context={"request": {"authorization": "Bearer secret"}},
            )
        )

        reopened = RuntimeStore(self.path)

        self.assertEqual(reopened.get_task("SYM-1")["status"], "running")
        self.assertEqual(reopened.get_task("SYM-1")["title"], "Persist runtime history")
        self.assertEqual(reopened.list_events(issue_identifier="SYM-1")["items"][0]["type"], "issue_started")
        error = reopened.list_errors(issue_identifier="SYM-1")["items"][0]
        self.assertEqual(error["id"], error_id)
        self.assertEqual(error["message"], "boom")
        self.assertEqual(error["context"]["request"]["authorization"], "[REDACTED]")

    def test_task_filters_and_cursor_pagination_use_persisted_rows(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.upsert_issue({"issue_identifier": "SYM-1", "title": "First dashboard", "status": "completed"})
        store.upsert_issue({"issue_identifier": "SYM-2", "title": "Runtime dashboard", "status": "running"})
        store.upsert_issue({"issue_identifier": "SYM-3", "title": "Another runtime", "status": "running"})

        filtered = store.list_tasks(status="running", query="runtime", cursor=None, limit=1)
        remaining = store.list_tasks(status="running", query="runtime", cursor=filtered["next_cursor"], limit=1)

        self.assertEqual(len(filtered["items"]), 1)
        self.assertIsNotNone(filtered["next_cursor"])
        self.assertEqual(len(remaining["items"]), 1)
        self.assertIsNone(remaining["next_cursor"])
        self.assertEqual(
            {filtered["items"][0]["issue_identifier"], remaining["items"][0]["issue_identifier"]},
            {"SYM-2", "SYM-3"},
        )

    def test_upserting_a_task_updates_its_last_activity_timestamp(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.upsert_issue({"issue_identifier": "SYM-1", "status": "queued"})
        first_updated_at = store.get_task("SYM-1")["updated_at"]
        time.sleep(0.001)

        store.upsert_issue({"issue_identifier": "SYM-1", "status": "running"})

        self.assertGreater(store.get_task("SYM-1")["updated_at"], first_updated_at)

    def test_events_errors_and_jsonl_logs_redact_secrets_and_errors_resolve(self):
        from symphonz.service.event_log import CompositeEventSink, ErrorJsonlLog, JsonlEventLog
        from symphonz.service.models import RuntimeErrorRecord, RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        runtime_path = self.path.parent / "runtime.jsonl"
        errors_path = self.path.parent / "errors.jsonl"
        event = RuntimeEvent(
            "tool_failed",
            "Tool failed",
            "SYM-1",
            data={"api_key": "top-secret", "nested": [{"password": "hidden"}]},
        )
        error_log = ErrorJsonlLog(errors_path)
        CompositeEventSink(JsonlEventLog(runtime_path).write, store.record_event, error_log.write).write(event)

        event_payload = json.loads(runtime_path.read_text().strip())
        error_payload = json.loads(errors_path.read_text().strip())
        persisted_event = store.list_events(issue_identifier="SYM-1")["items"][0]
        self.assertEqual(event_payload["data"]["api_key"], "[REDACTED]")
        self.assertEqual(event_payload["data"]["nested"][0]["password"], "[REDACTED]")
        self.assertEqual(error_payload["context"]["api_key"], "[REDACTED]")
        self.assertEqual(persisted_event["data"]["api_key"], "[REDACTED]")

        error_id = store.list_errors(issue_identifier="SYM-1")["items"][0]["id"]
        store.resolve_error(error_id, resolving_event="tool_completed")

        self.assertEqual(store.list_errors(resolved=False)["items"], [])
        self.assertEqual(store.list_errors(resolved=True)["items"][0]["resolving_event"], "tool_completed")

    def test_wal_store_accepts_concurrent_event_writers(self):
        from symphonz.service.models import RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        RuntimeStore(self.path)
        failures = []

        def record(index):
            try:
                RuntimeStore(self.path).record_event(RuntimeEvent("tool_completed", str(index), "SYM-1"))
            except Exception as error:  # pragma: no cover - asserted below
                failures.append(error)

        writers = [threading.Thread(target=record, args=(index,)) for index in range(12)]
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join()

        self.assertEqual(failures, [])
        self.assertEqual(len(RuntimeStore(self.path).list_events(issue_identifier="SYM-1", limit=20)["items"]), 12)

    def test_reports_sessions_and_login_attempts_are_persisted(self):
        from symphonz.service.runtime_store import RuntimeStore

        now = time.time()
        store = RuntimeStore(self.path)
        store.save_report(
            {
                "issue_identifier": "SYM-1",
                "report_version": 1,
                "json_path": "/tmp/report.json",
                "html_path": "/tmp/report.html",
                "url": "https://reports.example/SYM-1",
                "sync_status": "pending",
            }
        )
        store.save_session("token-hash", expires_at=now + 60, metadata={"user": "admin"})
        store.record_login_attempt("127.0.0.1", failures=2, window_started_at=now, locked_until=now + 30)

        reopened = RuntimeStore(self.path)

        self.assertEqual(reopened.get_report("SYM-1")["url"], "https://reports.example/SYM-1")
        self.assertEqual(reopened.get_session("token-hash")["metadata"], {"user": "admin"})
        self.assertEqual(reopened.get_login_attempt("127.0.0.1")["failures"], 2)
        reopened.clear_login_attempt("127.0.0.1")
        self.assertIsNone(reopened.get_login_attempt("127.0.0.1"))

    def test_nested_codex_failures_are_normalized_for_sqlite_and_error_jsonl(self):
        from symphonz.service.event_log import CompositeEventSink, ErrorJsonlLog
        from symphonz.service.models import RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.upsert_issue({"issue_identifier": "SYM-1", "status": "running"})
        errors_path = self.path.parent / "errors.jsonl"
        event = RuntimeEvent(
            "codex_event",
            "tool_call_failed",
            "SYM-1",
            data={
                "event": {
                    "type": "tool_call_failed",
                    "tool": "linear_graphql",
                    "result": {"success": False, "output": "permission denied"},
                    "session_id": "session-1",
                    "attempt": 2,
                }
            },
        )

        CompositeEventSink(store.record_event, ErrorJsonlLog(errors_path)).write(event)

        error = store.list_errors(issue_identifier="SYM-1")["items"][0]
        payload = json.loads(errors_path.read_text().strip())
        self.assertEqual(error["stage"], "codex")
        self.assertEqual(error["error_type"], "tool_call_failed")
        self.assertEqual(error["message"], "permission denied")
        self.assertEqual(payload["error_type"], "tool_call_failed")

    def test_runtime_event_router_keeps_jsonl_complete_and_filters_quiet_codex_events(self):
        from symphonz.service.event_log import ErrorJsonlLog, JsonlEventLog, RuntimeEventRouter
        from symphonz.service.models import RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        runtime_path = self.path.parent / "runtime.jsonl"
        errors_path = self.path.parent / "errors.jsonl"
        router = RuntimeEventRouter(
            JsonlEventLog(runtime_path),
            store,
            ErrorJsonlLog(errors_path),
        )
        events = [
            RuntimeEvent("service_started", "started", "SYM-1"),
            RuntimeEvent(
                "codex_event",
                "text delta",
                "SYM-1",
                data={"event": {"type": "item_agent_message_delta", "text": "hello"}},
            ),
            RuntimeEvent(
                "codex_event",
                "token usage",
                "SYM-1",
                data={"event": {"type": "turn_token_usage", "usage": {"total": 1}}},
            ),
            RuntimeEvent(
                "codex_event",
                "session started",
                "SYM-1",
                data={"event": {"type": "session_started", "session_id": "session-1"}},
            ),
            RuntimeEvent("report_published", "published", "SYM-1"),
            RuntimeEvent("issue_retrying", "retrying", "SYM-1"),
            RuntimeEvent("workspace_cleanup_failed", "cleanup failed", "SYM-1"),
        ]

        for event in events:
            router(event)

        runtime_lines = runtime_path.read_text().splitlines()
        stored_types = [item["type"] for item in store.list_events(issue_identifier="SYM-1")["items"]]
        self.assertEqual(len(runtime_lines), len(events))
        self.assertEqual(stored_types, [
            "workspace_cleanup_failed",
            "issue_retrying",
            "report_published",
            "codex_event",
            "service_started",
        ])

    def test_runtime_event_router_records_nested_codex_failure_once_and_mirrors_redacted_error(self):
        from symphonz.service.event_log import ErrorJsonlLog, JsonlEventLog, RuntimeEventRouter
        from symphonz.service.models import RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        errors_path = self.path.parent / "errors.jsonl"
        router = RuntimeEventRouter(
            JsonlEventLog(self.path.parent / "runtime.jsonl"),
            store,
            ErrorJsonlLog(errors_path),
        )
        event = RuntimeEvent(
            "codex_event",
            "tool_call_failed",
            "SYM-1",
            data={
                "event": {
                    "type": "tool_call_failed",
                    "tool": "linear_graphql",
                    "result": {"success": False, "output": "permission denied"},
                    "session_id": "session-1",
                },
                "api_key": "top-secret",
            },
        )

        router.write(event)

        errors = store.list_errors(issue_identifier="SYM-1")["items"]
        error_lines = errors_path.read_text().splitlines()
        payload = json.loads(error_lines[0])
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(error_lines), 1)
        self.assertEqual(errors[0]["message"], "permission denied")
        self.assertEqual(payload["context"]["api_key"], "[REDACTED]")

    def test_runtime_event_router_isolates_sink_failures(self):
        from symphonz.service.event_log import RuntimeEventRouter
        from symphonz.service.models import RuntimeEvent

        calls = []

        def failing_runtime_log(event):
            calls.append("runtime")
            raise OSError("runtime log unavailable")

        class Store:
            def record_event(self, event):
                calls.append("store")
                raise RuntimeError("sqlite unavailable")

        class ErrorLog:
            def write(self, record):
                calls.append("error")

        RuntimeEventRouter(failing_runtime_log, Store(), ErrorLog()).write(
            RuntimeEvent("tool_failed", "failed", "SYM-1")
        )

        self.assertEqual(calls, ["runtime", "store", "error"])

    def test_keyset_pagination_has_no_duplicates_when_rows_change_between_pages(self):
        from symphonz.service.models import RuntimeErrorRecord, RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        for identifier, updated_at in (("SYM-1", 30.0), ("SYM-2", 20.0), ("SYM-3", 10.0)):
            store.upsert_issue({"issue_identifier": identifier, "status": "running", "updated_at": updated_at})
        first_tasks = store.list_tasks(limit=1)
        store.upsert_issue({"issue_identifier": "SYM-1", "status": "running", "updated_at": 40.0})
        store.upsert_issue({"issue_identifier": "SYM-4", "status": "running", "updated_at": 50.0})
        second_tasks = store.list_tasks(cursor=first_tasks["next_cursor"], limit=1)

        events = [
            RuntimeEvent("event", "first", "SYM-1", timestamp=30.0),
            RuntimeEvent("event", "second", "SYM-1", timestamp=20.0),
            RuntimeEvent("event", "third", "SYM-1", timestamp=10.0),
        ]
        for event in events:
            store.record_event(event)
        first_events = store.list_events(limit=1)
        store.record_event(RuntimeEvent("event", "new", "SYM-1", timestamp=50.0))
        second_events = store.list_events(cursor=first_events["next_cursor"], limit=1)

        for timestamp, message in ((30.0, "first"), (20.0, "second"), (10.0, "third")):
            store.record_error(RuntimeErrorRecord(issue_identifier="SYM-1", message=message, timestamp=timestamp))
        first_errors = store.list_errors(limit=1)
        store.record_error(RuntimeErrorRecord(issue_identifier="SYM-1", message="new", timestamp=50.0))
        second_errors = store.list_errors(cursor=first_errors["next_cursor"], limit=1)

        self.assertEqual(first_tasks["items"][0]["issue_identifier"], "SYM-1")
        self.assertEqual(second_tasks["items"][0]["issue_identifier"], "SYM-2")
        self.assertEqual(first_events["items"][0]["message"], "first")
        self.assertEqual(second_events["items"][0]["message"], "second")
        self.assertEqual(first_errors["items"][0]["message"], "first")
        self.assertEqual(second_errors["items"][0]["message"], "second")

    def test_recording_an_issue_event_updates_last_activity_in_the_same_transaction(self):
        from symphonz.service.models import RuntimeEvent
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.upsert_issue({"issue_identifier": "SYM-1", "status": "running", "updated_at": 10.0})

        store.record_event(RuntimeEvent("tool_completed", "Done", "SYM-1", timestamp=20.0))

        self.assertEqual(store.get_task("SYM-1")["updated_at"], 20.0)

    def test_report_and_session_metadata_redact_header_secrets(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.save_report(
            {
                "issue_identifier": "SYM-1",
                "report_version": 1,
                "linear_sync_status": "pending",
                "review_metadata": {"headers": {"X-Api-Key": "hidden"}},
            }
        )
        store.save_session(
            "token-hash",
            expires_at=time.time() + 60,
            metadata={"headers": {"X-Api-Key": "hidden"}},
        )

        self.assertEqual(store.get_report("SYM-1")["review_metadata"]["headers"]["X-Api-Key"], "[REDACTED]")
        self.assertEqual(store.get_session("token-hash")["metadata"]["headers"]["X-Api-Key"], "[REDACTED]")

    def test_report_sync_status_uses_linear_sync_status_with_compatibility_alias(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        entry = {"issue_identifier": "SYM-1", "report_version": 1}
        store.save_report({**entry, "linear_sync_status": "pending"})
        self.assertEqual(store.get_report("SYM-1")["linear_sync_status"], "pending")
        store.save_report({**entry, "sync_status": "failed"})
        self.assertEqual(store.get_report("SYM-1")["linear_sync_status"], "failed")
        store.save_report({**entry, "linear_sync_status": "synced"})

        report = store.get_report("SYM-1")
        self.assertEqual(report["linear_sync_status"], "synced")
        self.assertNotIn("sync_status", report)

    def test_report_sync_lease_is_atomic_across_store_instances_and_reclaimable_after_expiry(self):
        from symphonz.service.runtime_store import RuntimeStore

        first = RuntimeStore(self.path)
        second = RuntimeStore(self.path)
        first.save_report(
            {
                "issue_identifier": "SYM-1",
                "report_version": 1,
                "linear_sync_status": "pending",
                "next_retry_at": 10.0,
            }
        )

        lease_started_at = time.time()
        claimed = first.claim_report_sync(
            "SYM-1", owner="worker-1", now=lease_started_at, lease_seconds=0.05,
        )
        contended = second.claim_report_sync(
            "SYM-1", owner="worker-2", now=lease_started_at, lease_seconds=0.05,
        )
        time.sleep(0.1)
        reclaimed = second.claim_report_sync(
            "SYM-1", owner="worker-2", now=time.time(), lease_seconds=30.0,
        )

        self.assertIsNotNone(claimed)
        self.assertIsNone(contended)
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed["sync_lease_owner"], "worker-2")

    def test_report_sync_lease_release_requires_owner(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.save_report({"issue_identifier": "SYM-1", "linear_sync_status": "pending"})
        store.claim_report_sync("SYM-1", owner="worker-1", now=10.0, lease_seconds=30.0)

        self.assertFalse(store.release_report_sync("SYM-1", owner="worker-2"))
        self.assertTrue(store.release_report_sync("SYM-1", owner="worker-1"))
        self.assertIsNotNone(store.claim_report_sync("SYM-1", owner="worker-2", now=11.0, lease_seconds=30.0))

    def test_report_sync_lease_renewal_and_state_writes_require_current_owner(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.save_report(
            {
                "issue_identifier": "SYM-1",
                "json_path": "report-current.json",
                "linear_sync_status": "pending",
            }
        )
        store.claim_report_sync("SYM-1", owner="worker-1", now=10.0, lease_seconds=30.0)

        self.assertFalse(store.renew_report_sync("SYM-1", owner="worker-2", now=11.0, lease_seconds=30.0))
        self.assertTrue(store.renew_report_sync("SYM-1", owner="worker-1", now=11.0, lease_seconds=30.0))
        self.assertFalse(
            store.update_report_sync_state(
                "SYM-1",
                expected_json_path="report-current.json",
                owner="worker-2",
                linear_sync_status="synced",
                linear_comment_id="comment-1",
                retry_count=0,
                next_retry_at=None,
                updated_at=12.0,
            )
        )
        self.assertTrue(
            store.update_report_sync_state(
                "SYM-1",
                expected_json_path="report-current.json",
                owner="worker-1",
                linear_sync_status="synced",
                linear_comment_id="comment-1",
                retry_count=0,
                next_retry_at=None,
                updated_at=12.0,
            )
        )

    def test_report_sync_lease_checks_and_fenced_writes_use_current_wall_clock(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.save_report(
            {
                "issue_identifier": "SYM-1",
                "json_path": "report-current.json",
                "linear_sync_status": "pending",
            }
        )
        lease_started_at = time.time()
        store.claim_report_sync("SYM-1", owner="old-owner", now=lease_started_at, lease_seconds=0.05)

        time.sleep(0.1)

        self.assertFalse(
            store.owns_report_sync_lease("SYM-1", owner="old-owner", now=lease_started_at)
        )
        self.assertFalse(
            store.update_report_sync_state(
                "SYM-1",
                expected_json_path="report-current.json",
                owner="old-owner",
                linear_sync_status="synced",
                linear_comment_id="comment-1",
                retry_count=0,
                next_retry_at=None,
                updated_at=lease_started_at,
                lease_checked_at=lease_started_at,
            )
        )

    def test_report_sync_state_update_cannot_replace_a_newer_authoritative_bundle(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        store.save_report(
            {
                "issue_identifier": "SYM-1",
                "json_path": "/artifacts/SYM-1/report-new.json",
                "html_path": "/artifacts/SYM-1/report-new.html",
                "linear_sync_status": "pending",
            }
        )
        store.claim_report_sync("SYM-1", owner="worker-1", now=10.0, lease_seconds=30.0)

        stale = store.update_report_sync_state(
            "SYM-1",
            expected_json_path="/artifacts/SYM-1/report-old.json",
            owner="worker-1",
            linear_sync_status="synced",
            linear_comment_id="comment-old",
            retry_count=0,
            next_retry_at=None,
            updated_at=20.0,
        )
        current = store.get_report("SYM-1")

        self.assertFalse(stale)
        self.assertEqual(current["json_path"], "/artifacts/SYM-1/report-new.json")
        self.assertEqual(current["linear_sync_status"], "pending")

    def test_failed_login_attempts_increment_and_lock_atomically_under_concurrency(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        now = 100.0
        barrier = threading.Barrier(5)
        results = []
        failures = []

        def record_failure():
            try:
                barrier.wait()
                results.append(
                    RuntimeStore(self.path).record_failed_login_attempt(
                        "admin:127.0.0.1", now=now, max_failures=5, window_seconds=300, lock_seconds=900
                    )
                )
            except Exception as error:  # pragma: no cover - asserted below
                failures.append(error)

        workers = [threading.Thread(target=record_failure) for _ in range(5)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        attempt = store.get_login_attempt("admin:127.0.0.1")
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 5)
        self.assertEqual(attempt["failures"], 5)
        self.assertEqual(attempt["locked_until"], now + 900)
        self.assertTrue(any(result["locked"] for result in results))

    def test_login_attempt_reservations_allow_only_five_concurrent_kdf_slots(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        now = 100.0
        barrier = threading.Barrier(10)

        def reserve():
            barrier.wait()
            return RuntimeStore(self.path).reserve_login_attempt(
                "admin:127.0.0.1", now=now, max_attempts=5, window_seconds=300, lock_seconds=900
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _unused: reserve(), range(10)))

        attempt = store.get_login_attempt("admin:127.0.0.1")
        self.assertEqual(sum(result["reserved"] for result in results), 5)
        self.assertTrue(all("reservation_id" in result for result in results if result["reserved"]))
        self.assertEqual(len({result["reservation_id"] for result in results if result["reserved"]}), 5)
        self.assertEqual(attempt["failures"], 5)
        self.assertEqual(attempt["locked_until"], now + 900)

    def test_failed_client_reservation_releases_global_slot(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        first = store.reserve_login_attempt("account:client:1", now=100.0, max_attempts=1)
        rejected = store.reserve_login_attempt("account:client:1", now=100.0, max_attempts=1)
        replacement = store.reserve_login_attempt("account:client:2", now=100.0, max_attempts=1)

        self.assertTrue(first["reserved"])
        self.assertFalse(rejected["reserved"])
        self.assertTrue(replacement["reserved"])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM login_attempt_reservations").fetchone()[0],
                2,
            )

    def test_login_attempt_completion_is_conditional_on_unique_reservation(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        reservations = [store.reserve_login_attempt("account:client:1", now=100.0) for _ in range(5)]

        self.assertTrue(all("reservation_id" in reservation for reservation in reservations))
        self.assertTrue(hasattr(store, "complete_login_attempt"))
        self.assertTrue(store.complete_login_attempt(reservations[0]["reservation_id"], succeeded=True, now=101.0))
        self.assertFalse(store.complete_login_attempt(reservations[0]["reservation_id"], succeeded=True, now=101.0))
        replacement = store.reserve_login_attempt("account:client:1", now=101.0)
        blocked = store.reserve_login_attempt("account:client:1", now=101.0)

        self.assertTrue(replacement["reserved"])
        self.assertFalse(blocked["reserved"])
        self.assertEqual(store.get_login_attempt("account:client:1")["failures"], 5)

    def test_expired_login_reservations_and_buckets_are_cleaned(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        expired = store.reserve_login_attempt("expired-bucket", now=100.0)
        self.assertIn("reservation_id", expired)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE login_attempts SET window_started_at = 0, locked_until = 1, updated_at = 0 "
                "WHERE rate_limit_key = ?",
                ("expired-bucket",),
            )
            connection.execute(
                "UPDATE login_attempt_reservations SET expires_at = 1 WHERE reservation_id = ?",
                (expired["reservation_id"],),
            )

        current = store.reserve_login_attempt("current-bucket", now=2_000.0)

        self.assertTrue(current["reserved"])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM login_attempt_reservations WHERE reservation_id = ?",
                    (expired["reservation_id"],),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM login_attempts WHERE rate_limit_key = 'expired-bucket'"
                ).fetchone()[0],
                0,
            )

    def test_composite_event_sink_continues_after_a_sink_fails(self):
        from symphonz.service.event_log import CompositeEventSink, JsonlEventLog
        from symphonz.service.models import RuntimeEvent

        class FailingSink:
            def write(self, event):
                raise RuntimeError("sink unavailable")

        path = self.path.parent / "runtime.jsonl"

        CompositeEventSink(FailingSink(), JsonlEventLog(path)).write(RuntimeEvent("started", "Started"))

        self.assertEqual(json.loads(path.read_text().strip())["message"], "Started")

    def test_invalid_pagination_inputs_raise_clear_errors(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        with self.assertRaisesRegex(ValueError, "Invalid pagination cursor"):
            store.list_tasks(cursor=-1)
        with self.assertRaisesRegex(ValueError, "Invalid pagination cursor"):
            store.list_events(cursor="not-a-cursor")
        with self.assertRaisesRegex(ValueError, "Invalid pagination limit"):
            store.list_errors(limit=0)

    def test_legacy_not_null_sync_status_reports_table_accepts_new_writes(self):
        from symphonz.service.runtime_store import RuntimeStore

        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
                CREATE TABLE reports (
                    issue_identifier TEXT NOT NULL,
                    report_version INTEGER NOT NULL,
                    json_path TEXT,
                    html_path TEXT,
                    url TEXT,
                    review_metadata_json TEXT NOT NULL DEFAULT '{}',
                    linear_comment_id TEXT,
                    sync_status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (issue_identifier, report_version)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO reports (issue_identifier, report_version, sync_status, created_at, updated_at)
                VALUES ('SYM-OLD', 1, 'failed', 1, 1)
                """
            )

        store = RuntimeStore(self.path)
        store.save_report({"issue_identifier": "SYM-NEW", "linear_sync_status": "synced"})

        self.assertEqual(store.get_report("SYM-OLD")["linear_sync_status"], "failed")
        self.assertEqual(store.get_report("SYM-NEW")["linear_sync_status"], "synced")

    def test_task_cursor_keeps_an_unread_task_after_its_activity_changes(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        for identifier, updated_at in (("SYM-1", 30.0), ("SYM-2", 20.0), ("SYM-3", 10.0)):
            store.upsert_issue({"issue_identifier": identifier, "status": "running", "updated_at": updated_at})

        first_page = store.list_tasks(limit=1)
        store.upsert_issue({"issue_identifier": "SYM-2", "status": "running", "updated_at": 40.0})
        second_page = store.list_tasks(cursor=first_page["next_cursor"], limit=1)
        third_page = store.list_tasks(cursor=second_page["next_cursor"], limit=1)

        self.assertEqual(first_page["items"][0]["issue_identifier"], "SYM-1")
        self.assertEqual(second_page["items"][0]["issue_identifier"], "SYM-2")
        self.assertEqual(third_page["items"][0]["issue_identifier"], "SYM-3")

    def test_malformed_base64_cursor_raises_runtime_store_input_error(self):
        from symphonz.service.runtime_store import RuntimeStore, RuntimeStoreInputError

        with self.assertRaisesRegex(RuntimeStoreInputError, "Invalid pagination cursor"):
            RuntimeStore(self.path).list_tasks(cursor="a")

    def test_expired_task_cursor_raises_and_using_it_cleans_snapshot(self):
        from symphonz.service.runtime_store import RuntimeStore, RuntimeStoreInputError

        store = RuntimeStore(self.path)
        snapshot_ttl_seconds = 300.0
        for identifier in ("SYM-1", "SYM-2"):
            store.upsert_issue({"issue_identifier": identifier, "status": "running"})

        first_page = store.list_tasks(limit=1)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE task_page_snapshots SET created_at = ?",
                (time.time() - snapshot_ttl_seconds - 1,),
            )

        with self.assertRaisesRegex(RuntimeStoreInputError, "Expired pagination cursor"):
            store.list_tasks(cursor=first_page["next_cursor"], limit=1)

        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_page_snapshots").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM task_page_snapshot_entries").fetchone()[0], 0
            )

    def test_creating_task_snapshot_performs_bounded_expiry_cleanup(self):
        from symphonz.service.runtime_store import RuntimeStore

        store = RuntimeStore(self.path)
        snapshot_ttl_seconds = 300.0
        cleanup_batch_size = 100
        for identifier in ("SYM-1", "SYM-2"):
            store.upsert_issue({"issue_identifier": identifier, "status": "running"})

        now = time.time()
        expired_count = cleanup_batch_size + 2
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                "INSERT INTO task_page_snapshots (created_at) VALUES (?)",
                [(now - snapshot_ttl_seconds - 1,)] * expired_count,
            )
            connection.execute("INSERT INTO task_page_snapshots (created_at) VALUES (?)", (now,))

        store.list_tasks(limit=1)

        with sqlite3.connect(self.path) as connection:
            remaining_expired = connection.execute(
                "SELECT COUNT(*) FROM task_page_snapshots WHERE created_at < ?",
                (now - snapshot_ttl_seconds,),
            ).fetchone()[0]
            self.assertEqual(remaining_expired, 2)


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
            workflow.config["hooks"] = {"after_create": "printf ready > ready.txt\n"}
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
        state.claimed.add("id-1")
        state.completed["SYM-1"] = {"issue_identifier": "SYM-1", "status": "completed"}
        payload = state.snapshot()
        html = render_dashboard_html()

        self.assertEqual(payload["counts"]["running"], 0)
        self.assertEqual(payload["counts"]["claimed"], 1)
        self.assertEqual(payload["events"][0]["message"], "runtime started")
        self.assertEqual(find_issue(payload, "SYM-1")["status"], "completed")
        self.assertIn("Symphonz Runtime", html)
        self.assertIn("Issue Queue", html)
        self.assertIn("Activity Feed", html)
        self.assertIn("class=\"app-shell\"", html)
        self.assertIn("class=\"sidebar\"", html)
        self.assertIn("status-chip", html)
        self.assertIn("Claimed", html)
        self.assertIn("Turn / Attempt", html)
        self.assertIn("due_at", html)
        self.assertIn("cancellation_reason", html)
