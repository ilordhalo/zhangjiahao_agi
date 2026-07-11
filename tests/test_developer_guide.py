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

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): value or "" for name, value in attrs}
        for name in ["src", "href", "srcset"]:
            if name in attributes:
                self.resources.append(attributes[name].strip())
        if tag.lower() == "link" and "stylesheet" in attributes.get("rel", "").lower().split():
            self.stylesheets.append(attributes.get("href", ""))


class DeveloperGuideTests(unittest.TestCase):
    def setUp(self):
        self.guide_path = PROJECT_ROOT / "docs" / "index.html"

    def read_guide(self) -> str:
        self.assertTrue(self.guide_path.exists(), "docs/index.html must exist")
        return self.guide_path.read_text()

    def test_guide_contains_complete_chinese_workflow_sections(self):
        html = self.read_guide()

        self.assertIn("<title>Symphonz 服务机制</title>", html)
        for section_id in [
            "overview",
            "system-map",
            "linear-polling",
            "codex-trigger",
            "issue-lifecycle",
            "project-layout",
            "implementation-boundaries",
        ]:
            self.assertIn(f'id="{section_id}"', html)

        for phrase in [
            "Linear GraphQL",
            "Symphonz Runtime",
            "Issue Workspace",
            "Codex app-server",
            "initialize",
            "thread/start",
            "turn/start",
            "Human Review",
            "GitHub / GitLab",
            "有界并发执行",
            "进程内存",
            "八个当前边界",
            "Human Review 默认不在 active_states",
            "指数退避重试",
            "runtime.jsonl",
        ]:
            self.assertIn(phrase, html)

        self.assertIn(f"v{__version__}", html)

    def test_guide_has_stable_diagram_and_interaction_hooks(self):
        html = self.read_guide()

        self.assertIn('class="system-map"', html)
        self.assertIn('class="sequence-diagram"', html)
        self.assertIn('class="lifecycle-track"', html)
        self.assertIn('data-component="linear"', html)
        self.assertIn('data-component="codex"', html)
        self.assertIn('data-sequence-step="initialize"', html)
        self.assertIn('data-sequence-step="initialized"', html)
        self.assertIn('data-actor="linear"', html)
        self.assertIn('data-transition="rework-return"', html)
        self.assertIn("Cancelled / Canceled / Duplicate", html)
        for edge in [
            "runtime-linear",
            "runtime-workflow",
            "runtime-workspace",
            "runtime-codex",
            "runtime-state",
            "codex-linear",
            "codex-provider",
        ]:
            self.assertIn(f'data-edge="{edge}"', html)
        self.assertIn('id="component-detail"', html)
        self.assertIn('id="play-sequence"', html)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', html)

    def test_guide_explains_current_workflow_design(self):
        html = self.read_guide()

        self.assertIn('id="workflow-anatomy"', html)
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
        ]:
            self.assertIn(phrase, html)

    def test_guide_contains_four_turn_pay_214_simulation(self):
        html = self.read_guide()

        self.assertIn('id="real-case-simulation"', html)
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

        todo_body = turns[0][1]
        done_body = turns[1][1]
        rework_body = turns[2][1]
        self.assertIn("- [x] 3DS return path covered", todo_body)
        self.assertIn("npm test -- payment-submit payment-3ds", todo_body)
        self.assertIn("- [x] 3DS return path covered", done_body)
        self.assertIn("Rapid retry after 3DS return uses shared guard", rework_body)
        self.assertEqual(html.count('data-simulation-actor="human"'), 3)
        self.assertIn("最简四轮路径", html)
        self.assertIn("在下一次 poll 前改为 Ready to Publish", html)
        self.assertIn("实际运行可能出现额外的 In Progress turn", html)

        prompt_blocks = re.findall(r'<pre class="prompt-block">(.*?)</pre>', html, re.DOTALL)
        self.assertEqual(len(prompt_blocks), 4)
        self.assertNotIn("Review context discovered", prompt_blocks[2])
        self.assertNotIn("PR #482", prompt_blocks[2])
        self.assertNotIn("Route the 3DS retry action", prompt_blocks[2])
        self.assertNotIn("Add the missing 3DS rapid-retry", prompt_blocks[2])
        self.assertIn("Apply the actionable review feedback discovered after launch", prompt_blocks[2])
        self.assertNotIn("PR #482", prompt_blocks[3])
        self.assertIn("Codex 启动后查询得到", rework_body)

        self.assertGreaterEqual(html.count("symphonz/PAY-214-prevent-duplicate-payment"), 5)
        self.assertGreaterEqual(html.count("PR #482"), 5)
        self.assertIn("runner-01:/workspaces/PAY-214@91bd204", todo_body)
        self.assertNotIn("runner-01:/workspaces/PAY-214@a13c9f2", todo_body)

        for phrase in [
            "PAY-214",
            "symphonz/PAY-214-prevent-duplicate-payment",
            "## Symphonz Workpad",
            "Todo → In Progress → Ready to Publish → Human Review → Rework → Human Review → Merging → Done",
            "离线真实结构模拟，不访问 Linear 或 GitHub，不产生外部写操作",
            "复用该 thread 运行最多",
            "Human Review 不触发 Codex",
        ]:
            self.assertIn(phrase, html)

    def test_guide_is_self_contained_and_offline(self):
        html = self.read_guide()

        parser = ResourceCollector()
        parser.feed(html)

        self.assertEqual(parser.stylesheets, [])
        for resource in parser.resources:
            self.assertNotRegex(resource, r"^(?:https?:)?//")
        self.assertIsNone(re.search(r"@import\s+(?:url\()?\s*[\"']?(?:https?:)?//", html, re.IGNORECASE))
        self.assertIsNone(re.search(r"url\(\s*[\"']?(?:https?:)?//", html, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
