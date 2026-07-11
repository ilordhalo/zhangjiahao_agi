from __future__ import annotations

from pathlib import Path
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import unittest


class StatefulLinearFixture:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.set_state("Todo")

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

    def set_state(self, state: str) -> None:
        payload = {"issue": self.issue(state)}
        temporary = self.root / ".actor-state.tmp"
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.root / "state.json")

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
    def test_one_service_process_drives_stateful_linear_and_terminal_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            project = root / "project"
            project.mkdir()
            fixture = StatefulLinearFixture(root / "fake-linear")
            self.install_cli_and_project(prefix, project)

            audit_path = root / "codex-audit.jsonl"
            provider_path = root / "provider-records.jsonl"
            fake_codex = root / "fake_codex.py"
            fake_codex.write_text(fake_codex_source(audit_path, provider_path))
            self.configure_workflow(project, fake_codex)
            env = os.environ.copy()
            env.update({"LINEAR_API_KEY": "fake-linear-key", "SYMPHONZ_LINEAR_ENDPOINT": fixture.url})

            service = subprocess.Popen(
                [
                    str(prefix / "bin" / "symphonz"),
                    "service",
                    ".symphonz/WORKFLOW.md",
                    "--logs-root",
                    ".symphonz/logs",
                ],
                cwd=project,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                self.wait_for(lambda: fixture.state_name == "In Progress", "Todo mutation")
                fixture.set_state("Ready to Publish")
                self.wait_for(
                    lambda: fixture.state_name == "Human Review" and self.mutation_count(fixture) >= 2,
                    "publication mutation",
                )
                fixture.set_state("Rework")
                self.wait_for(
                    lambda: fixture.state_name == "Human Review" and self.mutation_count(fixture) >= 3,
                    "rework mutation",
                )

                workspace = project / ".symphonz" / "workspace" / "QA-1"
                self.assertTrue(workspace.exists())
                self.assertEqual((workspace / "workpad-id.txt").read_text(), "workpad-QA-1")
                self.assertEqual((workspace / "branch.txt").read_text(), "symphonz/QA-1-sandbox-quality-run")
                self.assertEqual((workspace / "review.txt").read_text(), "https://github.local/pull/42")

                fixture.set_state("Merging")
                self.wait_for(lambda: fixture.state_name == "Done", "merge mutation")
                self.wait_for(lambda: not workspace.exists(), "terminal workspace cleanup")
            finally:
                if service.poll() is None:
                    service.send_signal(signal.SIGINT)
                stdout, stderr = service.communicate(timeout=8)
            self.assertEqual(service.returncode, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")

            audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
            self.assertEqual([entry["state"] for entry in audit], ["Todo", "Ready to Publish", "Rework", "Merging"])
            self.assertEqual({entry["workpad"] for entry in audit}, {"workpad-QA-1"})
            self.assertEqual({entry["branch"] for entry in audit}, {"symphonz/QA-1-sandbox-quality-run"})
            self.assertEqual({entry["review"] for entry in audit}, {"https://github.local/pull/42"})
            self.assertEqual(len({entry["process_id"] for entry in audit}), 4)

            provider = [json.loads(line) for line in provider_path.read_text().splitlines()]
            self.assertEqual([record["action"] for record in provider], ["pull_request", "merge"])
            self.assertEqual({record["branch"] for record in provider}, {"symphonz/QA-1-sandbox-quality-run"})

            mutations = [request for request in fixture.requests if request["operation"] == "SymphonzSetState"]
            self.assertEqual(
                [mutation["variables"]["stateName"] for mutation in mutations],
                ["In Progress", "Human Review", "Human Review", "Done"],
            )
            self.assertTrue(all(request["authorization"] == "fake-linear-key" for request in fixture.requests))
            log = project / ".symphonz" / "logs" / "runtime.jsonl"
            self.assertIn("workspace_removed", log.read_text())
            self.assertIn("issue_continuing", log.read_text())

    def install_cli_and_project(self, prefix: Path, project: Path) -> None:
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

    def mutation_count(self, fixture: StatefulLinearFixture) -> int:
        return sum(request["operation"] == "SymphonzSetState" for request in fixture.requests)

    def wait_for(self, predicate, label: str, timeout: float = 12) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate():
                    return
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {label}")


def fake_codex_source(audit_path: Path, provider_path: Path) -> str:
    return f'''import json
import os
import pathlib
import re
import sys

audit_path = pathlib.Path({str(audit_path)!r})
provider_path = pathlib.Path({str(provider_path)!r})
pending = None
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({{"id": msg["id"], "result": {{}}}}), flush=True)
    elif method == "thread/start":
        tools = msg.get("params", {{}}).get("dynamicTools", [])
        if not any(tool.get("name") == "linear_graphql" for tool in tools):
            raise SystemExit("linear_graphql was not advertised")
        print(json.dumps({{"id": msg["id"], "result": {{"thread": {{"id": "thread-QA-1"}}}}}}), flush=True)
    elif method == "turn/start":
        prompt = msg["params"]["input"][0]["text"]
        state = re.search(r"Current status: (.+)", prompt).group(1).strip()
        target = {{"Todo": "In Progress", "Ready to Publish": "Human Review", "Rework": "Human Review", "Merging": "Done"}}[state]
        branch = "symphonz/QA-1-sandbox-quality-run"
        review = "https://github.local/pull/42"
        pathlib.Path("workpad-id.txt").write_text("workpad-QA-1")
        pathlib.Path("branch.txt").write_text(branch)
        pathlib.Path("review.txt").write_text(review)
        if state in {{"Ready to Publish", "Merging"}}:
            with provider_path.open("a") as provider:
                provider.write(json.dumps({{"action": "pull_request" if state == "Ready to Publish" else "merge", "branch": branch, "review": review}}) + "\\n")
        with audit_path.open("a") as audit:
            audit.write(json.dumps({{"state": state, "target": target, "workpad": "workpad-QA-1", "branch": branch, "review": review, "process_id": os.getpid()}}) + "\\n")
        print(json.dumps({{"id": msg["id"], "result": {{"turn": {{"id": "turn-" + state.replace(" ", "-")}}}}}}), flush=True)
        pending = 900
        query = "mutation SymphonzSetState($issueId: String!, $stateName: String!) {{ issueUpdate(id: $issueId, input: {{stateName: $stateName}}) {{ success }} }}"
        print(json.dumps({{"jsonrpc": "2.0", "id": pending, "method": "item/tool/call", "params": {{"tool": "linear_graphql", "arguments": {{"query": query, "variables": {{"issueId": "issue-1", "stateName": target}}}}}}}}), flush=True)
    elif pending is not None and msg.get("id") == pending:
        if not msg.get("result", {{}}).get("success"):
            raise SystemExit("linear_graphql failed")
        pending = None
        print(json.dumps({{"method": "turn/completed", "params": {{"usage": {{"totalTokens": 7}}}}}}), flush=True)
'''


if __name__ == "__main__":
    unittest.main()
