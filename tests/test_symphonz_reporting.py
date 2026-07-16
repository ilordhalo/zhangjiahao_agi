import json
from pathlib import Path
import tempfile
import unittest


def valid_report(**overrides):
    report = {
        "operation": "publish",
        "issue_id": "issue-123",
        "issue_identifier": "SYM-123",
        "title": "Publish structured reports",
        "summary": "Reports are available for review.",
        "goal": "Make implementation results reviewable.",
        "scope": "Report publication and Linear synchronization.",
        "architecture": {
            "nodes": [
                {"id": "agent", "label": "Codex agent", "description": "Supplies structured data."},
                {"id": "runtime", "label": "Runtime", "description": "Validates and publishes."},
            ],
            "edges": [{"from": "agent", "to": "runtime", "label": "report"}],
        },
        "implementation": ["Validate the report before publishing it."],
        "decisions": [
            {
                "decision": "Use HTML/CSS for architecture diagrams.",
                "rationale": "It avoids executable diagram source.",
                "alternatives": ["Mermaid"],
                "tradeoffs": ["The layout is deliberately simple."],
            }
        ],
        "changed_files": ["symphonz/service/reporting.py"],
        "validation": [
            {"command": "python3 -m unittest", "result": "passed", "evidence": "All focused tests passed."}
        ],
        "risks": ["Linear may be temporarily unavailable."],
        "follow_ups": ["Show reports in the dashboard."],
        "review": {
            "provider": "gitlab",
            "url": "https://git.example.test/group/project/-/merge_requests/7",
            "branch": "codex/report-dashboard-auth",
            "commit": "0123456789abcdef",
            "target": "main",
        },
    }
    report.update(overrides)
    return report


class FakeLinearClient:
    def __init__(self):
        self.calls = []
        self.comments = []
        self.fail = False

    def graphql(self, query, variables):
        self.calls.append((query, variables))
        if self.fail:
            raise RuntimeError("Linear is temporarily unavailable")
        if "SymphonzFindReportComment" in query:
            return {"data": {"issue": {"comments": {"nodes": list(self.comments)}}}}
        if "SymphonzCreateReportComment" in query:
            comment = {"id": "comment-1", "body": variables["input"]["body"]}
            self.comments = [comment]
            return {"data": {"commentCreate": {"success": True, "comment": {"id": "comment-1"}}}}
        if "SymphonzUpdateReportComment" in query:
            self.comments[0]["body"] = variables["input"]["body"]
            return {"data": {"commentUpdate": {"success": True, "comment": {"id": "comment-1"}}}}
        raise AssertionError(query)


class ReportingTests(unittest.TestCase):
    def setUp(self):
        from symphonz.service.runtime_store import RuntimeStore

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = RuntimeStore(self.root / "runtime.sqlite3")
        self.linear = FakeLinearClient()

    def publisher(self):
        from symphonz.service.reporting import ReportPublisher

        return ReportPublisher(
            store=self.store,
            artifact_root=self.root / "artifacts",
            public_base_url="https://reports.example.test/base/",
            linear_client=self.linear,
            active_issue_id="issue-123",
            active_issue_identifier="SYM-123",
        )

    def test_report_renderer_escapes_agent_text_and_contains_required_sections(self):
        from symphonz.service.reporting import render_report, validate_report

        document = validate_report(valid_report(summary="<script>alert(1)</script>"))
        html = render_report(document)

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Architecture", html)
        self.assertIn("Validation", html)
        self.assertIn("position: sticky", html)
        self.assertIn("@media print", html)

    def test_validation_rejects_unknown_fields_unsafe_urls_and_excessive_collections(self):
        from symphonz.service.reporting import ReportValidationError, validate_report

        with self.assertRaisesRegex(ReportValidationError, "unknown"):
            validate_report(valid_report(unexpected="no"))
        with self.assertRaisesRegex(ReportValidationError, "http or https"):
            validate_report(valid_report(review={**valid_report()["review"], "url": "javascript:alert(1)"}))
        with self.assertRaisesRegex(ReportValidationError, "at most"):
            validate_report(valid_report(implementation=["step"] * 51))

    def test_publish_uses_stable_url_atomic_replacement_and_active_issue_identity(self):
        publisher = self.publisher()

        first = publisher.publish(valid_report(summary="First publication."))
        second = publisher.publish(valid_report(summary="Second publication."))

        self.assertTrue(first["success"])
        self.assertEqual(first["report_url"], "https://reports.example.test/base/issues/SYM-123/report")
        self.assertEqual(second["report_url"], first["report_url"])
        report_dir = self.root / "artifacts" / "SYM-123"
        self.assertEqual(json.loads((report_dir / "report.json").read_text())["summary"], "Second publication.")
        self.assertIn("Second publication.", (report_dir / "report.html").read_text())
        self.assertFalse(list(report_dir.glob("*.tmp")))
        with self.assertRaisesRegex(ValueError, "active issue"):
            publisher.publish(valid_report(issue_identifier="SYM-999"))

    def test_linear_comment_is_created_once_and_updated_on_republish(self):
        publisher = self.publisher()

        publisher.publish(valid_report(summary="Initial report."))
        publisher.publish(valid_report(summary="Updated report."))

        creates = [query for query, _ in self.linear.calls if "SymphonzCreateReportComment" in query]
        updates = [query for query, _ in self.linear.calls if "SymphonzUpdateReportComment" in query]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(updates), 1)
        self.assertIn("Updated report.", self.linear.comments[0]["body"])
        report = self.store.get_report("SYM-123")
        self.assertEqual(report["linear_comment_id"], "comment-1")
        self.assertEqual(report["linear_sync_status"], "synced")

    def test_failed_linear_sync_stays_pending_and_sync_pending_retries(self):
        from symphonz.service.reporting import sync_pending

        publisher = self.publisher()
        self.linear.fail = True
        result = publisher.publish(valid_report())

        self.assertTrue(result["success"])
        self.assertEqual(result["linear_sync_status"], "pending")
        pending = self.store.get_report("SYM-123")
        self.assertEqual(pending["linear_sync_status"], "pending")
        self.assertGreater(pending["next_retry_at"], 0)
        self.linear.fail = False
        self.assertEqual(sync_pending(self.linear, now=pending["next_retry_at"]), 1)
        self.assertEqual(self.store.get_report("SYM-123")["linear_sync_status"], "synced")

    def test_tool_spec_is_strict_publish_only(self):
        from symphonz.service.reporting import report_tool_spec

        spec = report_tool_spec()

        self.assertEqual(spec["name"], "symphonz_report")
        self.assertFalse(spec["inputSchema"]["additionalProperties"])
        self.assertIn("operation", spec["inputSchema"]["required"])
