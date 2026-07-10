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
            "同步逐个执行",
            "进程内存",
            "八个当前边界",
            "Human Review 默认不在 active_states",
            "不具备自动重试",
            "不会写入日志文件",
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
