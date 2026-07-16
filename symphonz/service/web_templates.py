from __future__ import annotations

from datetime import datetime
import html
import json
from urllib.parse import quote, urlencode, urlsplit


_STATUS_LABELS = {
    "queued": "排队中",
    "claimed": "已领取",
    "pending": "待处理",
    "running": "运行中",
    "blocked": "已阻塞",
    "retrying": "重试中",
    "completed": "已完成",
    "cancelled": "已取消",
    "unknown": "未知",
}

_CSS = r"""
:root {
  color-scheme: light;
  --bg: #f7f7f8;
  --surface: #ffffff;
  --surface-raised: #fbfbfc;
  --line: #e3e3e7;
  --line-strong: #d3d4da;
  --text: #202124;
  --muted: #696b73;
  --accent: #5e6ad2;
  --accent-soft: #eef0ff;
  --green: #18794e;
  --green-soft: #eaf8f0;
  --amber: #946200;
  --amber-soft: #fff7db;
  --red: #c43232;
  --red-soft: #fff0f0;
  --blue: #2366c4;
  --blue-soft: #edf5ff;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.45; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
button, input, select { font: inherit; }
button, .button {
  align-items: center; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 6px;
  color: var(--text); cursor: pointer; display: inline-flex; height: 34px; justify-content: center; padding: 0 11px;
}
button:hover, .button:hover { border-color: #aeb1bc; text-decoration: none; }
:focus-visible { outline: 3px solid rgba(94, 106, 210, .32); outline-offset: 2px; }
.skip-link { background: var(--text); color: #fff; left: 10px; padding: 8px 10px; position: fixed; top: -60px; z-index: 20; }
.skip-link:focus { top: 10px; }
.shell { display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 100vh; }
.sidebar { background: #f1f1f3; border-right: 1px solid var(--line); display: flex; flex-direction: column; padding: 18px 12px; }
.brand { align-items: center; color: var(--text); display: flex; gap: 10px; margin: 0 6px 22px; }
.brand:hover { text-decoration: none; }
.brand-mark { align-items: center; background: #27282c; border-radius: 6px; color: #fff; display: inline-flex; font-weight: 750; height: 28px; justify-content: center; width: 28px; }
.brand-copy strong { display: block; font-size: 14px; }
.brand-copy span { color: var(--muted); display: block; font-size: 11px; }
.nav { display: grid; gap: 3px; }
.nav a { border-radius: 6px; color: #4d4f56; display: flex; gap: 9px; padding: 8px 10px; }
.nav a:hover { background: rgba(255,255,255,.64); text-decoration: none; }
.nav a.active { background: #fff; color: var(--text); font-weight: 650; }
.nav-symbol { color: var(--muted); font-family: ui-monospace, monospace; width: 15px; }
.sidebar-footer { border-top: 1px solid var(--line); margin-top: auto; padding: 14px 6px 0; }
.logout { width: 100%; }
.main { min-width: 0; }
.warning { background: var(--amber-soft); border-bottom: 1px solid #ead38c; color: #725000; font-size: 12px; padding: 8px 28px; }
.content { margin: 0 auto; max-width: 1440px; padding: 24px 28px 44px; }
.page-header { align-items: flex-start; display: flex; gap: 18px; justify-content: space-between; margin-bottom: 20px; }
.eyebrow { color: var(--muted); font-size: 11px; font-weight: 700; margin: 0 0 4px; text-transform: uppercase; }
h1, h2, h3, p { margin-top: 0; }
h1 { font-size: 23px; letter-spacing: 0; line-height: 1.2; margin-bottom: 6px; }
h2 { font-size: 15px; letter-spacing: 0; }
h3 { font-size: 13px; letter-spacing: 0; }
.subtle { color: var(--muted); }
.toolbar { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
.health { align-items: center; color: var(--green); display: inline-flex; font-size: 12px; font-weight: 650; gap: 7px; min-height: 34px; }
.health-dot { background: currentColor; border-radius: 50%; height: 7px; width: 7px; }
.metrics { display: grid; gap: 8px; grid-template-columns: repeat(6, minmax(112px, 1fr)); margin-bottom: 18px; }
.metric { background: var(--surface); border: 1px solid var(--line); border-radius: 7px; min-height: 86px; padding: 13px; }
.metric-label { color: var(--muted); font-size: 11px; font-weight: 650; }
.metric-value { font-size: 27px; font-weight: 720; line-height: 1; margin-top: 15px; }
.grid-two { align-items: start; display: grid; gap: 14px; grid-template-columns: minmax(0, 1.45fr) minmax(300px, .75fr); }
.section { margin-top: 18px; }
.section-head { align-items: center; display: flex; justify-content: space-between; margin-bottom: 9px; }
.section-head h2 { margin: 0; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; min-width: 800px; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 11px 13px; text-align: left; vertical-align: top; }
th { background: var(--surface-raised); color: var(--muted); font-size: 10px; font-weight: 700; text-transform: uppercase; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: #fcfcfd; }
.issue { color: var(--text); font-weight: 700; }
.title { color: var(--muted); display: block; margin-top: 2px; max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }
.status { border: 1px solid var(--line); border-radius: 999px; display: inline-flex; font-size: 11px; font-weight: 650; padding: 3px 8px; white-space: nowrap; }
.status-running { background: var(--blue-soft); border-color: #c9dcf7; color: var(--blue); }
.status-completed { background: var(--green-soft); border-color: #bfe4ce; color: var(--green); }
.status-blocked { background: var(--red-soft); border-color: #f2caca; color: var(--red); }
.status-retrying, .status-queued, .status-claimed, .status-pending { background: var(--amber-soft); border-color: #ead38c; color: var(--amber); }
.list { display: grid; }
.list-row { border-bottom: 1px solid var(--line); display: grid; gap: 5px; padding: 12px 14px; }
.list-row:last-child { border-bottom: 0; }
.row-top { align-items: baseline; display: flex; gap: 12px; justify-content: space-between; }
.row-title { font-weight: 650; }
.row-meta { color: var(--muted); font-size: 11px; white-space: nowrap; }
.row-message { color: #565860; font-size: 12px; overflow-wrap: anywhere; }
.empty { color: var(--muted); padding: 24px 14px; }
.filters { align-items: end; display: flex; flex-wrap: wrap; gap: 9px; margin-bottom: 12px; }
.field { display: grid; gap: 5px; min-width: 150px; }
.field-grow { flex: 1; min-width: 220px; }
.field label { color: var(--muted); font-size: 11px; font-weight: 650; }
input, select { background: var(--surface); border: 1px solid var(--line-strong); border-radius: 6px; color: var(--text); height: 36px; min-width: 0; padding: 0 10px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
.tabs { border-bottom: 1px solid var(--line); display: flex; gap: 20px; margin-bottom: 18px; overflow-x: auto; }
.tabs a { border-bottom: 2px solid transparent; color: var(--muted); padding: 9px 2px; white-space: nowrap; }
.tabs a:hover { color: var(--text); text-decoration: none; }
.tabs a.active { border-color: var(--accent); color: var(--text); font-weight: 650; }
.detail-grid { display: grid; gap: 1px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.detail-item { background: var(--surface); border: 1px solid var(--line); margin: -1px 0 0 -1px; min-height: 76px; padding: 12px; }
.detail-label { color: var(--muted); display: block; font-size: 10px; font-weight: 700; margin-bottom: 6px; text-transform: uppercase; }
.error-row { border-left: 3px solid var(--red); }
details summary { cursor: pointer; font-weight: 650; }
pre { background: #f4f4f6; border: 1px solid var(--line); border-radius: 5px; font-size: 11px; overflow: auto; padding: 10px; white-space: pre-wrap; }
.login-body { align-items: center; display: grid; min-height: 100vh; padding: 24px; }
.login-wrap { margin: 0 auto; max-width: 380px; width: 100%; }
.login-brand { margin-bottom: 22px; }
.login-panel { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 22px; }
.login-panel h1 { font-size: 20px; }
.login-panel .field { margin-top: 13px; }
.login-panel button { margin-top: 17px; width: 100%; }
.form-error { background: var(--red-soft); border: 1px solid #f2caca; border-radius: 6px; color: #972727; margin-top: 12px; padding: 9px 10px; }
.login-warning { border: 1px solid #ead38c; border-radius: 6px; margin-bottom: 12px; }
@media (max-width: 1060px) {
  .metrics { grid-template-columns: repeat(3, minmax(112px, 1fr)); }
  .grid-two { grid-template-columns: 1fr; }
  .detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { border-bottom: 1px solid var(--line); border-right: 0; padding: 11px 14px; }
  .brand { margin: 0 0 10px; }
  .brand-copy span, .sidebar-footer { display: none; }
  .nav { display: flex; overflow-x: auto; }
  .nav a { white-space: nowrap; }
  .warning { padding: 8px 16px; }
  .content { padding: 18px 16px 34px; }
  .page-header { align-items: stretch; flex-direction: column; }
  .metrics { grid-template-columns: repeat(2, minmax(112px, 1fr)); }
  .detail-grid { grid-template-columns: 1fr; }
  .field, .field-grow { min-width: 100%; width: 100%; }
  .filters button { width: 100%; }
}
"""

_JS = r"""
(function () {
  'use strict';
  document.querySelectorAll('[data-copy-link]').forEach(function (button) {
    button.addEventListener('click', function () {
      navigator.clipboard.writeText(button.dataset.copyLink || window.location.href).then(function () {
        var previous = button.textContent;
        button.textContent = '已复制';
        window.setTimeout(function () { button.textContent = previous; }, 1400);
      });
    });
  });
  var overview = document.querySelector('[data-overview]');
  if (!overview) return;
  window.setInterval(function () {
    fetch('/api/overview', { headers: { 'Accept': 'application/json' } }).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (payload) {
      if (!payload) return;
      Object.keys(payload.counts).forEach(function (key) {
        var target = document.querySelector('[data-metric="' + key + '"]');
        if (target) target.textContent = String(payload.counts[key]);
      });
      var uptime = document.querySelector('[data-uptime]');
      if (uptime) uptime.textContent = String(payload.service.uptime_seconds) + ' 秒';
    }).catch(function () {});
  }, 5000);
}());
"""


def render_login_page(next_path: str | None, *, error: str | None = None, insecure_warning: bool = False) -> str:
    safe_next = html.escape(next_path or "", quote=True)
    error_html = f'<div class="form-error" role="alert">{_escape(error)}</div>' if error else ""
    warning = (
        '<div class="warning login-warning" role="status">未加密连接：请仅在受信任的局域网中登录。</div>'
        if insecure_warning else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 · Symphonz</title><style>{_CSS}</style></head>
<body class="login-body"><main class="login-wrap">
  <a class="brand login-brand" href="/login"><span class="brand-mark">S</span><span class="brand-copy"><strong>Symphonz</strong><span>任务运行中心</span></span></a>
  {warning}
  <section class="login-panel" aria-labelledby="login-title">
    <h1 id="login-title">登录运行中心</h1><p class="subtle">使用已配置的局域网账户继续。</p>{error_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{safe_next}">
      <div class="field"><label for="username">用户名</label><input id="username" name="username" autocomplete="username" required maxlength="256"></div>
      <div class="field"><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="4096"></div>
      <button type="submit">登录</button>
    </form>
  </section>
</main></body></html>"""


def render_overview_page(data: dict, *, insecure_warning: bool = False) -> str:
    counts = data["counts"]
    metric_labels = (
        ("running", "运行中"), ("queued", "排队中"), ("blocked", "已阻塞"),
        ("retrying", "重试中"), ("completed", "已完成"), ("unresolved_errors", "未解决错误"),
    )
    metrics = "".join(
        f'<div class="metric"><div class="metric-label">{label}</div><div class="metric-value" data-metric="{key}">{int(counts.get(key, 0))}</div></div>'
        for key, label in metric_labels
    )
    current_rows = _task_table(data.get("current_runs", ()), compact=True)
    report_rows = "".join(_report_row(report) for report in data.get("recent_reports", ()))
    event_rows = "".join(_event_row(event) for event in data.get("events", ()))
    body = f"""
<div data-overview>
  <header class="page-header"><div><p class="eyebrow">Operations</p><h1>运行概览</h1><p class="subtle">服务状态、当前任务与关键运行事件</p></div>
    <div class="toolbar"><span class="health"><span class="health-dot"></span>服务正常 · <span data-uptime>{int(data['service']['uptime_seconds'])} 秒</span></span><button type="button" onclick="window.location.reload()">刷新</button></div>
  </header>
  <section class="metrics" aria-label="运行指标">{metrics}</section>
  <div class="grid-two">
    <section><div class="section-head"><h2>当前运行</h2><a href="/tasks">查看全部</a></div><div class="panel">{current_rows}</div></section>
    <section><div class="section-head"><h2>最近报告</h2></div><div class="panel list">{report_rows or _empty('暂无已发布报告')}</div></section>
  </div>
  <section class="section"><div class="section-head"><h2>关键事件</h2></div><div class="panel list">{event_rows or _empty('暂无运行事件')}</div></section>
</div>"""
    return _shell("运行概览", "overview", body, insecure_warning)


def render_tasks_page(page: dict, filters: dict, *, insecure_warning: bool = False) -> str:
    query = _escape(filters.get("query") or "")
    status = filters.get("status") or ""
    options = [('','全部状态'), ('queued','排队中'), ('running','运行中'), ('blocked','已阻塞'), ('retrying','重试中'), ('completed','已完成')]
    status_options = "".join(
        f'<option value="{value}"{" selected" if value == status else ""}>{label}</option>' for value, label in options
    )
    next_link = ""
    if page.get("next_cursor"):
        values = {"cursor": page["next_cursor"], "limit": filters.get("limit", 50)}
        if filters.get("query"): values["q"] = filters["query"]
        if filters.get("status"): values["status"] = filters["status"]
        next_link = f'<a class="button" href="/tasks?{html.escape(urlencode(values), quote=True)}">下一页</a>'
    body = f"""
<header class="page-header"><div><p class="eyebrow">Tasks</p><h1>任务列表</h1><p class="subtle">持久化的任务状态、评审与报告记录</p></div></header>
<form class="filters" method="get" action="/tasks">
  <div class="field field-grow"><label for="task-query">搜索</label><input id="task-query" name="q" value="{query}" placeholder="任务编号或标题"></div>
  <div class="field"><label for="task-status">运行状态</label><select id="task-status" name="status">{status_options}</select></div>
  <button type="submit">筛选</button>
</form>
<section class="panel">{_task_table(page.get('items', ()))}</section><div class="pagination">{next_link}</div>"""
    return _shell("任务列表", "tasks", body, insecure_warning)


def render_issue_page(
    task: dict,
    report: dict,
    events: dict,
    errors: dict,
    *,
    selected_tab: str = "overview",
    filters: dict | None = None,
    insecure_warning: bool = False,
) -> str:
    filters = filters or {}
    identifier = str(task.get("issue_identifier") or "")
    encoded_identifier = quote(identifier, safe="")
    tabs = (("overview", "概览"), ("timeline", "时间线"), ("report", "报告"), ("errors", "错误"))
    tab_html = "".join(
        f'<a class="{"active" if key == selected_tab else ""}" href="/issues/{encoded_identifier}?tab={key}">{label}</a>'
        for key, label in tabs
    )
    header = f"""
<header class="page-header"><div><p class="eyebrow">{_escape(identifier)}</p><h1>{_escape(task.get('title') or '未命名任务')}</h1>
  <p class="subtle">{_escape(task.get('linear_state') or 'Linear 状态未知')} · {_status_badge(task.get('status'))}</p></div>
  <div class="toolbar"><button type="button" data-copy-link="">复制链接</button>{_external_link(task.get('review_url'), '打开评审')}</div></header>
<nav class="tabs" aria-label="任务详情">{tab_html}</nav>"""
    if selected_tab == "timeline":
        content = _timeline_tab(identifier, events, filters)
    elif selected_tab == "report":
        content = _report_tab(identifier, report)
    elif selected_tab == "errors":
        content = _errors_list(errors.get("items", ()))
    else:
        content = _overview_tab(task, report)
    return _shell(f"{identifier} 任务详情", "tasks", header + content, insecure_warning)


def render_errors_page(page: dict, filters: dict, *, insecure_warning: bool = False) -> str:
    resolved = filters.get("resolved")
    resolved_value = "all" if resolved is None else str(resolved).lower()
    options = "".join(
        f'<option value="{value}"{" selected" if value == resolved_value else ""}>{label}</option>'
        for value, label in (("false", "未解决"), ("true", "已解决"), ("all", "全部"))
    )
    body = f"""
<header class="page-header"><div><p class="eyebrow">Errors</p><h1>错误中心</h1><p class="subtle">优先处理未解决错误并查看已脱敏上下文</p></div></header>
<form class="filters" method="get" action="/errors">
  <div class="field"><label for="error-issue">任务</label><input id="error-issue" name="issue" value="{_escape(filters.get('issue') or '')}" placeholder="SYM-1"></div>
  <div class="field"><label for="error-stage">阶段</label><input id="error-stage" name="stage" value="{_escape(filters.get('stage') or '')}" placeholder="codex"></div>
  <div class="field"><label for="error-type">类型</label><input id="error-type" name="type" value="{_escape(filters.get('error_type') or '')}" placeholder="TurnTimeout"></div>
  <div class="field"><label for="error-resolved">处理状态</label><select id="error-resolved" name="resolved">{options}</select></div>
  <button type="submit">筛选</button>
</form>
<section class="panel list">{_errors_list(page.get('items', ()))}</section>"""
    return _shell("错误中心", "errors", body, insecure_warning)


def render_error_page(title: str, message: str, *, insecure_warning: bool = False) -> str:
    body = f"""<header class="page-header"><div><p class="eyebrow">Request</p><h1>{_escape(title)}</h1><p class="subtle">{_escape(message)}</p></div></header><a class="button" href="/">返回运行概览</a>"""
    return _shell(title, "", body, insecure_warning)


def _shell(title: str, active: str, body: str, insecure_warning: bool) -> str:
    warning = '<div class="warning" role="status">未加密连接：当前会话仅适用于受信任的局域网。</div>' if insecure_warning else ""
    nav = (
        ("overview", "/", "01", "概览"),
        ("tasks", "/tasks", "02", "任务"),
        ("errors", "/errors", "03", "错误"),
    )
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}"><span class="nav-symbol">{symbol}</span>{label}</a>'
        for key, href, symbol, label in nav
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)} · Symphonz</title><style>{_CSS}</style></head><body>
<a class="skip-link" href="#main">跳到主要内容</a><div class="shell">
<aside class="sidebar"><a class="brand" href="/"><span class="brand-mark">S</span><span class="brand-copy"><strong>Symphonz</strong><span>任务运行中心</span></span></a>
<nav class="nav" aria-label="主要导航">{links}</nav><div class="sidebar-footer"><form method="post" action="/logout"><button class="logout" type="submit">退出登录</button></form></div></aside>
<div class="main">{warning}<main class="content" id="main">{body}</main></div></div><script>{_JS}</script></body></html>"""


def _task_table(tasks, *, compact: bool = False) -> str:
    tasks = list(tasks)
    if not tasks:
        return _empty("暂无任务记录")
    rows = "".join(_task_row(task, compact=compact) for task in tasks)
    extra = "" if compact else "<th>Linear</th><th>尝试</th><th>评审 / 报告</th><th>错误</th>"
    return f'<div class="table-wrap"><table><thead><tr><th>任务</th><th>状态</th>{extra}<th>最近活动</th></tr></thead><tbody>{rows}</tbody></table></div>'


def _task_row(task: dict, *, compact: bool) -> str:
    identifier = str(task.get("issue_identifier") or "")
    issue = f'<a class="issue" href="/issues/{quote(identifier, safe="")}">{_escape(identifier)}</a><span class="title">{_escape(task.get("title") or "未命名任务")}</span>'
    status = _status_badge(task.get("status"))
    updated = _format_time(task.get("updated_at"))
    if compact:
        return f"<tr><td>{issue}</td><td>{status}</td><td>{updated}</td></tr>"
    review = _external_link(task.get("review_url"), "评审")
    report = f'<a href="/issues/{quote(identifier, safe="")}/report">报告</a>' if task.get("report_url") else '<span class="subtle">暂无</span>'
    return (
        f"<tr><td>{issue}</td><td>{status}</td><td>{_escape(task.get('linear_state') or '—')}</td>"
        f"<td>{_escape(task.get('attempt') if task.get('attempt') is not None else '—')}</td><td>{review} · {report}</td>"
        f"<td>{int(task.get('error_count') or 0)}</td><td>{updated}</td></tr>"
    )


def _report_row(report: dict) -> str:
    identifier = str(report.get("issue_identifier") or "")
    return f"""<div class="list-row"><div class="row-top"><a class="row-title" href="/issues/{quote(identifier, safe='')}/report">{_escape(identifier)}</a><span class="row-meta">{_format_time(report.get('updated_at'))}</span></div><div class="row-message">{_escape(report.get('summary') or '实施报告已发布')}</div></div>"""


def _event_row(event: dict) -> str:
    identifier = event.get("issue_identifier")
    issue = f" · {_escape(identifier)}" if identifier else ""
    return f"""<div class="list-row"><div class="row-top"><span class="row-title">{_escape(event.get('type') or 'event')}{issue}</span><time class="row-meta">{_format_time(event.get('timestamp'))}</time></div><div class="row-message">{_escape(event.get('message') or '')}</div></div>"""


def _overview_tab(task: dict, report: dict) -> str:
    values = (
        ("运行状态", _status_badge(task.get("status"))),
        ("Linear 状态", _escape(task.get("linear_state") or "—")),
        ("尝试次数", _escape(task.get("attempt") if task.get("attempt") is not None else "—")),
        ("分支", f'<span class="mono">{_escape(task.get("branch") or "—")}</span>'),
        ("提交", f'<span class="mono">{_escape(task.get("commit") or "—")}</span>'),
        ("Codex 会话", f'<span class="mono">{_escape(task.get("codex_session_id") or "—")}</span>'),
        ("工作区", f'<span class="mono">{_escape(task.get("workspace") or "—")}</span>'),
        ("评审请求", _external_link(task.get("review_url"), "打开评审")),
        ("实施报告", f'<a href="/issues/{quote(str(task.get("issue_identifier") or ""), safe="")}/report">打开报告</a>' if report.get("available") else '<span class="subtle">尚未发布</span>'),
    )
    items = "".join(f'<div class="detail-item"><span class="detail-label">{label}</span>{value}</div>' for label, value in values)
    return f'<section><h2>执行信息</h2><div class="detail-grid">{items}</div></section>'


def _timeline_tab(identifier: str, events: dict, filters: dict) -> str:
    category = filters.get("category") or ""
    options = (("", "全部类别"), ("lifecycle", "生命周期"), ("codex", "Codex"), ("git", "Git"), ("linear", "Linear"), ("report", "报告"), ("error", "错误"))
    choices = "".join(f'<option value="{value}"{" selected" if value == category else ""}>{label}</option>' for value, label in options)
    rows = "".join(_event_row(event) for event in events.get("items", ()))
    next_link = ""
    if events.get("next_cursor"):
        values = {
            "cursor": events["next_cursor"],
            "limit": filters.get("limit", 50),
            "tab": "timeline",
        }
        if category:
            values["category"] = category
        if filters.get("event_type"):
            values["type"] = filters["event_type"]
        next_link = f'<div class="pagination"><a class="button" href="/issues/{quote(identifier, safe="")}?{html.escape(urlencode(values), quote=True)}">下一页</a></div>'
    return f"""<form class="filters" method="get" action="/issues/{quote(identifier, safe='')}"><input type="hidden" name="tab" value="timeline"><div class="field"><label for="timeline-category">事件类别</label><select id="timeline-category" name="category">{choices}</select></div><div class="field field-grow"><label for="timeline-type">事件类型</label><input id="timeline-type" name="type" value="{_escape(filters.get('event_type') or '')}" placeholder="例如 report_published"></div><button type="submit">筛选</button></form><section class="panel list">{rows or _empty('暂无匹配事件')}</section>{next_link}"""


def _report_tab(identifier: str, report: dict) -> str:
    if not report.get("available"):
        return '<section class="panel"><p class="empty">该任务尚未发布实施报告。</p></section>'
    return f"""<section><h2>实施报告</h2><p class="subtle">当前链接始终指向 RuntimeStore 记录的权威报告版本。</p><div class="toolbar"><a class="button" href="/issues/{quote(identifier, safe='')}/report">打开报告</a><button type="button" data-copy-link="/issues/{quote(identifier, safe='')}/report">复制报告链接</button></div></section>"""


def _errors_list(errors) -> str:
    errors = list(errors)
    if not errors:
        return _empty("暂无匹配错误")
    return "".join(_error_row(error) for error in errors)


def _error_row(error: dict) -> str:
    state = "已解决" if error.get("resolved_at") is not None else "未解决"
    context = json.dumps(error.get("context") or {}, ensure_ascii=False, indent=2, sort_keys=True)
    identifier = error.get("issue_identifier") or "服务"
    retryable = "可重试" if error.get("retryable") else "不可重试"
    nearby = "".join(_event_row(event) for event in error.get("nearby_events", ()))
    nearby_details = f'<details><summary>相关事件</summary><div class="list">{nearby or _empty("暂无相关事件")}</div></details>'
    return f"""<article class="list-row error-row"><div class="row-top"><span class="row-title">{_escape(error.get('error_type') or 'RuntimeError')} · {_escape(identifier)}</span><time class="row-meta">{_format_time(error.get('timestamp'))}</time></div><div class="row-message">{_escape(error.get('message') or '')}</div><div class="row-meta">{_escape(error.get('stage') or 'runtime')} · 第 {_escape(error.get('attempt') if error.get('attempt') is not None else '—')} 次 · {retryable} · {state}</div><details><summary>错误上下文</summary><pre>{_escape(context)}</pre></details>{nearby_details}</article>"""


def _status_badge(value) -> str:
    normalized = str(value or "unknown").lower()
    css_status = normalized if normalized in _STATUS_LABELS else "unknown"
    return f'<span class="status status-{css_status}">{_STATUS_LABELS.get(normalized, _escape(value or "未知"))}</span>'


def _external_link(value, label: str) -> str:
    if not isinstance(value, str):
        return '<span class="subtle">暂无</span>'
    try:
        parsed = urlsplit(value)
    except ValueError:
        return '<span class="subtle">无效链接</span>'
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return '<span class="subtle">无效链接</span>'
    return f'<a href="{html.escape(value, quote=True)}" rel="noreferrer">{_escape(label)}</a>'


def _format_time(value) -> str:
    if value is None:
        return "—"
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return "—"


def _empty(message: str) -> str:
    return f'<p class="empty">{_escape(message)}</p>'


def _escape(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)
