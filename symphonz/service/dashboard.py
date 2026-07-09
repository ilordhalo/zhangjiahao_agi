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
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --surface: #ffffff;
      --surface-subtle: #fafbfc;
      --line: #e3e6eb;
      --line-strong: #d7dbe3;
      --text: #171a1f;
      --muted: #6b7280;
      --faint: #9aa3af;
      --accent: #5e6ad2;
      --accent-soft: #eef0ff;
      --green: #16825d;
      --green-soft: #eaf7f1;
      --amber: #a46313;
      --amber-soft: #fff4df;
      --red: #c23b32;
      --red-soft: #fff0ef;
      --blue: #2563eb;
      --blue-soft: #edf4ff;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    h1, h2, p { margin: 0; }
    button {
      border: 1px solid var(--line-strong);
      background: var(--surface);
      border-radius: 6px;
      color: var(--text);
      cursor: pointer;
      font: inherit;
      height: 32px;
      padding: 0 10px;
    }
    button:hover { border-color: var(--accent); color: var(--accent); }
    .app-shell {
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      background: #fbfbfd;
      border-right: 1px solid var(--line);
      padding: 18px 14px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .brand {
      align-items: center;
      display: flex;
      gap: 10px;
      padding: 2px 6px 10px;
    }
    .brand-mark {
      align-items: center;
      background: var(--text);
      border-radius: 7px;
      color: #ffffff;
      display: inline-flex;
      font-size: 13px;
      font-weight: 700;
      height: 28px;
      justify-content: center;
      width: 28px;
    }
    .brand-title { font-size: 15px; font-weight: 650; }
    .muted { color: var(--muted); }
    .nav-group { display: grid; gap: 4px; }
    .nav-item {
      align-items: center;
      border-radius: 6px;
      color: var(--muted);
      display: flex;
      font-size: 13px;
      gap: 8px;
      padding: 8px 10px;
    }
    .nav-item.active {
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 600;
    }
    .nav-dot {
      border: 1px solid currentColor;
      border-radius: 999px;
      height: 8px;
      width: 8px;
    }
    .sidebar-footer {
      border-top: 1px solid var(--line);
      margin-top: auto;
      padding: 14px 8px 2px;
    }
    .content {
      align-content: start;
      display: grid;
      gap: 18px;
      min-width: 0;
      padding: 22px 28px 30px;
    }
    .topbar {
      align-items: center;
      display: flex;
      gap: 16px;
      justify-content: space-between;
    }
    .eyebrow {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    h1 { font-size: 24px; font-weight: 680; letter-spacing: 0; line-height: 1.2; }
    .toolbar { align-items: center; display: flex; gap: 8px; }
    .live-chip {
      align-items: center;
      background: var(--green-soft);
      border: 1px solid #bfe7d5;
      border-radius: 999px;
      color: var(--green);
      display: inline-flex;
      font-size: 12px;
      font-weight: 650;
      gap: 7px;
      height: 30px;
      padding: 0 10px;
    }
    .pulse {
      background: currentColor;
      border-radius: 999px;
      height: 7px;
      width: 7px;
    }
    .metrics {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(4, minmax(130px, 1fr));
    }
    .metric {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-height: 92px;
      padding: 14px;
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      text-transform: capitalize;
    }
    .metric-value {
      font-size: 30px;
      font-weight: 720;
      letter-spacing: 0;
      line-height: 1;
      margin-top: 14px;
    }
    .layout-grid {
      align-items: start;
      display: grid;
      gap: 14px;
      grid-template-columns: minmax(0, 1fr) 360px;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
      overflow: hidden;
    }
    .panel-header {
      align-items: center;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      min-height: 52px;
      padding: 0 16px;
    }
    .panel-title { font-size: 15px; font-weight: 680; }
    .panel-meta { color: var(--muted); font-size: 12px; }
    .table-wrap { overflow-x: auto; }
    table {
      border-collapse: collapse;
      font-size: 13px;
      min-width: 760px;
      width: 100%;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      text-align: left;
      vertical-align: middle;
    }
    th {
      background: var(--surface-subtle);
      color: var(--muted);
      font-size: 11px;
      font-weight: 650;
      text-transform: uppercase;
    }
    tr:hover td { background: #fbfcff; }
    .issue-key { font-weight: 680; }
    .issue-title {
      color: var(--muted);
      display: block;
      margin-top: 3px;
      max-width: 440px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .path, .session {
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      white-space: nowrap;
    }
    .status-chip {
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      display: inline-flex;
      font-size: 12px;
      font-weight: 650;
      height: 24px;
      padding: 0 9px;
      text-transform: capitalize;
      white-space: nowrap;
    }
    .status-running { background: var(--blue-soft); border-color: #c8dcff; color: var(--blue); }
    .status-completed { background: var(--green-soft); border-color: #bfe7d5; color: var(--green); }
    .status-blocked { background: var(--red-soft); border-color: #ffd0cc; color: var(--red); }
    .status-retrying { background: var(--amber-soft); border-color: #f4d7a3; color: var(--amber); }
    .activity-list {
      display: grid;
      max-height: 620px;
      overflow: auto;
    }
    .event-row {
      border-bottom: 1px solid var(--line);
      display: grid;
      gap: 5px;
      padding: 13px 16px;
    }
    .event-top {
      align-items: center;
      display: flex;
      gap: 8px;
      justify-content: space-between;
    }
    .event-type { font-size: 13px; font-weight: 650; }
    .event-time { color: var(--faint); font-size: 11px; white-space: nowrap; }
    .event-message { color: var(--muted); font-size: 12px; line-height: 1.45; }
    .empty {
      color: var(--muted);
      font-size: 13px;
      padding: 22px 16px;
    }
    @media (max-width: 980px) {
      .app-shell { grid-template-columns: 1fr; }
      .sidebar { display: none; }
      .content { padding: 18px; }
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .layout-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 620px) {
      .topbar { align-items: flex-start; flex-direction: column; }
      .metrics { grid-template-columns: 1fr; }
      .toolbar { width: 100%; }
      button { flex: 1; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">S</span>
        <div>
          <div class="brand-title">Symphonz</div>
          <div class="muted">Runtime control</div>
        </div>
      </div>
      <nav class="nav-group" aria-label="Runtime navigation">
        <div class="nav-item active"><span class="nav-dot"></span>Overview</div>
        <div class="nav-item"><span class="nav-dot"></span>Issue Queue</div>
        <div class="nav-item"><span class="nav-dot"></span>Activity Feed</div>
        <div class="nav-item"><span class="nav-dot"></span>Workspaces</div>
      </nav>
      <div class="sidebar-footer">
        <div class="eyebrow">Tracker</div>
        <div class="brand-title">Linear</div>
      </div>
    </aside>
    <main class="content">
      <header class="topbar">
        <div>
          <div class="eyebrow">Orchestration</div>
          <h1>Symphonz Runtime</h1>
          <p class="muted">Linear orchestration, Codex sessions, workspace activity</p>
        </div>
        <div class="toolbar">
          <span class="live-chip"><span class="pulse"></span>Live</span>
          <button onclick="loadState()">Refresh</button>
        </div>
      </header>
      <section class="metrics" id="metrics" aria-label="Runtime metrics"></section>
      <section class="layout-grid">
        <div class="panel">
          <div class="panel-header">
            <h2 class="panel-title">Issue Queue</h2>
            <span class="panel-meta" id="issue-count">0 issues</span>
          </div>
          <div class="table-wrap" id="issues"></div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2 class="panel-title">Activity Feed</h2>
            <span class="panel-meta">Latest 20</span>
          </div>
          <div class="activity-list" id="events"></div>
        </div>
      </section>
    </main>
  </div>
  <script>
    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }
    function statusClass(value) {
      const normalized = String(value || 'running').toLowerCase();
      if (normalized.includes('complete')) return 'completed';
      if (normalized.includes('block') || normalized.includes('fail')) return 'blocked';
      if (normalized.includes('retry')) return 'retrying';
      return 'running';
    }
    function eventTime(timestamp) {
      if (!timestamp) return '';
      return new Date(timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    async function loadState() {
      const state = await fetch('/api/state').then(r => r.json());
      const counts = state.counts;
      const metricLabels = {
        running: 'Running',
        completed: 'Completed',
        blocked: 'Blocked',
        retrying: 'Retrying'
      };
      document.getElementById('metrics').innerHTML = ['running','completed','blocked','retrying'].map(k => `
        <div class="metric">
          <div class="metric-label">${metricLabels[k]}</div>
          <div class="metric-value">${counts[k] || 0}</div>
        </div>
      `).join('');
      const issues = [...state.running, ...state.blocked, ...state.retrying, ...state.completed];
      document.getElementById('issue-count').textContent = `${issues.length} ${issues.length === 1 ? 'issue' : 'issues'}`;
      document.getElementById('issues').innerHTML = issues.length ? `
        <table>
          <thead><tr><th>Issue</th><th>Status</th><th>Workspace</th><th>Session</th></tr></thead>
          <tbody>
            ${issues.map(i => {
              const status = statusClass(i.status || i.state);
              return `<tr>
                <td><span class="issue-key">${escapeHtml(i.issue_identifier)}</span><span class="issue-title">${escapeHtml(i.title)}</span></td>
                <td><span class="status-chip status-${status}">${escapeHtml(i.status || i.state || 'running')}</span></td>
                <td><span class="path">${escapeHtml(i.workspace)}</span></td>
                <td><span class="session">${escapeHtml(i.session_id)}</span></td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      ` : '<p class="empty">No issue activity yet.</p>';
      const events = state.events.slice(-20).reverse();
      document.getElementById('events').innerHTML = events.length ? events.map(event => `
        <div class="event-row">
          <div class="event-top">
            <span class="event-type">${escapeHtml(event.type)}</span>
            <span class="event-time">${escapeHtml(eventTime(event.timestamp))}</span>
          </div>
          <div class="event-message">${escapeHtml(event.message)}${event.issue_identifier ? ` · ${escapeHtml(event.issue_identifier)}` : ''}</div>
        </div>
      `).join('') : '<p class="empty">No events recorded yet.</p>';
    }
    loadState();
    setInterval(loadState, 3000);
  </script>
</body>
</html>"""
