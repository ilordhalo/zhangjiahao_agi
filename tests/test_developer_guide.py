from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

from symphonz import __version__


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ResourceCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []
        self.stylesheets = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): value or "" for name, value in attrs}
        for name in ["src", "href", "srcset"]:
            if name in attributes:
                self.resources.append(attributes[name].strip())
        if tag.lower() == "link" and "stylesheet" in attributes.get("rel", "").lower().split():
            self.stylesheets.append(attributes.get("href", ""))
        if tag.lower() == "script":
            self.scripts.append(attributes.get("src", ""))


class DeveloperGuideTests(unittest.TestCase):
    def setUp(self):
        self.guide_path = PROJECT_ROOT / "docs" / "index.html"

    def read_guide(self) -> str:
        self.assertTrue(self.guide_path.exists(), "docs/index.html must exist")
        return self.guide_path.read_text()

    def test_guide_is_the_complete_chinese_040_developer_guide(self):
        html = self.read_guide()

        self.assertEqual(__version__, "0.4.0")
        self.assertIn("<title>Symphonz 0.4.0 开发者指南</title>", html)
        self.assertIn("lang=\"zh-CN\"", html)
        self.assertIn(f"v{__version__}", html)
        self.assertNotIn("0.3.1", html)

        for section_id in [
            "overview",
            "system-map",
            "linear-polling",
            "codex-trigger",
            "workflow-anatomy",
            "issue-lifecycle",
            "persistence-reporting",
            "dashboard-auth",
            "human-review",
            "real-case-simulation",
            "project-layout",
            "implementation-boundaries",
        ]:
            self.assertIn(f'id="{section_id}"', html)

        for phrase in [
            "Linear GraphQL",
            "Symphonz Runtime",
            "Issue Workspace",
            "Codex app-server",
            "RuntimeStore",
            "ReportPublisher",
            "PendingReportSynchronizer",
            "initialize",
            "initialized",
            "thread/start",
            "turn/start",
            "linear_graphql",
            "symphonz_report",
            "runtime.sqlite3",
            "runtime.jsonl",
            "errors.jsonl",
            "Human Review",
            "Dashboard",
            "HttpOnly",
            "SameSite=Lax",
            "人工审核门",
            "终态清理",
        ]:
            self.assertIn(phrase, html)

    def test_diagrams_encode_polling_protocol_reporting_auth_and_cleanup(self):
        html = self.read_guide()

        self.assertIn('class="system-map"', html)
        self.assertIn('class="sequence-diagram"', html)
        self.assertIn('class="lifecycle-track"', html)
        for component in ["linear", "runtime", "workflow", "workspace", "codex", "store", "report", "dashboard", "provider"]:
            self.assertIn(f'data-component="{component}"', html)
        for edge in [
            "runtime-linear",
            "runtime-workflow",
            "runtime-workspace",
            "runtime-codex",
            "runtime-store",
            "codex-linear",
            "codex-report",
            "report-store",
            "report-linear",
            "dashboard-store",
            "dashboard-auth",
            "codex-provider",
        ]:
            self.assertIn(f'data-edge="{edge}"', html)
        for step in [
            "poll",
            "eligibility",
            "workspace",
            "initialize",
            "initialized",
            "thread",
            "turn",
            "linear-tool",
            "report-tool",
            "persist",
            "report-sync",
            "human-review",
            "terminal-cleanup",
        ]:
            self.assertIn(f'data-sequence-step="{step}"', html)

        self.assertIn('id="component-detail"', html)
        self.assertIn('id="play-sequence"', html)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', html)
        self.assertIn("Cancelled / Canceled / Duplicate", html)
        self.assertIn('data-transition="rework-return"', html)

    def test_workflow_explanation_matches_runtime_and_agent_ownership(self):
        html = self.read_guide()

        workflow_parts = re.findall(r'data-workflow-part="([^"]+)"', html)
        self.assertEqual(
            workflow_parts,
            [
                "tracker",
                "workspace",
                "codex",
                "issue-context",
                "operating-rules",
                "workpad",
                "status-map",
                "review-convention",
                "guardrails",
            ],
        )
        for phrase in [
            "为什么这样写",
            "配置层",
            "Prompt 层",
            "Linear 是外部控制面",
            "一个 Issue 只维护一个 Workpad",
            "Ready to Publish 是发布命令，Done 是终态",
            "报告必须在 review request 存在之后发布",
            "报告同步 pending 时不得进入 Human Review",
            "Runtime-owned",
            "Agent-owned",
        ]:
            self.assertIn(phrase, html)
        self.assertGreaterEqual(html.count('data-owner="runtime"'), 4)
        self.assertGreaterEqual(html.count('data-owner="agent"'), 4)

    def test_pay_214_simulation_has_each_turn_and_both_linear_comments(self):
        html = self.read_guide()

        turns = re.findall(
            r'<details[^>]+data-case-turn="([^"]+)"[^>]*>(.*?)</details>',
            html,
            re.DOTALL,
        )
        self.assertEqual([name for name, _ in turns], ["todo", "ready-to-publish", "rework", "merging"])
        for _, body in turns:
            self.assertIn('class="prompt-block"', body)
            self.assertIn('class="linear-sync"', body)
            self.assertIn('class="review-sync"', body)
            self.assertIn("Sync state", body)

        todo_body, publish_body, rework_body, merging_body = [body for _, body in turns]
        self.assertIn("## Symphonz Workpad", todo_body)
        self.assertIn("npm test -- payment-submit payment-3ds", todo_body)
        self.assertIn("symphonz_report", publish_body)
        self.assertIn("## Symphonz Implementation Report", publish_body)
        self.assertIn("https://symphonz.example/issues/PAY-214/report", publish_body)
        self.assertIn("https://github.example/acme/payments/pull/482", publish_body)
        self.assertIn("linear_sync_status: synced", publish_body)
        self.assertIn("Apply the actionable review feedback discovered after launch", rework_body)
        self.assertIn("linear_sync_status: synced", rework_body)
        self.assertIn("workspace_cleanup_status: removed", merging_body)
        self.assertIn("报告 artifact 保留", merging_body)

        for phrase in [
            "PAY-214",
            "symphonz/PAY-214-prevent-duplicate-payment",
            "a13c9f2",
            "c71b640",
            "85b27de",
            "PR #482",
            "Todo → In Progress → Ready to Publish → Human Review → Rework → Human Review → Merging → Done",
            "离线真实结构模拟，不访问 Linear 或 GitHub，不产生外部写操作",
            "Workpad 由 Agent 维护",
            "Implementation Report 评论由 Runtime 维护",
            "Human Review 不触发 Codex",
        ]:
            self.assertIn(phrase, html)
        self.assertEqual(html.count('data-simulation-actor="human"'), 3)

    def test_guide_is_accessible_self_contained_and_free_of_credentials(self):
        html = self.read_guide()
        parser = ResourceCollector()
        parser.feed(html)

        self.assertEqual(parser.stylesheets, [])
        self.assertTrue(all(not source for source in parser.scripts), "scripts must remain inline and offline")
        for resource in parser.resources:
            self.assertNotRegex(resource, r"^(?:https?:)?//")
        self.assertIsNone(re.search(r"@import\s+(?:url\()?\s*[\"']?(?:https?:)?//", html, re.IGNORECASE))
        self.assertIsNone(re.search(r"url\(\s*[\"']?(?:https?:)?//", html, re.IGNORECASE))
        for unsafe_api in ["eval(", "new Function", ".innerHTML", "document.write"]:
            self.assertNotIn(unsafe_api, html)
        for credential_pattern in [r"sk-[A-Za-z0-9]{12,}", r"ghp_[A-Za-z0-9]+", r"glpat-[A-Za-z0-9_-]+"]:
            self.assertIsNone(re.search(credential_pattern, html))
        self.assertNotIn("zhangjiahao.me", html)
        self.assertIn(":focus-visible", html)
        self.assertIn("@media (max-width: 520px)", html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", html)


if __name__ == "__main__":
    unittest.main()
