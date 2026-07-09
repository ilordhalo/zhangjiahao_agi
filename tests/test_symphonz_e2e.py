from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import unittest


class FakeLinearFixture:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "responses.json").write_text(json.dumps(self.responses()))

    @property
    def url(self) -> str:
        return self.root.as_uri()

    @property
    def requests(self) -> list[dict]:
        requests_path = self.root / "requests.jsonl"
        if not requests_path.exists():
            return []
        return [json.loads(line) for line in requests_path.read_text().splitlines() if line]

    def responses(self) -> dict:
        open_issue = {
            "id": "issue-1",
            "identifier": "QA-1",
            "title": "Sandbox quality run",
            "description": "Exercise the built-in runtime.",
            "priority": 1,
            "state": {"name": "Todo"},
            "branchName": None,
            "url": "https://linear.local/QA-1",
            "labels": {"nodes": [{"name": "codex-ready"}]},
            "createdAt": "2026-07-09T00:00:00Z",
            "updatedAt": "2026-07-09T01:00:00Z",
        }
        closed_issue = dict(open_issue)
        closed_issue["state"] = {"name": "Closed"}
        return {
            "SymphonzPoll": {"data": {"issues": {"nodes": [open_issue]}}},
            "SymphonzIssuesById": {"data": {"issues": {"nodes": [closed_issue]}}},
        }


class InstalledCliE2ETests(unittest.TestCase):
    def test_installed_cli_runs_builtin_runtime_against_fake_linear_and_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            project = root / "project"
            project.mkdir()
            fake_linear = FakeLinearFixture(root / "fake-linear")
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
            answers = "\n".join(["LINEAR_API_KEY", "quality-project", "", "", "", ""]) + "\n"
            project_install = subprocess.run(
                [str(prefix / "bin" / "symphonz"), "install"],
                cwd=project,
                input=answers,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(project_install.returncode, 0, project_install.stderr)
            workflow = project / ".symphonz" / "WORKFLOW.md"
            text = workflow.read_text()
            text, hook_replacements = re.subn(
                r"  after_create: \|\n(?:    .*\n)+agent:",
                '  after_create: |\n    printf "%s" "$SYMPHONZ_ISSUE_IDENTIFIER" > dispatched_issue.txt\nagent:',
                text,
            )
            self.assertEqual(hook_replacements, 1)
            self.assertIn(
                "codex --config shell_environment_policy.inherit=all",
                text,
            )
            text = text.replace(
                "codex --config shell_environment_policy.inherit=all --config 'model=\"gpt-5.5\"' --config model_reasoning_effort=xhigh app-server",
                f"python3 {root / 'fake_codex.py'}",
            )
            workflow.write_text(text)
            (root / "fake_codex.py").write_text(fake_codex_source())
            env = os.environ.copy()
            env.update(
                {
                    "LINEAR_API_KEY": "fake-linear-key",
                    "SYMPHONZ_LINEAR_ENDPOINT": fake_linear.url,
                }
            )

            run = subprocess.run(
                [
                    str(prefix / "bin" / "symphonz"),
                    "service",
                    ".symphonz/WORKFLOW.md",
                    "--logs-root",
                    ".symphonz/logs",
                    "--once",
                ],
                cwd=project,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(run.returncode, 0, run.stderr)
            workspace = project / ".symphonz" / "workspace" / "QA-1"
            self.assertEqual((workspace / "dispatched_issue.txt").read_text(), "QA-1")
            self.assertIn("Identifier: QA-1", (workspace / "codex_prompt.txt").read_text())
            self.assertFalse((project / ".symphonz" / "runtime").exists())
            self.assertEqual(fake_linear.requests[0]["authorization"], "fake-linear-key")
            self.assertEqual(fake_linear.requests[0]["operation"], "SymphonzPoll")
            self.assertEqual(fake_linear.requests[0]["variables"]["projectSlug"], "quality-project")


def fake_codex_source() -> str:
    return (
        "import json, pathlib, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    method = msg.get('method')\n"
        "    if method == 'initialize':\n"
        "        print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)\n"
        "    elif method == 'initialized':\n"
        "        pass\n"
        "    elif method == 'thread/start':\n"
        "        print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'thread-1'}}}), flush=True)\n"
        "    elif method == 'turn/start':\n"
        "        prompt = msg['params']['input'][0]['text']\n"
        "        pathlib.Path('codex_prompt.txt').write_text(prompt)\n"
        "        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)\n"
        "        print(json.dumps({'method': 'turn/completed', 'params': {'usage': {'totalTokens': 7}}}), flush=True)\n"
    )
