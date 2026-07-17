from __future__ import annotations

from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode
import json
import os
import re
import signal
import socket
import stat
import subprocess
import tempfile
import time
import unittest

from symphonz.service.runtime_store import RuntimeStore


DASHBOARD_USERNAME = "admin"
DASHBOARD_PASSWORD = "local-e2e-dashboard-password"
REPORT_HEADING = "## Symphonz Implementation Report"
WORKPAD_HEADING = "## Symphonz Workpad"


class StatefulLinearFixture:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.set_state("Todo")
        self.set_report_sync_success(False)

    @property
    def url(self) -> str:
        return self.root.as_uri()

    @property
    def state_name(self) -> str:
        return json.loads((self.root / "state.json").read_text())["issue"]["state"]["name"]

    @property
    def requests(self) -> list[dict]:
        path = self.root / "requests.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    @property
    def report_comments(self) -> dict[str, str]:
        return self._comments_with_heading(REPORT_HEADING)

    @property
    def workpad_comments(self) -> dict[str, str]:
        return self._comments_with_heading(WORKPAD_HEADING)

    @property
    def comments(self) -> list[dict]:
        state_path = self.root / "state.json"
        if not state_path.exists():
            return []
        comments = json.loads(state_path.read_text()).get("comments", [])
        return comments if isinstance(comments, list) else []

    def _comments_with_heading(self, heading: str) -> dict[str, str]:
        return {
            comment["issueId"]: comment["body"]
            for comment in self.comments
            if isinstance(comment, dict)
            and isinstance(comment.get("issueId"), str)
            and isinstance(comment.get("body"), str)
            and comment["body"].startswith(heading)
        }

    def set_state(self, state: str) -> None:
        state_path = self.root / "state.json"
        payload = json.loads(state_path.read_text()) if state_path.exists() else {}
        payload["issue"] = self.issue(state)
        payload.setdefault("comments", [])
        temporary = self.root / ".actor-state.tmp"
        temporary.write_text(json.dumps(payload))
        temporary.replace(state_path)

    def set_report_sync_success(self, success: bool) -> None:
        state_path = self.root / "state.json"
        payload = json.loads(state_path.read_text())
        payload["reportSyncSuccess"] = success
        temporary_state = self.root / ".actor-report-state.tmp"
        temporary_state.write_text(json.dumps(payload))
        temporary_state.replace(state_path)
        responses = {
            "default": {"errors": [{"message": "unexpected fixture operation"}]},
        }
        temporary = self.root / ".responses.tmp"
        temporary.write_text(json.dumps(responses))
        temporary.replace(self.root / "responses.json")

    @staticmethod
    def issue(state: str) -> dict:
        return {
            "id": "issue-1",
            "identifier": "QA-1",
            "title": "Sandbox quality run",
            "description": "Exercise the complete built-in lifecycle.",
            "priority": 1,
            "state": {"name": state},
            "branchName": "symphonz/QA-1-sandbox-quality-run",
            "url": "https://linear.local/QA-1",
            "labels": {"nodes": [{"name": "codex-ready"}]},
            "inverseRelations": {"nodes": []},
            "createdAt": "2026-07-09T00:00:00Z",
            "updatedAt": "2026-07-09T01:00:00Z",
        }


class InstalledCliE2ETests(unittest.TestCase):
    def test_installed_service_persists_report_restart_auth_and_terminal_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            project = root / "project"
            project.mkdir()
            fixture = StatefulLinearFixture(root / "fake-linear")
            port = self.free_port()
            public_base_url = f"http://127.0.0.1:{port}"
            self.install_cli_and_project(prefix, project, port, public_base_url)

            auth_path = project / ".symphonz" / "auth.toml"
            self.assertTrue(auth_path.is_file())
            self.assertEqual(stat.S_IMODE(auth_path.stat().st_mode), 0o600)
            config = (project / ".symphonz" / "config.toml").read_text()
            self.assertIn(f'port = "{port}"', config)
            self.assertIn(f'public_base_url = "{public_base_url}"', config)
            self.assertNotIn(DASHBOARD_PASSWORD, config)

            audit_path = root / "codex-audit.jsonl"
            protocol_path = root / "codex-protocol.jsonl"
            provider_path = root / "provider-records.jsonl"
            leak_path = root / "codex-child-leaked.txt"
            report_retry_path = root / "allow-report-retry"
            fake_codex = root / "fake_codex.py"
            fake_codex.write_text(
                fake_codex_source(
                    audit_path,
                    protocol_path,
                    provider_path,
                    leak_path,
                    report_retry_path,
                    public_base_url,
                )
            )
            self.configure_workflow(project, fake_codex)
            env = os.environ.copy()
            env.update({"LINEAR_API_KEY": "fake-linear-key", "SYMPHONZ_LINEAR_ENDPOINT": fixture.url})

            workspace = project / ".symphonz" / "workspace" / "QA-1"
            runtime_db = project / ".symphonz" / "logs" / "runtime.sqlite3"
            artifacts = project / ".symphonz" / "artifacts" / "QA-1"

            first_service = self.start_service(prefix, project, env, port)
            try:
                try:
                    self.wait_for(lambda: fixture.state_name == "In Progress", "Todo mutation")
                except AssertionError as error:
                    diagnostics = RuntimeStore(runtime_db).list_errors(issue_identifier="QA-1", limit=20)["items"]
                    protocol_diagnostics = protocol_path.read_text() if protocol_path.exists() else "<no protocol>"
                    self.fail(f"{error}; runtime errors={diagnostics!r}; protocol={protocol_diagnostics!r}")
                todo_run = self.wait_for_audit_state(audit_path, "Todo", occurrence=1)
                self.assert_process_gone(todo_run["process_id"])
                self.assert_process_gone(todo_run["child_process_id"])
                self.assertTrue(workspace.exists())
                self.assertEqual((workspace / "workpad-id.txt").read_text(), "workpad-QA-1")
                self.assertEqual((workspace / "branch.txt").read_text(), "symphonz/QA-1-sandbox-quality-run")

                fixture.set_state("Ready to Publish")
                self.wait_for(
                    lambda: RuntimeStore(runtime_db).get_report("QA-1")["linear_sync_status"] == "pending",
                    "pending report sync",
                )
                self.assertEqual(fixture.state_name, "Ready to Publish")
                report = RuntimeStore(runtime_db).get_report("QA-1")
                report_path = artifacts / report["html_path"]
                self.assertTrue(report_path.is_file())
                self.assertIn("Initial QA implementation report", report_path.read_text())

                unauthenticated = self.http_request(port, "GET", "/issues/QA-1/report")
                self.assertEqual(unauthenticated[0], 303)
                self.assertEqual(
                    unauthenticated[1]["Location"],
                    "/login?next=%2Fissues%2FQA-1%2Freport",
                )
                login = self.http_request(
                    port,
                    "POST",
                    "/login",
                    body=urlencode(
                        {
                            "username": DASHBOARD_USERNAME,
                            "password": DASHBOARD_PASSWORD,
                            "next": "/issues/QA-1/report",
                        }
                    ),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                self.assertEqual(login[0], 303)
                session_cookie = login[1]["Set-Cookie"].split(";", 1)[0]
                self.assertIn("symphonz_session=", session_cookie)
                authenticated_report = self.http_request(
                    port,
                    "GET",
                    "/issues/QA-1/report",
                    headers={"Cookie": session_cookie},
                )
                self.assertEqual(authenticated_report[0], 200)
                self.assertIn("Initial QA implementation report", authenticated_report[2])

                pending_errors = RuntimeStore(runtime_db).list_errors(
                    issue_identifier="QA-1",
                    stage="report_sync",
                    resolved=False,
                )["items"]
                self.assertEqual(len(pending_errors), 1)
                self.assertIn("commentCreate", pending_errors[0]["message"])
                self.assertIn(
                    "report_sync",
                    (project / ".symphonz" / "logs" / "errors.jsonl").read_text(),
                )
                pending_task_api = self.authenticated_json(port, "/api/issues/QA-1", session_cookie)
                pending_events_api = self.authenticated_json(
                    port, "/api/issues/QA-1/events?limit=200", session_cookie
                )
                pending_errors_api = self.authenticated_json(
                    port, "/api/issues/QA-1/errors?limit=200", session_cookie
                )
                self.assertEqual(pending_task_api["report"]["linear_sync_status"], "pending")
                self.assertTrue(pending_events_api["items"])
                self.assertEqual(len(pending_errors_api["items"]), 1)
            finally:
                self.stop_service(first_service)

            persisted = RuntimeStore(runtime_db)
            published = persisted.get_report("QA-1")
            self.assertEqual(published["linear_sync_status"], "pending")
            self.assertEqual(persisted.list_tasks(query="QA-1")["items"][0]["report_url"], f"{public_base_url}/issues/QA-1/report")
            unresolved_errors = persisted.list_errors(
                issue_identifier="QA-1",
                stage="report_sync",
                resolved=False,
            )["items"]
            self.assertEqual(len(unresolved_errors), 1)
            interrupted_publish = self.wait_for_audit_state(audit_path, "Ready to Publish", occurrence=1)
            self.assert_process_gone(interrupted_publish["process_id"])
            self.assert_process_gone(interrupted_publish["child_process_id"])

            second_service = self.start_service(prefix, project, env, port)
            try:
                report_page = self.http_request(
                    port,
                    "GET",
                    "/issues/QA-1/report",
                    headers={"Cookie": session_cookie},
                )
                self.assertEqual(report_page[0], 200)
                self.assertIn("Initial QA implementation report", report_page[2])
                self.assertIn("https://github.local/pull/42", report_page[2])
                task_page = self.http_request(port, "GET", "/tasks", headers={"Cookie": session_cookie})
                self.assertEqual(task_page[0], 200)
                self.assertIn("QA-1", task_page[2])

                restarted_task_api = self.authenticated_json(port, "/api/issues/QA-1", session_cookie)
                restarted_events_api = self.authenticated_json(
                    port, "/api/issues/QA-1/events?limit=200", session_cookie
                )
                restarted_errors_api = self.authenticated_json(
                    port, "/api/issues/QA-1/errors?limit=200", session_cookie
                )
                self.assertEqual(restarted_task_api["task"]["issue_identifier"], "QA-1")
                self.assertEqual(restarted_task_api["report"]["url"], pending_task_api["report"]["url"])
                self.assertTrue(
                    {item["id"] for item in pending_events_api["items"]}.issubset(
                        {item["id"] for item in restarted_events_api["items"]}
                    )
                )
                self.assertTrue(
                    {item["id"] for item in pending_errors_api["items"]}.issubset(
                        {item["id"] for item in restarted_errors_api["items"]}
                    )
                )

                self.wait_for_audit_state(audit_path, "Ready to Publish", occurrence=2)
                fixture.set_report_sync_success(True)
                report_retry_path.write_text("retry")
                self.wait_for(lambda: fixture.state_name == "Human Review", "publication mutation after restart")
                self.wait_for(
                    lambda: RuntimeStore(runtime_db).get_report("QA-1")["linear_sync_status"] == "synced",
                    "report sync recovery after restart",
                )
                recovered_publish = self.wait_for_audit_state(audit_path, "Ready to Publish", occurrence=2)
                self.assert_process_gone(recovered_publish["process_id"])
                self.assert_process_gone(recovered_publish["child_process_id"])

                updates_before_rework = self.report_update_count(fixture)
                fixture.set_state("Rework")
                self.wait_for(
                    lambda: fixture.state_name == "Human Review"
                    and self.report_update_count(fixture) == updates_before_rework + 1,
                    "rework report update",
                )
                self.wait_for(
                    lambda: "Rework validation incorporated" in fixture.report_comments.get("issue-1", ""),
                    "runtime-owned report comment update",
                )
                rework_run = self.wait_for_audit_state(audit_path, "Rework", occurrence=1)
                self.assert_process_gone(rework_run["process_id"])
                self.assert_process_gone(rework_run["child_process_id"])

                fixture.set_state("Merging")
                self.wait_for(lambda: fixture.state_name == "Done", "merge mutation")
                merging_run = self.wait_for_audit_state(audit_path, "Merging", occurrence=1)
                self.assert_process_gone(merging_run["process_id"])
                self.assert_process_gone(merging_run["child_process_id"])
                self.wait_for(lambda: not workspace.exists(), "terminal workspace cleanup")
                self.wait_for(
                    lambda: "workspace_removed"
                    in {
                        event["type"]
                        for event in RuntimeStore(runtime_db).list_events(
                            issue_identifier="QA-1", limit=200
                        )["items"]
                    },
                    "persisted terminal cleanup",
                )
                surviving_report_page = self.http_request(
                    port,
                    "GET",
                    "/issues/QA-1/report",
                    headers={"Cookie": session_cookie},
                )
                self.assertEqual(surviving_report_page[0], 200)
                self.assertIn("Rework validation incorporated", surviving_report_page[2])
            finally:
                self.stop_service(second_service)

            final_store = RuntimeStore(runtime_db)
            final_report = final_store.get_report("QA-1")
            final_report_path = artifacts / final_report["html_path"]
            final_report_json = artifacts / final_report["json_path"]
            self.assertTrue(final_report_path.is_file())
            self.assertTrue(final_report_json.is_file())
            self.assertIn("Rework validation incorporated", final_report_path.read_text())
            self.assertFalse(workspace.exists())

            tasks = final_store.list_tasks(query="QA-1")["items"]
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["linear_state"], "Done")
            self.assertEqual(tasks[0]["workspace_cleanup_status"], "removed")
            self.assertEqual(tasks[0]["review_url"], "https://github.local/pull/42")
            self.assertEqual(tasks[0]["report_url"], f"{public_base_url}/issues/QA-1/report")
            event_types = {event["type"] for event in final_store.list_events(issue_identifier="QA-1", limit=200)["items"]}
            self.assertTrue({"issue_started", "codex_event", "workspace_removed"}.issubset(event_types))
            resolved_errors = final_store.list_errors(issue_identifier="QA-1", stage="report_sync", resolved=True)["items"]
            self.assertEqual(len(resolved_errors), 1)
            self.assertEqual(resolved_errors[0]["resolving_event"], "report_sync_succeeded")

            report_comment_objects = [
                comment for comment in fixture.comments if str(comment.get("body") or "").startswith(REPORT_HEADING)
            ]
            workpad_comment_objects = [
                comment for comment in fixture.comments if str(comment.get("body") or "").startswith(WORKPAD_HEADING)
            ]
            self.assertEqual(len(report_comment_objects), 1)
            self.assertEqual(report_comment_objects[0]["id"], "report-comment-QA-1")
            self.assertEqual(len(workpad_comment_objects), 1)
            self.assertEqual(workpad_comment_objects[0]["id"], "workpad-QA-1")
            self.assertEqual(fixture.report_comments["issue-1"].count(REPORT_HEADING), 1)
            self.assertIn(f"Report: {public_base_url}/issues/QA-1/report", fixture.report_comments["issue-1"])
            self.assertIn("Review: ", fixture.report_comments["issue-1"])
            self.assertIn("github.local/pull/42", fixture.report_comments["issue-1"])
            self.assertEqual(fixture.workpad_comments["issue-1"].count(WORKPAD_HEADING), 1)
            self.assertIn("State: Done", fixture.workpad_comments["issue-1"])

            audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
            self.assertEqual(
                [entry["state"] for entry in audit],
                ["Todo", "Ready to Publish", "Ready to Publish", "Rework", "Merging"],
            )
            self.assertEqual({entry["branch"] for entry in audit}, {"symphonz/QA-1-sandbox-quality-run"})
            self.assertEqual({entry["review"] for entry in audit}, {"https://github.local/pull/42"})
            self.assertEqual(len({entry["process_id"] for entry in audit}), 5)
            for entry in audit:
                self.assert_process_gone(entry["process_id"])
                self.assert_process_gone(entry["child_process_id"])
            self.assertFalse(leak_path.exists(), "Codex descendant survived process-group cleanup")

            protocol = [json.loads(line) for line in protocol_path.read_text().splitlines()]
            self.assertEqual(
                [entry["method"] for entry in protocol],
                ["initialize", "initialized", "thread/start", "turn/start"] * 5,
            )
            self.assertTrue(all(entry["jsonrpc"] == "2.0" for entry in protocol))
            self.assertTrue(all(entry["contract_valid"] is True for entry in protocol))

            provider = [json.loads(line) for line in provider_path.read_text().splitlines()]
            self.assertEqual([record["action"] for record in provider], ["pull_request", "pull_request", "merge"])
            mutations = [request for request in fixture.requests if request["operation"] == "SymphonzSetState"]
            self.assertEqual(
                [mutation["variables"]["stateName"] for mutation in mutations],
                ["In Progress", "Human Review", "Human Review", "Done"],
            )
            self.assertTrue(all(request["authorization"] == "fake-linear-key" for request in fixture.requests))
            runtime_log = project / ".symphonz" / "logs" / "runtime.jsonl"
            self.assertGreaterEqual(runtime_log.read_text().count('"type": "service_started"'), 2)

    def install_cli_and_project(self, prefix: Path, project: Path, port: int, public_base_url: str) -> None:
        install = subprocess.run(
            ["sh", "install.sh", "--prefix", str(prefix), "--source", str(Path.cwd())],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(install.returncode, 0, install.stderr)
        subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/example/quality-project.git"],
            cwd=project,
            check=True,
        )
        install_env = os.environ.copy()
        install_env.update(
            {
                "SYMPHONZ_DASHBOARD_PASSWORD": DASHBOARD_PASSWORD,
                "SYMPHONZ_DASHBOARD_PORT": str(port),
                "SYMPHONZ_DASHBOARD_PUBLIC_BASE_URL": public_base_url,
            }
        )
        project_install = subprocess.run(
            [
                str(prefix / "bin" / "symphonz"),
                "install",
                "--yes",
                "--skip-linear-preflight",
                "--linear-project",
                "quality-project",
                "--git-provider",
                "github",
            ],
            cwd=project,
            env=install_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(project_install.returncode, 0, project_install.stderr)

    def configure_workflow(self, project: Path, fake_codex: Path) -> None:
        workflow = project / ".symphonz" / "WORKFLOW.md"
        content = workflow.read_text()
        content, replacements = re.subn(
            r"hooks:\n.*?agent:",
            "hooks:\n  after_create: |\n    git init -b main\n    printf '%s' \"$SYMPHONZ_ISSUE_IDENTIFIER\" > dispatched_issue.txt\nagent:",
            content,
            flags=re.DOTALL,
        )
        self.assertEqual(replacements, 1)
        content = content.replace("  max_turns: 20", "  max_turns: 1")
        content = content.replace("  interval_ms: 5000", "  interval_ms: 100")
        content = re.sub(r"  command: .* app-server", f"  command: python3 {fake_codex}", content, count=1)
        workflow.write_text(content)

    def start_service(self, prefix: Path, project: Path, env: dict[str, str], port: int) -> subprocess.Popen:
        service = subprocess.Popen(
            [str(prefix / "bin" / "symphonz"), "run"],
            cwd=project,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.wait_for(lambda: self.http_request(port, "GET", "/healthz")[0] == 200, "dashboard health")
        except BaseException:
            self.stop_service(service)
            raise
        return service

    def stop_service(self, service: subprocess.Popen) -> None:
        if service.poll() is None:
            service.send_signal(signal.SIGINT)
        stdout, stderr = service.communicate(timeout=12)
        self.assertEqual(service.returncode, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")

    def http_request(
        self,
        port: int,
        method: str,
        path: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def report_update_count(self, fixture: StatefulLinearFixture) -> int:
        return sum(request["operation"] == "SymphonzUpdateReportComment" for request in fixture.requests)

    def authenticated_json(self, port: int, path: str, session_cookie: str) -> dict:
        status, _, body = self.http_request(port, "GET", path, headers={"Cookie": session_cookie})
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertIsInstance(payload, dict)
        return payload

    def wait_for_audit_state(self, audit_path: Path, state: str, *, occurrence: int) -> dict:
        def matching_entries() -> list[dict]:
            if not audit_path.exists():
                return []
            return [
                entry
                for line in audit_path.read_text().splitlines()
                if line
                for entry in [json.loads(line)]
                if entry.get("state") == state
            ]

        self.wait_for(lambda: len(matching_entries()) >= occurrence, f"{state} audit entry {occurrence}")
        return matching_entries()[occurrence - 1]

    def wait_for(self, predicate, label: str, timeout: float = 16) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate():
                    return
            except (ConnectionError, FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
                pass
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {label}")

    def assert_process_gone(self, process_id: int) -> None:
        if os.name != "posix":
            return
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                self.fail(f"Process {process_id} still exists but cannot be inspected")
            time.sleep(0.05)
        self.fail(f"Process {process_id} is still alive")

    @staticmethod
    def free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])


def fake_codex_source(
    audit_path: Path,
    protocol_path: Path,
    provider_path: Path,
    leak_path: Path,
    report_retry_path: Path,
    public_base_url: str,
) -> str:
    prefix = (
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import re\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"audit_path = pathlib.Path({str(audit_path)!r})\n"
        f"protocol_path = pathlib.Path({str(protocol_path)!r})\n"
        f"provider_path = pathlib.Path({str(provider_path)!r})\n"
        f"leak_path = pathlib.Path({str(leak_path)!r})\n"
        f"report_retry_path = pathlib.Path({str(report_retry_path)!r})\n"
        f"public_base_url = {public_base_url!r}\n"
    )
    return prefix + r'''
child_code = "import pathlib,sys,time; time.sleep(60); pathlib.Path(sys.argv[1]).write_text('leaked')"
child = subprocess.Popen([sys.executable, "-c", child_code, str(leak_path)])
pending = None
actions = []
state = None
next_tool_id = 900
thread_id = "thread-QA-1"
turn_id = None


def require_contract(condition, message):
    if not condition:
        with protocol_path.open("a") as protocol:
            protocol.write(json.dumps({"contract_valid": False, "error": message}) + "\n")
        raise SystemExit("protocol contract violation: " + message)


def same_path(left, right):
    return isinstance(left, str) and isinstance(right, str) and os.path.realpath(left) == os.path.realpath(right)


def validate_protocol_message(msg):
    method = msg.get("method")
    require_contract(msg.get("jsonrpc") == "2.0", f"{method} missing jsonrpc 2.0")
    params = msg.get("params")
    require_contract(isinstance(params, dict), f"{method} params must be an object")
    if method == "initialize":
        require_contract(params.get("clientInfo") == {"name": "symphonz", "version": "0.4.0"}, "invalid clientInfo")
        require_contract(params.get("capabilities") == {"experimentalApi": True}, "invalid capabilities")
    elif method == "initialized":
        require_contract("id" not in msg and params == {}, "initialized must be an empty notification")
    elif method == "thread/start":
        require_contract(params.get("approvalPolicy") == "never", "invalid thread approval policy")
        require_contract(params.get("sandbox") == "workspace-write", "invalid thread sandbox")
        require_contract(same_path(params.get("cwd"), os.getcwd()), "thread cwd does not match workspace")
        tool_by_name = {tool.get("name"): tool for tool in params.get("dynamicTools", [])}
        require_contract(set(tool_by_name) == {"linear_graphql", "symphonz_report"}, "unexpected dynamic tools")
        linear_schema = tool_by_name["linear_graphql"].get("inputSchema", {})
        report_schema = tool_by_name["symphonz_report"].get("inputSchema", {})
        require_contract(linear_schema.get("required") == ["query"], "invalid Linear tool schema")
        require_contract(linear_schema.get("additionalProperties") is False, "Linear schema must be closed")
        require_contract("review" in report_schema.get("required", []), "report tool must require review metadata")
        require_contract(report_schema.get("additionalProperties") is False, "report schema must be closed")
    elif method == "turn/start":
        require_contract(params.get("threadId") == thread_id, "turn is not linked to the created thread")
        require_contract(same_path(params.get("cwd"), os.getcwd()), "turn cwd does not match workspace")
        require_contract(params.get("approvalPolicy") == "never", "invalid turn approval policy")
        require_contract(params.get("title") == "QA-1: Sandbox quality run", "invalid turn title")
        sandbox_policy = params.get("sandboxPolicy")
        require_contract(isinstance(sandbox_policy, dict), "turn sandbox policy must be an object")
        writable_roots = sandbox_policy.get("writableRoots")
        require_contract(sandbox_policy.get("type") == "workspaceWrite", "invalid turn sandbox type")
        if writable_roots is None:
            require_contract(sandbox_policy.get("networkAccess") is True, "invalid configured turn sandbox policy")
        else:
            require_contract(
                isinstance(writable_roots, list)
                and len(writable_roots) == 1
                and same_path(writable_roots[0], os.getcwd()),
                "invalid turn writable roots",
            )
        inputs = params.get("input")
        require_contract(
            isinstance(inputs, list)
            and len(inputs) == 1
            and inputs[0].get("type") == "text"
            and isinstance(inputs[0].get("text"), str),
            "turn input must contain exactly one text prompt",
        )
    else:
        require_contract(False, f"unexpected runtime method {method}")
    with protocol_path.open("a") as protocol:
        protocol.write(json.dumps({"method": method, "jsonrpc": msg["jsonrpc"], "contract_valid": True}) + "\n")


def report_payload(summary):
    return {
        "operation": "publish",
        "issue_id": "issue-1",
        "issue_identifier": "QA-1",
        "title": "Sandbox quality run",
        "summary": summary,
        "goal": "Verify the installed Symphonz lifecycle without external services.",
        "scope": "Fake Linear, fake Codex, report publication, restart, auth, and cleanup.",
        "architecture": {
            "nodes": [
                {"id": "codex", "label": "Fake Codex", "description": "Issues dynamic tool calls."},
                {"id": "runtime", "label": "Symphonz Runtime", "description": "Persists and publishes."},
            ],
            "edges": [{"from": "codex", "to": "runtime", "label": "symphonz_report"}],
        },
        "implementation": ["Drive the complete deterministic lifecycle through local fakes."],
        "decisions": [
            {
                "decision": "Use local deterministic fixtures.",
                "rationale": "The E2E must not use real credentials or network services.",
                "alternatives": ["Live Linear and GitHub"],
                "tradeoffs": ["Provider behavior is simulated."],
            }
        ],
        "changed_files": ["tests/test_symphonz_e2e.py"],
        "validation": [
            {"command": "python3 -m unittest tests.test_symphonz_e2e", "result": "passed", "evidence": "Deterministic local flow."}
        ],
        "risks": ["Socket creation can be restricted by a test sandbox."],
        "follow_ups": ["Controller performs browser visual QA."],
        "review": {
            "provider": "github",
            "url": "https://github.local/pull/42",
            "branch": "symphonz/QA-1-sandbox-quality-run",
            "commit": "a13c9f2",
            "target": "main",
        },
    }


def graphql_action(operation, body, *, create=False):
    mutation = "commentCreate(input: $input)" if create else "commentUpdate(id: $id, input: $input)"
    declaration = "$input: CommentCreateInput!" if create else "$id: String!, $input: CommentUpdateInput!"
    variables = {"input": {"issueId": "issue-1", "body": body}} if create else {"id": "workpad-QA-1", "input": {"body": body}}
    return (
        "linear_graphql",
        {"query": f"mutation {operation}({declaration}) {{ {mutation} {{ success comment {{ id }} }} }}", "variables": variables},
    )


def state_action(target):
    query = "mutation SymphonzSetState($issueId: String!, $stateName: String!) { issueUpdate(id: $issueId, input: {stateName: $stateName}) { success } }"
    return "linear_graphql", {"query": query, "variables": {"issueId": "issue-1", "stateName": target}}


def send_next():
    global pending, next_tool_id
    if actions:
        tool, arguments = actions.pop(0)
        pending = {"id": next_tool_id, "tool": tool}
        next_tool_id += 1
        print(json.dumps({"jsonrpc": "2.0", "id": pending["id"], "method": "item/tool/call", "params": {"tool": tool, "arguments": arguments}}), flush=True)
        return
    pending = None
    print(json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": thread_id, "turnId": turn_id, "status": "completed", "usage": {"totalTokens": 17}}}), flush=True)


for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method:
        validate_protocol_message(msg)
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "fake-codex", "version": "1.0"}}}), flush=True)
    elif method == "thread/start":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"thread": {"id": thread_id}}}), flush=True)
    elif method == "turn/start":
        turn_id = "turn-" + str(os.getpid())
        prompt = msg["params"]["input"][0]["text"]
        state = re.search(r"Current status: (.+)", prompt).group(1).strip()
        target = {"Todo": "In Progress", "Ready to Publish": "Human Review", "Rework": "Human Review", "Merging": "Done"}[state]
        branch = "symphonz/QA-1-sandbox-quality-run"
        review = "https://github.local/pull/42"
        pathlib.Path("workpad-id.txt").write_text("workpad-QA-1")
        pathlib.Path("branch.txt").write_text(branch)
        pathlib.Path("review.txt").write_text(review)
        if state in {"Ready to Publish", "Merging"}:
            with provider_path.open("a") as provider:
                provider.write(json.dumps({"action": "pull_request" if state == "Ready to Publish" else "merge", "branch": branch, "review": review}) + "\n")
        with audit_path.open("a") as audit:
            audit.write(json.dumps({"state": state, "target": target, "workpad": "workpad-QA-1", "branch": branch, "review": review, "process_id": os.getpid(), "child_process_id": child.pid}) + "\n")
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"turn": {"id": turn_id, "threadId": thread_id}}}), flush=True)

        workpad = "\n".join([
            "## Symphonz Workpad",
            "",
            f"State: {target}",
            f"Branch: {branch}",
            f"Review: {review if state != 'Todo' else 'pending'}",
            f"Report: {public_base_url}/issues/QA-1/report" if state != "Todo" else "Report: pending",
        ])
        actions = []
        if state == "Todo":
            actions.append(graphql_action("SymphonzCreateWorkpadComment", workpad, create=True))
        else:
            actions.append(graphql_action("SymphonzUpdateWorkpadComment", workpad))
        if state == "Ready to Publish":
            actions.insert(0, ("symphonz_report", report_payload("Initial QA implementation report")))
        elif state == "Rework":
            actions.insert(0, ("symphonz_report", report_payload("Rework validation incorporated")))
        actions.append(state_action(target))
        send_next()
    elif pending is not None and msg.get("id") == pending["id"]:
        require_contract(msg.get("jsonrpc") == "2.0", "dynamic tool response missing jsonrpc 2.0")
        require_contract(isinstance(msg.get("result"), dict), "dynamic tool response result must be an object")
        require_contract(isinstance(msg["result"].get("contentItems"), list), "dynamic tool response missing contentItems")
        if not msg.get("result", {}).get("success"):
            raise SystemExit(f"{pending['tool']} failed: {msg}")
        tool_output = json.loads(msg["result"].get("output", "{}"))
        if pending["tool"] == "symphonz_report" and tool_output.get("linear_sync_status") == "pending":
            deadline = time.time() + 12
            while not report_retry_path.exists() and time.time() < deadline:
                time.sleep(0.02)
            if not report_retry_path.exists():
                raise SystemExit("report retry was not released")
            actions.insert(0, ("symphonz_report", report_payload("Initial QA implementation report")))
        send_next()
'''


if __name__ == "__main__":
    unittest.main()
