from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread
import time
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from symphonz.service.auth import (
    AuthenticationError,
    LoginLockedError,
    SESSION_COOKIE_NAME,
    safe_next_path,
)
from symphonz.service.reporting import ReportArtifactReader
from symphonz.service.runtime_store import RuntimeStoreInputError
from symphonz.service.web_templates import (
    render_error_page,
    render_errors_page,
    render_issue_page,
    render_login_page,
    render_overview_page,
    render_tasks_page,
)


_MAX_REQUEST_TARGET_BYTES = 8192
_MAX_FORM_BYTES = 4096
_MAX_QUERY_FIELDS = 32
_ISSUE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*-[0-9]+")
_DASHBOARD_CSP = (
    "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)
_REPORT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
_QUIET_EVENT_MARKERS = ("delta", "token_count", "token_usage", "text_stream")


class _RequestInputError(ValueError):
    pass


class DashboardServer:
    def __init__(
        self,
        host: str,
        port: int,
        store,
        auth_service,
        artifacts_root: Path,
        insecure_warning: bool,
    ):
        self.host = host
        self.requested_port = port
        self.store = store
        self.auth_service = auth_service
        self.artifacts_root = Path(artifacts_root)
        self.insecure_warning = bool(insecure_warning)
        self.started_at = time.time()
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None
        self.report_reader: ReportArtifactReader | None = None

    @property
    def port(self) -> int:
        if self.httpd is None:
            return self.requested_port
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        if self.httpd is not None:
            raise RuntimeError("Dashboard server is already running")
        dashboard = self
        report_reader = ReportArtifactReader(self.store, self.artifacts_root)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                target = self._parse_target()
                if target is None:
                    return
                path, query = target
                if path == "/healthz":
                    self._respond_json({"status": "ok"})
                    return
                if path == "/login":
                    try:
                        next_path = safe_next_path(_query_value(query, "next"))
                    except _RequestInputError:
                        self._respond_html(
                            render_login_page(
                                None,
                                error="登录返回地址无效。",
                                insecure_warning=dashboard.insecure_warning,
                            ),
                            status=400,
                        )
                        return
                    self._respond_html(
                        render_login_page(next_path, insecure_warning=dashboard.insecure_warning)
                    )
                    return
                if not self._authenticated(path):
                    return
                try:
                    self._dispatch_get(path, query)
                except (RuntimeStoreInputError, _RequestInputError, UnicodeDecodeError):
                    self._respond_request_error(path, 400, "invalid_request", "请求参数无效。")

            def do_POST(self) -> None:
                target = self._parse_target()
                if target is None:
                    return
                path, _query = target
                if path == "/login":
                    self._login()
                    return
                if not self._authenticated(path):
                    return
                if path == "/logout":
                    self._logout()
                    return
                if _is_known_get_route(path):
                    self._method_not_allowed(path, "GET")
                    return
                self._not_found(path)

            def _dispatch_get(self, path: str, query: dict[str, list[str]]) -> None:
                if path == "/logout":
                    self._method_not_allowed(path, "POST")
                    return
                if path == "/":
                    self._respond_html(
                        render_overview_page(
                            _overview_payload(dashboard.store, dashboard.started_at),
                            insecure_warning=dashboard.insecure_warning,
                        )
                    )
                    return
                if path == "/tasks":
                    filters = _task_filters(query)
                    page = dashboard.store.list_tasks(
                        status=filters["status"],
                        query=filters["query"],
                        cursor=filters["cursor"],
                        limit=filters["limit"],
                    )
                    self._respond_html(
                        render_tasks_page(page, filters, insecure_warning=dashboard.insecure_warning)
                    )
                    return
                if path == "/errors":
                    filters = _error_filters(query)
                    page = dashboard.store.list_errors(
                        issue_identifier=filters["issue"],
                        stage=filters["stage"],
                        error_type=filters["error_type"],
                        resolved=filters["resolved"],
                        cursor=filters["cursor"],
                        limit=filters["limit"],
                    )
                    page = _with_nearby_events(dashboard.store, page)
                    self._respond_html(
                        render_errors_page(page, filters, insecure_warning=dashboard.insecure_warning)
                    )
                    return

                issue_route = _parse_issue_route(path)
                if issue_route is not None:
                    identifier, suffix = issue_route
                    if suffix == "report":
                        self._report(identifier)
                    elif suffix is None:
                        self._issue_page(identifier, query)
                    else:
                        self._not_found(path)
                    return

                if path == "/api/overview":
                    self._respond_json(_overview_payload(dashboard.store, dashboard.started_at))
                    return
                if path == "/api/tasks":
                    filters = _task_filters(query)
                    self._respond_json(
                        dashboard.store.list_tasks(
                            status=filters["status"],
                            query=filters["query"],
                            cursor=filters["cursor"],
                            limit=filters["limit"],
                        )
                    )
                    return

                api_route = _parse_api_issue_route(path)
                if api_route is not None:
                    identifier, resource = api_route
                    self._issue_api(identifier, resource, query)
                    return
                if path == "/api/errors":
                    filters = _error_filters(query)
                    self._respond_json(
                        dashboard.store.list_errors(
                            issue_identifier=filters["issue"],
                            stage=filters["stage"],
                            error_type=filters["error_type"],
                            resolved=filters["resolved"],
                            cursor=filters["cursor"],
                            limit=filters["limit"],
                        )
                    )
                    return
                self._not_found(path)

            def _issue_page(self, identifier: str, query: dict[str, list[str]]) -> None:
                task = dashboard.store.get_task(identifier)
                if task is None:
                    self._not_found(self.path)
                    return
                selected_tab = _query_value(query, "tab") or "overview"
                if selected_tab not in {"overview", "timeline", "report", "errors"}:
                    selected_tab = "overview"
                event_filters = _event_filters(query)
                error_filters = _error_filters(query, default_issue=identifier)
                events = _significant_events_page(
                    dashboard.store,
                    issue_identifier=identifier,
                    category=event_filters["category"],
                    event_type=event_filters["event_type"],
                    cursor=event_filters["cursor"],
                    limit=event_filters["limit"],
                )
                errors = dashboard.store.list_errors(
                    issue_identifier=identifier,
                    stage=error_filters["stage"],
                    error_type=error_filters["error_type"],
                    resolved=error_filters["resolved"],
                    cursor=error_filters["cursor"],
                    limit=error_filters["limit"],
                )
                errors = _with_nearby_events(dashboard.store, errors)
                self._respond_html(
                    render_issue_page(
                        task,
                        _report_metadata(dashboard.store.get_report(identifier)),
                        events,
                        errors,
                        selected_tab=selected_tab,
                        filters=event_filters,
                        insecure_warning=dashboard.insecure_warning,
                    )
                )

            def _issue_api(
                self,
                identifier: str,
                resource: str | None,
                query: dict[str, list[str]],
            ) -> None:
                task = dashboard.store.get_task(identifier)
                if task is None:
                    self._json_error(404, "not_found", "未找到任务。")
                    return
                if resource is None:
                    self._respond_json(
                        {
                            "report": _report_metadata(dashboard.store.get_report(identifier)),
                            "task": task,
                        }
                    )
                    return
                if resource == "events":
                    filters = _event_filters(query)
                    self._respond_json(
                        dashboard.store.list_events(
                            issue_identifier=identifier,
                            severity=filters["severity"],
                            category=filters["category"],
                            event_type=filters["event_type"],
                            cursor=filters["cursor"],
                            limit=filters["limit"],
                        )
                    )
                    return
                if resource == "errors":
                    filters = _error_filters(query, default_issue=identifier)
                    self._respond_json(
                        dashboard.store.list_errors(
                            issue_identifier=identifier,
                            stage=filters["stage"],
                            error_type=filters["error_type"],
                            resolved=filters["resolved"],
                            cursor=filters["cursor"],
                            limit=filters["limit"],
                        )
                    )
                    return
                self._json_error(404, "not_found", "未找到请求的资源。")

            def _report(self, identifier: str) -> None:
                if dashboard.store.get_report(identifier) is None:
                    self._not_found(self.path)
                    return
                try:
                    report_html = report_reader.read_current_html(identifier)
                except RuntimeError:
                    self._respond_html(
                        render_error_page(
                            "报告不可用",
                            "权威报告文件缺失或未通过安全校验。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=500,
                    )
                    return
                self._respond_report(report_html)

            def _login(self) -> None:
                form = self._read_form(public=True)
                if form is None:
                    return
                username = form.get("username", "")
                password = form.get("password", "")
                next_path = safe_next_path(form.get("next"))
                try:
                    result = dashboard.auth_service.login(
                        username,
                        password,
                        str(self.client_address[0]),
                    )
                except LoginLockedError:
                    self._respond_html(
                        render_login_page(
                            next_path,
                            error="登录尝试过多，请稍后重试。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=429,
                    )
                    return
                except AuthenticationError:
                    self._respond_html(
                        render_login_page(
                            next_path,
                            error="用户名或密码错误。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=401,
                    )
                    return
                self._redirect(next_path or "/", set_cookie=result.set_cookie)

            def _logout(self) -> None:
                form = self._read_form()
                if form is None:
                    return
                dashboard.auth_service.logout(self._session_token())
                cookie = SimpleCookie()
                cookie[SESSION_COOKIE_NAME] = ""
                morsel = cookie[SESSION_COOKIE_NAME]
                morsel["httponly"] = True
                morsel["samesite"] = "Lax"
                morsel["path"] = "/"
                morsel["max-age"] = "0"
                if dashboard.auth_service.secure_cookie:
                    morsel["secure"] = True
                self._redirect("/login", set_cookie=morsel.OutputString())

            def _read_form(self, *, public: bool = False) -> dict[str, str] | None:
                length_header = self.headers.get("Content-Length", "0")
                try:
                    length = int(length_header)
                except ValueError:
                    self._form_error(400, "Content-Length 无效。", public=public)
                    return None
                if length < 0:
                    self._form_error(400, "请求长度无效。", public=public)
                    return None
                if length > _MAX_FORM_BYTES:
                    self._form_error(413, "表单超过允许大小。", public=public)
                    return None
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/x-www-form-urlencoded":
                    self._form_error(415, "仅接受表单编码请求。", public=public)
                    return None
                try:
                    raw = self.rfile.read(length).decode("utf-8")
                    values = parse_qs(raw, keep_blank_values=True, max_num_fields=8)
                except (UnicodeDecodeError, ValueError):
                    self._form_error(400, "表单编码无效。", public=public)
                    return None
                if any(len(items) != 1 for items in values.values()):
                    self._form_error(400, "表单字段不能重复。", public=public)
                    return None
                return {key: items[0] for key, items in values.items()}

            def _form_error(self, status: int, message: str, *, public: bool) -> None:
                if public:
                    page = render_login_page(
                        None,
                        error=message,
                        insecure_warning=dashboard.insecure_warning,
                    )
                else:
                    page = render_error_page(
                        "请求无效",
                        message,
                        insecure_warning=dashboard.insecure_warning,
                    )
                self._respond_html(page, status=status)

            def _parse_target(self) -> tuple[str, dict[str, list[str]]] | None:
                if len(self.path.encode("utf-8", "surrogatepass")) > _MAX_REQUEST_TARGET_BYTES:
                    self._respond_html(
                        render_login_page(
                            None,
                            error="请求地址超过允许长度。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=414,
                    )
                    return None
                try:
                    parsed = urlsplit(self.path)
                    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
                        raise ValueError("request target must be origin-form")
                    query = parse_qs(
                        parsed.query,
                        keep_blank_values=True,
                        max_num_fields=_MAX_QUERY_FIELDS,
                    )
                except ValueError:
                    self._respond_html(
                        render_login_page(
                            None,
                            error="请求地址无效。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=400,
                    )
                    return None
                return parsed.path, query

            def _authenticated(self, path: str) -> bool:
                if dashboard.auth_service.authenticate_cookie(self.headers.get("Cookie")) is not None:
                    return True
                if _is_api_path(path):
                    self._json_error(401, "authentication_required", "需要登录。")
                else:
                    next_path = safe_next_path(self.path) or "/"
                    self._redirect("/login?" + urlencode({"next": next_path}))
                return False

            def _session_token(self) -> str:
                header = self.headers.get("Cookie")
                if not header:
                    return ""
                cookie = SimpleCookie()
                try:
                    cookie.load(header)
                except CookieError:
                    return ""
                morsel = cookie.get(SESSION_COOKIE_NAME)
                return morsel.value if morsel is not None else ""

            def _not_found(self, path: str) -> None:
                if _is_api_path(path):
                    self._json_error(404, "not_found", "未找到请求的资源。")
                else:
                    self._respond_html(
                        render_error_page(
                            "未找到",
                            "请求的页面或任务不存在。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=404,
                    )

            def _respond_request_error(self, path: str, status: int, code: str, message: str) -> None:
                if _is_api_path(path):
                    self._json_error(status, code, message)
                else:
                    self._respond_html(
                        render_error_page("请求无效", message, insecure_warning=dashboard.insecure_warning),
                        status=status,
                    )

            def _method_not_allowed(self, path: str, allow: str) -> None:
                headers = {"Allow": allow}
                if _is_api_path(path):
                    self._json_error(405, "method_not_allowed", "请求方法不受支持。", headers=headers)
                else:
                    self._respond_html(
                        render_error_page(
                            "方法不受支持",
                            "请使用页面支持的请求方式。",
                            insecure_warning=dashboard.insecure_warning,
                        ),
                        status=405,
                        headers=headers,
                    )

            def _json_error(
                self,
                status: int,
                code: str,
                message: str,
                *,
                headers: dict[str, str] | None = None,
            ) -> None:
                self._respond_json(
                    {"error": {"code": code, "message": message}},
                    status=status,
                    headers=headers,
                )

            def _redirect(self, location: str, *, set_cookie: str | None = None) -> None:
                self.send_response(303)
                self._common_headers()
                self.send_header("Location", location)
                if set_cookie:
                    self.send_header("Set-Cookie", set_cookie)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _respond_json(
                self,
                payload: dict,
                *,
                status: int = 200,
                headers: dict[str, str] | None = None,
            ) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                self.send_response(status)
                self._common_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                if headers:
                    for name, value in headers.items():
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _respond_html(
                self,
                page: str,
                *,
                status: int = 200,
                headers: dict[str, str] | None = None,
            ) -> None:
                body = page.encode("utf-8")
                self.send_response(status)
                self._common_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", _DASHBOARD_CSP)
                if headers:
                    for name, value in headers.items():
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _respond_report(self, page: str) -> None:
                body = page.encode("utf-8")
                self.send_response(200)
                self._common_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", _REPORT_CSP)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _common_headers(self) -> None:
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            self.httpd = ThreadingHTTPServer((self.host, self.requested_port), Handler)
            self.httpd.daemon_threads = True
            self.report_reader = report_reader
            self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
        except Exception:
            report_reader.close()
            self.httpd = None
            self.report_reader = None
            raise

    def stop(self) -> None:
        httpd = self.httpd
        thread = self.thread
        reader = self.report_reader
        self.httpd = None
        self.thread = None
        self.report_reader = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)
        if reader is not None:
            reader.close()


def _overview_payload(store, started_at: float) -> dict:
    tasks = _all_tasks(store)
    counts = {
        "blocked": 0,
        "completed": 0,
        "queued": 0,
        "retrying": 0,
        "running": 0,
        "unresolved_errors": _unresolved_error_count(store),
    }
    current_runs = []
    for task in tasks:
        status = str(task.get("status") or "unknown").lower()
        count_key = "queued" if status in {"queued", "claimed", "pending"} else status
        if count_key in counts and count_key != "unresolved_errors":
            counts[count_key] += 1
        if status not in {"completed", "cancelled"} and len(current_runs) < 20:
            current_runs.append(task)
    reports_page = store.list_reports(limit=20)
    events = _significant_events_page(store, limit=20)
    return {
        "counts": counts,
        "current_runs": current_runs,
        "events": events["items"],
        "recent_reports": [_report_metadata(item, include_identifier=True) for item in reports_page["items"]],
        "service": {
            "started_at": started_at,
            "status": "healthy",
            "uptime_seconds": max(0, int(time.time() - started_at)),
        },
    }


def _all_tasks(store) -> list[dict]:
    tasks: list[dict] = []
    cursor = None
    while True:
        page = store.list_tasks(cursor=cursor, limit=200)
        tasks.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return tasks


def _unresolved_error_count(store) -> int:
    count = 0
    cursor = None
    while True:
        page = store.list_errors(resolved=False, cursor=cursor, limit=200)
        count += len(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return count


def _significant_events_page(
    store,
    *,
    issue_identifier: str | None = None,
    category: str | None = None,
    event_type: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    items: list[dict] = []
    current_cursor = cursor
    next_cursor = None
    while len(items) < limit:
        page = store.list_events(
            issue_identifier=issue_identifier,
            category=category,
            event_type=event_type,
            cursor=current_cursor,
            limit=max(1, limit - len(items)),
        )
        for event in page["items"]:
            if _is_significant_event(event) and len(items) < limit:
                items.append(event)
        next_cursor = page.get("next_cursor")
        if not next_cursor or len(items) >= limit:
            break
        current_cursor = next_cursor
    return {"items": items, "next_cursor": next_cursor}


def _with_nearby_events(store, page: dict) -> dict:
    enriched = []
    for error in page["items"]:
        issue_identifier = error.get("issue_identifier")
        nearby = []
        if issue_identifier:
            nearby = _significant_events_page(
                store,
                issue_identifier=str(issue_identifier),
                limit=5,
            )["items"]
        enriched.append({**error, "nearby_events": nearby})
    return {**page, "items": enriched}


def _is_significant_event(event: dict) -> bool:
    event_type = str(event.get("type") or "").lower()
    return not any(marker in event_type for marker in _QUIET_EVENT_MARKERS)


def _report_metadata(entry: dict | None, *, include_identifier: bool = False) -> dict:
    if entry is None:
        return {"available": False}
    metadata = {
        "available": True,
        "created_at": entry.get("created_at"),
        "linear_sync_status": entry.get("linear_sync_status"),
        "report_version": entry.get("report_version"),
        "review_metadata": entry.get("review_metadata") or {},
        "summary": entry.get("summary"),
        "updated_at": entry.get("updated_at"),
        "url": entry.get("url"),
    }
    if include_identifier:
        metadata["issue_identifier"] = entry.get("issue_identifier")
    return metadata


def _task_filters(query: dict[str, list[str]]) -> dict:
    return {
        "cursor": _optional(_query_value(query, "cursor")),
        "limit": _limit(_query_value(query, "limit"), 50),
        "query": _optional(_query_value(query, "q")),
        "status": _optional(_query_value(query, "status")),
    }


def _event_filters(query: dict[str, list[str]]) -> dict:
    return {
        "category": _optional(_query_value(query, "category")),
        "cursor": _optional(_query_value(query, "cursor")),
        "event_type": _optional(_query_value(query, "type")),
        "limit": _limit(_query_value(query, "limit"), 50),
        "severity": _optional(_query_value(query, "severity")),
    }


def _error_filters(query: dict[str, list[str]], default_issue: str | None = None) -> dict:
    return {
        "cursor": _optional(_query_value(query, "cursor")),
        "error_type": _optional(_query_value(query, "type")),
        "issue": _optional(_query_value(query, "issue")) or default_issue,
        "limit": _limit(_query_value(query, "limit"), 50),
        "resolved": _resolved(_query_value(query, "resolved")),
        "stage": _optional(_query_value(query, "stage")),
    }


def _limit(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise _RequestInputError("invalid limit") from error
    if not 1 <= parsed <= 200:
        raise _RequestInputError("invalid limit")
    return parsed


def _resolved(value: str | None) -> bool | None:
    if value in (None, "", "all"):
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise _RequestInputError("invalid resolved filter")


def _query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    if len(values) != 1:
        raise _RequestInputError("duplicate query field")
    return values[0]


def _optional(value: str | None) -> str | None:
    return value if value else None


def _parse_issue_route(path: str) -> tuple[str, str | None] | None:
    parts = path.split("/")
    if len(parts) not in {3, 4} or parts[:2] != ["", "issues"]:
        return None
    identifier = _decode_identifier(parts[2])
    suffix = parts[3] if len(parts) == 4 else None
    return identifier, suffix


def _parse_api_issue_route(path: str) -> tuple[str, str | None] | None:
    parts = path.split("/")
    if len(parts) not in {4, 5} or parts[:3] != ["", "api", "issues"]:
        return None
    identifier = _decode_identifier(parts[3])
    resource = parts[4] if len(parts) == 5 else None
    return identifier, resource


def _decode_identifier(value: str) -> str:
    identifier = unquote(value, errors="strict")
    if _ISSUE_IDENTIFIER.fullmatch(identifier) is None:
        raise _RequestInputError("invalid issue identifier")
    return identifier


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_known_get_route(path: str) -> bool:
    if path in {"/", "/tasks", "/errors", "/healthz", "/login", "/api/overview", "/api/tasks", "/api/errors"}:
        return True
    try:
        return _parse_issue_route(path) is not None or _parse_api_issue_route(path) is not None
    except (_RequestInputError, UnicodeDecodeError):
        return False


def find_issue(snapshot: dict, issue_identifier: str) -> dict | None:
    """Retain the legacy RuntimeState snapshot lookup for existing callers."""
    for key in ("running", "completed", "blocked", "retrying"):
        for issue in snapshot.get(key, ()):
            if issue.get("issue_identifier") == issue_identifier:
                return issue
    return None


def render_dashboard_html() -> str:
    """Return the legacy unauthenticated preview without attaching it to a route."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Symphonz Runtime</title></head>
<body><div class="app-shell"><aside class="sidebar">Symphonz Runtime</aside>
<main><h1>Issue Queue</h1><h2>Activity Feed</h2><span class="status-chip">Claimed</span>
<table><thead><tr><th>Turn / Attempt</th></tr></thead></table>
<code>due_at cancellation_reason</code></main></div></body></html>"""
