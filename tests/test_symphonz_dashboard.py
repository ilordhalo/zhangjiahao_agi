from __future__ import annotations

import base64
from http.client import HTTPConnection
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from urllib.parse import urlencode

from symphonz.service.auth import AuthService, hash_password
from symphonz.service.dashboard import DashboardServer
from symphonz.service.models import RuntimeErrorRecord, RuntimeEvent
from symphonz.service.runtime_store import RuntimeStore


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_record = hash_password("dashboard-secret")
        cls.session_secret = base64.b64encode(b"s" * 32).decode("ascii")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.store = RuntimeStore(self.root / "runtime.sqlite3")
        self.auth = AuthService(
            self.store,
            "admin",
            self.password_record,
            self.session_secret,
            session_days=30,
        )
        self._seed_runtime()
        self.server = DashboardServer(
            "127.0.0.1",
            0,
            self.store,
            self.auth,
            self.artifacts,
            insecure_warning=True,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

    def _seed_runtime(self):
        self.store.upsert_issue(
            {
                "issue_identifier": "SYM-1",
                "issue_id": "issue-1",
                "title": "构建 <script>alert(1)</script> 仪表盘",
                "linear_state": "In Progress",
                "status": "running",
                "attempt": 2,
                "workspace": "/tmp/symphonz/SYM-1",
                "codex_session_id": "session-1",
                "branch": "codex/dashboard",
                "commit_hash": "abc1234",
                "review_url": "https://git.example.test/symphonz/merge_requests/4",
                "report_url": "https://dashboard.example.test/issues/SYM-1/report",
                "updated_at": 200.0,
            }
        )
        self.store.upsert_issue(
            {
                "issue_identifier": "SYM-2",
                "issue_id": "issue-2",
                "title": "完成历史任务",
                "linear_state": "Done",
                "status": "completed",
                "attempt": 1,
                "updated_at": 100.0,
            }
        )
        self.store.record_event(
            RuntimeEvent(
                "codex_session_started",
                "Codex 会话已启动",
                "SYM-1",
                timestamp=201.0,
                data={"category": "codex", "severity": "info", "session_id": "session-1"},
            )
        )
        self.store.record_event(
            RuntimeEvent(
                "agent_message_delta",
                "private streaming delta",
                "SYM-1",
                timestamp=202.0,
                data={"category": "codex", "severity": "info"},
            )
        )
        self.store.record_error(
            RuntimeErrorRecord(
                issue_identifier="SYM-1",
                session_id="session-1",
                stage="codex",
                error_type="TurnTimeout",
                message="执行超时",
                retryable=True,
                attempt=2,
                timestamp=203.0,
                context={"token": "must-not-leak", "detail": "turn exceeded deadline"},
            )
        )
        issue_dir = self.artifacts / "SYM-1"
        issue_dir.mkdir()
        self.report_html = (
            "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>SYM-1 实施报告</title></head><body><h1>权威实施报告</h1></body></html>"
        )
        (issue_dir / "report-current123.html").write_text(self.report_html, encoding="utf-8")
        (issue_dir / "report-current123.json").write_text("{}", encoding="utf-8")
        self.store.save_report(
            {
                "issue_identifier": "SYM-1",
                "report_version": 1,
                "json_path": "report-current123.json",
                "html_path": "report-current123.html",
                "url": "https://dashboard.example.test/issues/SYM-1/report",
                "review_metadata": {"provider": "gitlab", "branch": "codex/dashboard"},
                "linear_sync_status": "synced",
                "created_at": 204.0,
                "updated_at": 204.0,
                "summary": "Dashboard implementation",
            }
        )

    def request(self, method, path, *, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        connection.request(method, path, body=encoded, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = SimpleNamespace(
            status=response.status,
            headers={name: value for name, value in response.getheaders()},
            body=raw,
            text=raw.decode("utf-8"),
        )
        connection.close()
        return result

    def get(self, path, *, cookie=None):
        headers = {"Cookie": cookie} if cookie else None
        return self.request("GET", path, headers=headers)

    def authenticated_cookie(self):
        result = self.auth.login("admin", "dashboard-secret", "test-client")
        return result.set_cookie.split(";", 1)[0]

    def post_form(self, path, values, *, cookie=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        return self.request("POST", path, body=urlencode(values), headers=headers)

    def json_body(self, response):
        return json.loads(response.body.decode("utf-8"))

    def test_public_routes_and_authentication_gate_do_not_leak_task_data(self):
        login = self.get("/login?next=%2Ftasks")
        health = self.get("/healthz")
        page = self.get("/tasks?status=running")
        report = self.get("/issues/SYM-1/report")
        api = self.get("/api/tasks")

        self.assertEqual(login.status, 200)
        self.assertIn('value="/tasks"', login.text)
        self.assertIn("未加密连接", login.text)
        self.assertNotIn("SYM-1", login.text)
        self.assertEqual(health.status, 200)
        self.assertEqual(self.json_body(health), {"status": "ok"})
        self.assertEqual(page.status, 303)
        self.assertEqual(page.headers["Location"], "/login?next=%2Ftasks%3Fstatus%3Drunning")
        self.assertNotIn("SYM-1", page.text)
        self.assertEqual(report.status, 303)
        self.assertEqual(report.headers["Location"], "/login?next=%2Fissues%2FSYM-1%2Freport")
        self.assertNotIn("权威实施报告", report.text)
        self.assertEqual(api.status, 401)
        self.assertEqual(self.json_body(api)["error"]["code"], "authentication_required")
        self.assertNotIn("SYM-1", api.text)

    def test_login_sets_auth_cookie_and_honors_only_safe_next_paths(self):
        response = self.post_form(
            "/login",
            {"username": "admin", "password": "dashboard-secret", "next": "/issues/SYM-1/report"},
        )

        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/issues/SYM-1/report")
        set_cookie = response.headers["Set-Cookie"]
        self.assertIn("symphonz_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        self.assertIn("Path=/", set_cookie)
        self.assertIn("Max-Age=2592000", set_cookie)
        cookie = set_cookie.split(";", 1)[0]
        served = self.get("/issues/SYM-1/report", cookie=cookie)
        self.assertEqual(served.status, 200)
        self.assertEqual(served.text, self.report_html)

        for unsafe_next in ("//evil.example.test/steal", "/\r\nX-Injected: yes"):
            with self.subTest(next=unsafe_next):
                unsafe = self.post_form(
                    "/login",
                    {"username": "admin", "password": "dashboard-secret", "next": unsafe_next},
                )
                self.assertEqual(unsafe.status, 303)
                self.assertEqual(unsafe.headers["Location"], "/")
                self.assertNotIn("X-Injected", str(unsafe.headers))

    def test_invalid_login_and_request_limits_return_bounded_errors(self):
        wrong = self.post_form(
            "/login",
            {"username": "admin", "password": "wrong", "next": "/tasks"},
        )
        wrong_type = self.request(
            "POST",
            "/login",
            body='{"username":"admin"}',
            headers={"Content-Type": "application/json"},
        )
        oversized = self.request(
            "POST",
            "/login",
            body="x" * 4097,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        long_target = self.get("/login?next=/" + ("a" * 8200))
        duplicate_next = self.get("/login?next=%2Ftasks&next=%2Ferrors")

        self.assertEqual(wrong.status, 401)
        self.assertIn("用户名或密码错误", wrong.text)
        self.assertNotIn("wrong", wrong.text)
        self.assertEqual(wrong_type.status, 415)
        self.assertIn("登录运行中心", wrong_type.text)
        self.assertNotIn("退出登录", wrong_type.text)
        self.assertEqual(oversized.status, 413)
        self.assertIn("登录运行中心", oversized.text)
        self.assertNotIn("退出登录", oversized.text)
        self.assertEqual(long_target.status, 414)
        self.assertIn("登录运行中心", long_target.text)
        self.assertEqual(duplicate_next.status, 400)
        self.assertIn("登录运行中心", duplicate_next.text)

    def test_logout_is_post_only_expires_cookie_and_invalidates_session(self):
        cookie = self.authenticated_cookie()

        get_logout = self.get("/logout", cookie=cookie)
        response = self.post_form("/logout", {}, cookie=cookie)
        after = self.get("/api/tasks", cookie=cookie)

        self.assertEqual(get_logout.status, 405)
        self.assertEqual(get_logout.headers["Allow"], "POST")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertEqual(after.status, 401)

    def test_authenticated_pages_render_chinese_first_content_escaped_and_responsive(self):
        cookie = self.authenticated_cookie()

        overview = self.get("/", cookie=cookie)
        tasks = self.get("/tasks?status=running&q=SYM", cookie=cookie)
        detail = self.get("/issues/SYM-1?tab=timeline&category=codex", cookie=cookie)
        errors = self.get("/errors?resolved=false", cookie=cookie)

        for response in (overview, tasks, detail, errors):
            self.assertEqual(response.status, 200)
            self.assertIn("未加密连接", response.text)
            self.assertIn("@media (max-width: 760px)", response.text)
            self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn("运行概览", overview.text)
        self.assertIn("当前运行", overview.text)
        self.assertIn("最近报告", overview.text)
        self.assertIn("任务列表", tasks.text)
        self.assertIn("构建 &lt;script&gt;alert(1)&lt;/script&gt; 仪表盘", tasks.text)
        self.assertNotIn("<script>alert(1)</script>", tasks.text)
        self.assertIn("value=\"running\" selected", tasks.text)
        self.assertIn("概览", detail.text)
        self.assertIn("时间线", detail.text)
        self.assertIn("报告", detail.text)
        self.assertIn("错误", detail.text)
        self.assertIn("Codex 会话已启动", detail.text)
        self.assertNotIn("private streaming delta", detail.text)
        self.assertIn("错误中心", errors.text)
        self.assertIn("执行超时", errors.text)
        self.assertIn("相关事件", errors.text)
        self.assertIn("Codex 会话已启动", errors.text)
        self.assertIn("[REDACTED]", errors.text)
        self.assertNotIn("must-not-leak", errors.text)

    def test_timeline_pagination_does_not_skip_significant_events_after_quiet_deltas(self):
        self.store.record_event(
            RuntimeEvent(
                "git_pushed",
                "分支已推送",
                "SYM-1",
                timestamp=205.0,
                data={"category": "git", "severity": "info"},
            )
        )
        self.store.record_event(
            RuntimeEvent(
                "report_published",
                "报告已发布",
                "SYM-1",
                timestamp=206.0,
                data={"category": "report", "severity": "info"},
            )
        )

        response = self.get(
            "/issues/SYM-1?tab=timeline&limit=1",
            cookie=self.authenticated_cookie(),
        )

        self.assertEqual(response.status, 200)
        self.assertIn("报告已发布", response.text)
        self.assertNotIn("分支已推送", response.text)
        self.assertIn("下一页", response.text)

    def test_overview_api_returns_deterministic_operational_summary(self):
        response = self.get("/api/overview", cookie=self.authenticated_cookie())

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
        payload = self.json_body(response)
        self.assertEqual(
            payload["counts"],
            {
                "blocked": 0,
                "completed": 1,
                "queued": 0,
                "retrying": 0,
                "running": 1,
                "unresolved_errors": 1,
            },
        )
        self.assertEqual(payload["service"]["status"], "healthy")
        self.assertEqual(payload["current_runs"][0]["issue_identifier"], "SYM-1")
        self.assertEqual(payload["recent_reports"][0]["issue_identifier"], "SYM-1")
        self.assertNotIn("html_path", response.text)
        self.assertNotIn("json_path", response.text)
        self.assertNotIn("agent_message_delta", response.text)
        expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self.assertEqual(response.text, expected)

    def test_task_and_issue_apis_support_filters_pagination_and_safe_metadata(self):
        cookie = self.authenticated_cookie()

        tasks = self.get("/api/tasks?status=completed&q=%E5%8E%86%E5%8F%B2&limit=1", cookie=cookie)
        issue = self.get("/api/issues/SYM-1", cookie=cookie)
        events = self.get("/api/issues/SYM-1/events?category=codex&limit=1", cookie=cookie)
        errors = self.get("/api/issues/SYM-1/errors?stage=codex&resolved=false", cookie=cookie)

        self.assertEqual(tasks.status, 200)
        self.assertEqual([item["issue_identifier"] for item in self.json_body(tasks)["items"]], ["SYM-2"])
        self.assertEqual(issue.status, 200)
        issue_payload = self.json_body(issue)
        self.assertEqual(issue_payload["task"]["issue_identifier"], "SYM-1")
        self.assertTrue(issue_payload["report"]["available"])
        self.assertNotIn("html_path", issue.text)
        self.assertNotIn("json_path", issue.text)
        self.assertEqual(events.status, 200)
        self.assertEqual(self.json_body(events)["items"][0]["type"], "agent_message_delta")
        self.assertEqual(errors.status, 200)
        self.assertEqual(self.json_body(errors)["items"][0]["error_type"], "TurnTimeout")
        self.assertNotIn("must-not-leak", errors.text)

    def test_global_error_api_filters_resolution_and_preserves_redaction(self):
        cookie = self.authenticated_cookie()
        unresolved = self.get("/api/errors?issue=SYM-1&type=TurnTimeout&resolved=false", cookie=cookie)
        resolved = self.get("/api/errors?resolved=true", cookie=cookie)

        self.assertEqual(unresolved.status, 200)
        self.assertEqual(len(self.json_body(unresolved)["items"]), 1)
        self.assertIn("[REDACTED]", unresolved.text)
        self.assertNotIn("must-not-leak", unresolved.text)
        self.assertEqual(resolved.status, 200)
        self.assertEqual(self.json_body(resolved)["items"], [])

    def test_report_route_serves_only_runtime_store_authoritative_generation(self):
        cookie = self.authenticated_cookie()
        issue_dir = self.artifacts / "SYM-1"
        (issue_dir / "report-attacker999.html").write_text("attacker supplied path", encoding="utf-8")

        first = self.get("/issues/SYM-1/report?path=report-attacker999.html", cookie=cookie)
        self.assertEqual(first.status, 200)
        self.assertEqual(first.text, self.report_html)
        self.assertNotIn("attacker supplied path", first.text)

        replacement = "<!doctype html><html><body><h1>新权威报告</h1></body></html>"
        (issue_dir / "report-next456.html").write_text(replacement, encoding="utf-8")
        current = self.store.get_report("SYM-1")
        self.store.save_report({**current, "html_path": "report-next456.html", "updated_at": 205.0})

        second = self.get("/issues/SYM-1/report", cookie=cookie)
        self.assertEqual(second.status, 200)
        self.assertEqual(second.text, replacement)
        self.assertIn("default-src 'none'", second.headers["Content-Security-Policy"])

    def test_authenticated_not_found_bad_query_and_wrong_method_are_consistent(self):
        cookie = self.authenticated_cookie()

        page = self.get("/issues/SYM-404", cookie=cookie)
        report = self.get("/issues/SYM-2/report", cookie=cookie)
        api = self.get("/api/issues/SYM-404", cookie=cookie)
        bad_limit = self.get("/api/tasks?limit=zero", cookie=cookie)
        wrong_method = self.request("POST", "/api/tasks", body=b"", headers={"Cookie": cookie})

        self.assertEqual(page.status, 404)
        self.assertIn("未找到", page.text)
        self.assertEqual(report.status, 404)
        self.assertEqual(api.status, 404)
        self.assertEqual(self.json_body(api)["error"]["code"], "not_found")
        self.assertEqual(bad_limit.status, 400)
        self.assertEqual(self.json_body(bad_limit)["error"]["code"], "invalid_request")
        self.assertEqual(wrong_method.status, 405)
        self.assertEqual(wrong_method.headers["Allow"], "GET")


if __name__ == "__main__":
    unittest.main()
