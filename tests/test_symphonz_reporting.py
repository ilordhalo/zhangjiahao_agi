import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
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
        self.create_success = True
        self.update_success = True
        self.omit_mutation_id = False
        self.comment_pages = None
        self._lock = threading.Lock()

    def graphql(self, query, variables):
        with self._lock:
            self.calls.append((query, variables))
            if self.fail:
                raise RuntimeError("Linear is temporarily unavailable")
            if "SymphonzFindReportComment" in query:
                if self.comment_pages is not None:
                    page = self.comment_pages[variables.get("after")]
                    return {"data": {"issue": {"comments": page}}}
                return {
                    "data": {
                        "issue": {
                            "comments": {
                                "nodes": list(self.comments),
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            if "SymphonzCreateReportComment" in query:
                comment = {"id": "comment-1", "body": variables["input"]["body"]}
                self.comments = [comment]
                mutation_comment = {} if self.omit_mutation_id else {"id": "comment-1"}
                return {
                    "data": {
                        "commentCreate": {"success": self.create_success, "comment": mutation_comment}
                    }
                }
            if "SymphonzUpdateReportComment" in query:
                if self.comments:
                    self.comments[0]["body"] = variables["input"]["body"]
                mutation_comment = {} if self.omit_mutation_id else {"id": "comment-1"}
                return {
                    "data": {
                        "commentUpdate": {"success": self.update_success, "comment": mutation_comment}
                    }
                }
            raise AssertionError(query)


class ReportingTests(unittest.TestCase):
    def setUp(self):
        from symphonz.service.runtime_store import RuntimeStore

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = RuntimeStore(self.root / "runtime.sqlite3")
        self.linear = FakeLinearClient()

    def publisher(self, **overrides):
        from symphonz.service.reporting import ReportPublisher

        arguments = {
            "store": self.store,
            "artifact_root": self.root / "artifacts",
            "public_base_url": "https://reports.example.test/base/",
            "linear_client": self.linear,
            "active_issue_id": "issue-123",
            "active_issue_identifier": "SYM-123",
        }
        arguments.update(overrides)
        return ReportPublisher(
            **arguments
        )

    def artifact_path(self, entry, key):
        return self.root / "artifacts" / entry["issue_identifier"] / entry[key]

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
        first_entry = self.store.get_report("SYM-123")
        second = publisher.publish(valid_report(summary="Second publication."))
        second_entry = self.store.get_report("SYM-123")

        self.assertTrue(first["success"])
        self.assertEqual(first["report_url"], "https://reports.example.test/base/issues/SYM-123/report")
        self.assertEqual(second["report_url"], first["report_url"])
        report_dir = self.root / "artifacts" / "SYM-123"
        self.assertNotEqual(first_entry["json_path"], second_entry["json_path"])
        self.assertNotEqual(first_entry["html_path"], second_entry["html_path"])
        self.assertRegex(Path(second_entry["json_path"]).name, r"^report-[a-z0-9]+\.json$")
        self.assertRegex(Path(second_entry["html_path"]).name, r"^report-[a-z0-9]+\.html$")
        self.assertFalse(Path(second_entry["json_path"]).is_absolute())
        self.assertFalse(Path(second_entry["html_path"]).is_absolute())
        self.assertEqual(json.loads(publisher.read_current_json("SYM-123"))["summary"], "Second publication.")
        self.assertIn("Second publication.", publisher.read_current_html("SYM-123"))
        self.assertFalse(self.artifact_path(first_entry, "json_path").exists())
        self.assertFalse(self.artifact_path(first_entry, "html_path").exists())
        self.assertFalse(list(report_dir.glob("*.tmp")))
        with self.assertRaisesRegex(ValueError, "active issue"):
            publisher.publish(valid_report(issue_identifier="SYM-999"))

    def test_second_bundle_write_failure_keeps_previous_database_paths_authoritative(self):
        publisher = self.publisher()
        publisher.publish(valid_report(summary="Authoritative report."))
        previous = self.store.get_report("SYM-123")
        original_write = publisher._write_bundle_file
        writes = 0

        def fail_second_write(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("injected HTML write failure")
            return original_write(*args, **kwargs)

        publisher._write_bundle_file = fail_second_write
        with self.assertRaisesRegex(OSError, "injected HTML"):
            publisher.publish(valid_report(summary="Must not become authoritative."))

        current = self.store.get_report("SYM-123")
        self.assertEqual(current["json_path"], previous["json_path"])
        self.assertEqual(current["html_path"], previous["html_path"])
        self.assertIn("Authoritative report.", publisher.read_current_html("SYM-123"))
        self.assertEqual(
            sorted(path.name for path in (self.root / "artifacts" / "SYM-123").iterdir()),
            sorted([Path(current["json_path"]).name, Path(current["html_path"]).name]),
        )

    def test_successful_publish_removes_every_non_authoritative_generation(self):
        publisher = self.publisher()
        publisher.publish(valid_report(summary="First generation."))
        report_dir = self.root / "artifacts" / "SYM-123"
        (report_dir / "report-crash.json").write_text("{}", encoding="utf-8")
        (report_dir / "report-crash.html").write_text("orphan", encoding="utf-8")

        publisher.publish(valid_report(summary="Second generation."))

        entry = self.store.get_report("SYM-123")
        self.assertEqual(
            sorted(path.name for path in report_dir.iterdir()),
            sorted([entry["json_path"], entry["html_path"]]),
        )

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
        publisher = self.publisher()
        self.linear.fail = True
        large_report = valid_report(
            implementation=[f"Implementation evidence {index}: " + ("x" * 500) for index in range(40)]
        )
        result = publisher.publish(large_report)

        self.assertTrue(result["success"])
        self.assertEqual(result["linear_sync_status"], "pending")
        pending = self.store.get_report("SYM-123")
        self.assertNotIn("document", pending)
        self.assertEqual(pending["linear_sync_status"], "pending")
        self.assertGreater(pending["next_retry_at"], 0)
        self.linear.fail = False
        restarted = self.publisher()
        self.assertEqual(restarted.sync_pending(now=pending["next_retry_at"]), 1)
        self.assertEqual(self.store.get_report("SYM-123")["linear_sync_status"], "synced")

    def test_missing_retry_artifact_records_failure_emits_error_and_advances_backoff(self):
        errors = []
        publisher = self.publisher(error_sink=errors.append)
        self.linear.fail = True
        publisher.publish(valid_report())
        pending = self.store.get_report("SYM-123")
        self.artifact_path(pending, "json_path").unlink()
        self.linear.fail = False

        self.assertEqual(publisher.sync_pending(now=pending["next_retry_at"]), 0)

        retried = self.store.get_report("SYM-123")
        self.assertEqual(retried["retry_count"], pending["retry_count"] + 1)
        self.assertGreater(retried["next_retry_at"], pending["next_retry_at"])
        self.assertEqual(errors[-1].stage, "report_sync")
        self.assertIn("artifact", errors[-1].message.lower())

    def test_corrupt_retry_artifact_records_failure_and_advances_backoff(self):
        errors = []
        publisher = self.publisher(error_sink=errors.append)
        self.linear.fail = True
        publisher.publish(valid_report())
        pending = self.store.get_report("SYM-123")
        self.artifact_path(pending, "json_path").write_text("{not-json", encoding="utf-8")
        self.linear.fail = False

        self.assertEqual(publisher.sync_pending(now=pending["next_retry_at"]), 0)

        retried = self.store.get_report("SYM-123")
        self.assertEqual(retried["retry_count"], pending["retry_count"] + 1)
        self.assertGreater(retried["next_retry_at"], pending["next_retry_at"])
        self.assertIn("corrupt", errors[-1].message.lower())

    def test_artifact_root_and_issue_directory_symlinks_are_rejected(self):
        real_root = self.root / "real-artifacts"
        real_root.mkdir()
        symlink_root = self.root / "artifacts-link"
        symlink_root.symlink_to(real_root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            self.publisher(artifact_root=symlink_root)

        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "SYM-123").symlink_to(real_root, target_is_directory=True)
        publisher = self.publisher(artifact_root=artifacts)
        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            publisher.publish(valid_report())

    def test_artifact_root_parent_swap_is_rejected_without_writing_replacement(self):
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        publisher = self.publisher(artifact_root=artifacts)
        pinned = self.root / "artifacts-pinned"
        artifacts.rename(pinned)
        artifacts.mkdir()

        with self.assertRaisesRegex(RuntimeError, "changed"):
            publisher.publish(valid_report())

        self.assertEqual(list(artifacts.iterdir()), [])

    def test_comment_lookup_paginates_and_recovers_create_before_state_save(self):
        publisher = self.publisher()
        publisher.publish(valid_report())
        entry = self.store.get_report("SYM-123")
        self.store.save_report({**entry, "linear_comment_id": None, "linear_sync_status": "pending", "next_retry_at": 1.0})
        existing = {"id": "comment-1", "body": "## Symphonz Implementation Report\nold"}
        self.linear.comment_pages = {
            None: {
                "nodes": [{"id": "newer-comment", "body": "Unrelated"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "older"},
            },
            "older": {
                "nodes": [existing],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
        self.linear.calls.clear()

        self.assertEqual(publisher.sync_pending(now=1.0), 1)

        creates = [query for query, _ in self.linear.calls if "SymphonzCreateReportComment" in query]
        updates = [query for query, _ in self.linear.calls if "SymphonzUpdateReportComment" in query]
        lookups = [variables.get("after") for query, variables in self.linear.calls if "SymphonzFindReportComment" in query]
        self.assertEqual(creates, [])
        self.assertEqual(len(updates), 1)
        self.assertEqual(lookups, [None, "older"])

    def test_sqlite_sync_lease_prevents_duplicate_cross_process_comment_creation(self):
        from symphonz.service.runtime_store import RuntimeStore

        lookup_started = threading.Event()
        release_lookup = threading.Event()

        class BlockingLinearClient(FakeLinearClient):
            block_lookups = False

            def graphql(self, query, variables):
                if self.block_lookups and "SymphonzFindReportComment" in query and not lookup_started.is_set():
                    lookup_started.set()
                    release_lookup.wait(timeout=2)
                return super().graphql(query, variables)

        linear = BlockingLinearClient()
        linear.fail = True
        first = self.publisher(linear_client=linear)
        first.publish(valid_report())
        pending = self.store.get_report("SYM-123")
        linear.fail = False
        linear.block_lookups = True
        linear.calls.clear()
        second = self.publisher(
            store=RuntimeStore(self.root / "runtime.sqlite3"),
            linear_client=linear,
        )
        results = []

        worker = threading.Thread(target=lambda: results.append(first.sync_pending(now=pending["next_retry_at"])))
        worker.start()
        self.assertTrue(lookup_started.wait(timeout=2))
        results.append(second.sync_pending(now=pending["next_retry_at"]))
        release_lookup.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(results), [0, 1])
        creates = [query for query, _ in linear.calls if "SymphonzCreateReportComment" in query]
        self.assertEqual(len(creates), 1)

    def test_sync_heartbeat_keeps_a_slow_linear_operation_exclusively_leased(self):
        lookup_started = threading.Event()
        release_lookup = threading.Event()

        class SlowLinearClient(FakeLinearClient):
            block = False

            def graphql(self, query, variables):
                if self.block and "SymphonzFindReportComment" in query and not lookup_started.is_set():
                    lookup_started.set()
                    release_lookup.wait(timeout=2)
                return super().graphql(query, variables)

        linear = SlowLinearClient()
        linear.fail = True
        first = self.publisher(linear_client=linear, sync_lease_seconds=0.06)
        first.publish(valid_report())
        pending = self.store.get_report("SYM-123")
        linear.fail = False
        linear.block = True
        second = self.publisher(
            store=type(self.store)(self.root / "runtime.sqlite3"),
            linear_client=linear,
            sync_lease_seconds=0.06,
        )
        results = []

        worker = threading.Thread(target=lambda: results.append(first.sync_pending(now=pending["next_retry_at"])))
        worker.start()
        self.assertTrue(lookup_started.wait(timeout=2))
        time.sleep(0.15)
        results.append(second.sync_pending(now=time.time()))
        release_lookup.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(results), [0, 1])
        creates = [query for query, _ in linear.calls if "SymphonzCreateReportComment" in query]
        self.assertEqual(len(creates), 1)

    def test_worker_that_loses_its_lease_does_not_mutate_linear_or_sync_state(self):
        class LeaseStealingLinearClient(FakeLinearClient):
            publisher = None
            steal = False

            def graphql(self, query, variables):
                if self.steal and "SymphonzFindReportComment" in query:
                    self.steal = False
                    connection = self.publisher.store._connect()
                    try:
                        connection.execute(
                            "UPDATE report_sync_leases SET owner = ?, expires_at = ? "
                            "WHERE issue_identifier = ?",
                            ("replacement", time.time() + 60, "SYM-123"),
                        )
                    finally:
                        connection.close()
                return super().graphql(query, variables)

        linear = LeaseStealingLinearClient()
        linear.fail = True
        publisher = self.publisher(linear_client=linear)
        linear.publisher = publisher
        publisher.publish(valid_report())
        pending = self.store.get_report("SYM-123")
        linear.fail = False
        linear.steal = True
        linear.calls.clear()

        self.assertEqual(publisher.sync_pending(now=pending["next_retry_at"]), 0)

        self.assertEqual(self.store.get_report("SYM-123")["linear_sync_status"], "pending")
        self.assertEqual(
            [query for query, _ in linear.calls if "SymphonzCreateReportComment" in query],
            [],
        )
        self.assertEqual(
            [query for query, _ in linear.calls if "SymphonzUpdateReportComment" in query],
            [],
        )

    def test_expired_heartbeat_checks_the_current_wall_clock_before_mutation(self):
        from symphonz.service.reporting import _SyncLeaseHeartbeat, _SyncLeaseLost

        publisher = self.publisher(linear_client=None, sync_lease_seconds=0.05)
        publisher.store.save_report(
            {
                "issue_identifier": "SYM-123",
                "json_path": "report-current.json",
                "linear_sync_status": "pending",
                "next_retry_at": time.time(),
            }
        )
        lease_started_at = time.time()
        publisher.store.claim_report_sync(
            "SYM-123", owner="old-owner", now=lease_started_at, lease_seconds=0.05,
        )
        heartbeat = _SyncLeaseHeartbeat(publisher.store, "SYM-123", "old-owner", 0.05)

        time.sleep(0.1)

        with self.assertRaises(_SyncLeaseLost):
            heartbeat.require_owned(lease_started_at)

    def test_graphql_business_failure_or_missing_comment_id_remains_pending(self):
        self.linear.create_success = False
        created = self.publisher().publish(valid_report())
        self.assertEqual(created["linear_sync_status"], "pending")

        self.linear.create_success = True
        self.linear.comments = [{"id": "comment-1", "body": "## Symphonz Implementation Report\nold"}]
        self.linear.update_success = False
        pending = self.store.get_report("SYM-123")
        self.store.save_report({**pending, "linear_sync_status": "pending", "next_retry_at": 2.0})
        self.assertEqual(self.publisher().sync_pending(now=2.0), 0)
        self.assertEqual(self.store.get_report("SYM-123")["linear_sync_status"], "pending")

        self.linear.update_success = True
        self.linear.omit_mutation_id = True
        pending = self.store.get_report("SYM-123")
        self.store.save_report({**pending, "linear_sync_status": "pending", "next_retry_at": 3.0})
        self.assertEqual(self.publisher().sync_pending(now=3.0), 0)
        self.assertEqual(self.store.get_report("SYM-123")["linear_sync_status"], "pending")

    def test_linear_markdown_neutralizes_ai_controlled_fields(self):
        publisher = self.publisher()
        report = valid_report(
            summary=(
                "@all\n## forged heading\n[click](https://evil.test)\n```owned``` "
                "http://evil.test/x https://evil.test/y HTTP://evil.test/z HtTpS://evil.test/q"
            ),
            review={
                **valid_report()["review"],
                "branch": "topic`\n@team",
                "commit": "abc`def",
            },
        )

        publisher.publish(report)

        body = self.linear.comments[0]["body"]
        self.assertNotIn("@all", body)
        self.assertNotIn("\n## forged", body)
        self.assertNotIn("[click](", body)
        self.assertNotIn("```owned```", body)
        self.assertNotIn("`topic`", body)
        self.assertNotIn("http://evil.test", body)
        self.assertNotIn("https://evil.test", body)
        self.assertNotIn("HTTP://evil.test", body)
        self.assertNotIn("HtTpS://evil.test", body)
        self.assertEqual(body.count("## Symphonz Implementation Report"), 1)

    def test_report_sync_failure_uses_error_sink_and_later_success_resolves_database_error(self):
        errors = []
        publisher = self.publisher(error_sink=errors.append)
        self.linear.fail = True
        publisher.publish(valid_report())
        unresolved = self.store.list_errors(issue_identifier="SYM-123", stage="report_sync", resolved=False)["items"]

        self.assertEqual(len(errors), 1)
        self.assertEqual(len(unresolved), 1)

        self.linear.fail = False
        pending = self.store.get_report("SYM-123")
        self.assertEqual(publisher.sync_pending(now=pending["next_retry_at"]), 1)
        self.assertEqual(
            self.store.list_errors(issue_identifier="SYM-123", stage="report_sync", resolved=False)["items"],
            [],
        )
        resolved = self.store.list_errors(issue_identifier="SYM-123", stage="report_sync", resolved=True)["items"]
        self.assertEqual(resolved[0]["resolving_event"], "report_sync_succeeded")

    def test_public_base_url_rejects_ambiguous_authority_and_request_components(self):
        invalid = [
            "ftp://reports.example.test/base",
            "https:///base",
            "https://user@reports.example.test/base",
            "https://reports.example.test/base?tenant=one",
            "https://reports.example.test/base#fragment",
            "https://reports.example.test/base/../admin",
            "https://reports.example.test:/base",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "public_base_url"):
                self.publisher(public_base_url=value)

        publisher = self.publisher(public_base_url="https://reports.example.test//team///reports/")
        self.assertEqual(
            publisher.report_url("SYM-123"),
            "https://reports.example.test/team/reports/issues/SYM-123/report",
        )

    def test_tool_spec_is_strict_publish_only(self):
        from symphonz.service.reporting import report_tool_spec

        spec = report_tool_spec()

        self.assertEqual(spec["name"], "symphonz_report")
        schema = spec["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["operation"], {"type": "string", "const": "publish"})
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["title"]["maxLength"], 255)
        self.assertEqual(schema["properties"]["implementation"]["maxItems"], 50)
        self.assertFalse(schema["properties"]["architecture"]["additionalProperties"])
        node_schema = schema["properties"]["architecture"]["properties"]["nodes"]
        self.assertEqual(node_schema["maxItems"], 24)
        self.assertFalse(node_schema["items"]["additionalProperties"])
        self.assertEqual(set(node_schema["items"]["required"]), {"id", "label"})
        self.assertFalse(schema["properties"]["review"]["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]["review"]["required"]),
            {"provider", "url", "branch", "commit", "target"},
        )
        url_schema = schema["properties"]["review"]["properties"]["url"]
        self.assertIn("pattern", url_schema)
        self.assertIsNone(re.fullmatch(url_schema["pattern"], "https://user:secret@reviews.example.test/1"))
        self.assertIsNone(re.fullmatch(url_schema["pattern"], "ftp://reviews.example.test/1"))
        for invalid in (
            "https:///1",
            "https://reviews.example.test/1?view=full",
            "https://reviews.example.test/1#fragment",
            "https://reviews.example.test/1/\x01",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(re.fullmatch(url_schema["pattern"], invalid))
        self.assertIsNotNone(re.fullmatch(url_schema["pattern"], "https://reviews.example.test/1"))

    def test_issue_publish_lock_serializes_cross_process_publication(self):
        publisher = self.publisher(linear_client=None)
        publisher.publish(valid_report(summary="Initial generation."))
        read_fd, write_fd = os.pipe()
        try:
            with publisher._issue_publish_lock("SYM-123"):
                child_pid = os.fork()
                if child_pid == 0:
                    os.close(read_fd)
                    try:
                        publisher.publish(valid_report(summary="Child generation."))
                        os.write(write_fd, b"done")
                    except BaseException:
                        os.write(write_fd, b"error")
                    finally:
                        os.close(write_fd)
                        os._exit(0)
                os.close(write_fd)
                time.sleep(0.1)
                self.assertEqual(os.waitpid(child_pid, os.WNOHANG), (0, 0))

            _, status = os.waitpid(child_pid, 0)
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.read(read_fd, 5), b"done")
            self.assertEqual(
                json.loads(publisher.read_current_json("SYM-123"))["summary"],
                "Child generation.",
            )
        finally:
            os.close(read_fd)
