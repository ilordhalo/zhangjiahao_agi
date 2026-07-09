from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import json
import time

from symphonz.service.models import RuntimeState


class DashboardServer:
    def __init__(self, host: str, port: int, state: RuntimeState):
        self.host = host
        self.requested_port = port
        self.state = state
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None

    @property
    def port(self) -> int:
        if self.httpd is None:
            return self.requested_port
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/":
                    self.respond_html(render_dashboard_html())
                elif self.path == "/api/state":
                    self.respond_json(state.snapshot())
                elif self.path.startswith("/api/issues/"):
                    issue_identifier = self.path.rsplit("/", 1)[-1]
                    snapshot = state.snapshot()
                    issue = find_issue(snapshot, issue_identifier)
                    self.respond_json(issue or {"error": "issue not found"})
                else:
                    self.send_error(404)

            def log_message(self, format: str, *args: object) -> None:
                return

            def respond_json(self, payload: dict) -> None:
                body = json.dumps(payload, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def respond_html(self, html: str) -> None:
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer((self.host, self.requested_port), Handler)
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


def find_issue(snapshot: dict, issue_identifier: str) -> dict | None:
    for key in ["running", "completed", "blocked", "retrying"]:
        for issue in snapshot.get(key, []):
            if issue.get("issue_identifier") == issue_identifier:
                return issue
    return None


def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Symphonz Runtime</title>
  <style>
    :root { color-scheme: light; --bg: #f7f8fa; --panel: #ffffff; --line: #d8dde6; --text: #172033; --muted: #657085; --accent: #0f766e; --warn: #b45309; --bad: #b42318; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    header { padding: 22px 28px; border-bottom: 1px solid var(--line); background: var(--panel); display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    h1 { margin: 0; font-size: 22px; letter-spacing: 0; }
    main { padding: 24px 28px; display: grid; gap: 18px; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
    .metric, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    .label { color: var(--muted); font-size: 12px; text-transform: uppercase; }
    .value { font-size: 30px; font-weight: 700; margin-top: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 10px 8px; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    .pill { display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; background: #f9fafb; }
    .muted { color: var(--muted); }
    .toolbar { display: flex; gap: 8px; align-items: center; }
    button { border: 1px solid var(--line); background: var(--panel); border-radius: 6px; padding: 8px 10px; cursor: pointer; }
    button:hover { border-color: var(--accent); }
    pre { overflow: auto; background: #111827; color: #f9fafb; border-radius: 8px; padding: 14px; }
  </style>
</head>
<body>
  <header>
    <div><h1>Symphonz Runtime</h1><div class="muted">Linear orchestration, Codex sessions, workspace activity</div></div>
    <div class="toolbar"><span class="pill">Live</span><button onclick="loadState()">Refresh</button></div>
  </header>
  <main>
    <section class="metrics" id="metrics"></section>
    <section class="panel"><h2>Issues</h2><div id="issues"></div></section>
    <section class="panel"><h2>Recent Events</h2><pre id="events">Loading...</pre></section>
  </main>
  <script>
    async function loadState() {
      const state = await fetch('/api/state').then(r => r.json());
      const counts = state.counts;
      document.getElementById('metrics').innerHTML = ['running','completed','blocked','retrying'].map(k => `<div class="metric"><div class="label">${k}</div><div class="value">${counts[k]}</div></div>`).join('');
      const issues = [...state.running, ...state.blocked, ...state.retrying, ...state.completed];
      document.getElementById('issues').innerHTML = issues.length ? `<table><thead><tr><th>Issue</th><th>Status</th><th>Workspace</th><th>Session</th></tr></thead><tbody>${issues.map(i => `<tr><td>${i.issue_identifier || ''}<br><span class="muted">${i.title || ''}</span></td><td>${i.status || i.state || ''}</td><td>${i.workspace || ''}</td><td>${i.session_id || ''}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">No issue activity yet.</p>';
      document.getElementById('events').textContent = JSON.stringify(state.events.slice(-20), null, 2);
    }
    loadState();
    setInterval(loadState, 3000);
  </script>
</body>
</html>"""

