from __future__ import annotations

from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
import weakref

from symphonz.service.models import RuntimeErrorRecord


_ISSUE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+$")
_REPORT_HEADING = "## Symphonz Implementation Report"
_MAX_TEXT_LENGTH = 4_000
_MAX_SHORT_TEXT_LENGTH = 255
_MAX_ITEMS = 50
_MAX_NODES = 24
_MAX_EDGES = 48
_PUBLISHERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_PUBLISHERS_BY_ID: dict[int, "ReportPublisher"] = {}


class ReportValidationError(ValueError):
    """Raised when a report payload cannot be safely persisted or rendered."""


@dataclass(frozen=True)
class ArchitectureNode:
    identifier: str
    label: str
    description: str


@dataclass(frozen=True)
class ArchitectureEdge:
    source: str
    target: str
    label: str


@dataclass(frozen=True)
class ReportDecision:
    decision: str
    rationale: str
    alternatives: tuple[str, ...]
    tradeoffs: tuple[str, ...]


@dataclass(frozen=True)
class ValidationEvidence:
    command: str
    result: str
    evidence: str


@dataclass(frozen=True)
class ReviewMetadata:
    provider: str
    url: str
    branch: str
    commit: str
    target: str


@dataclass(frozen=True)
class ReportDocument:
    issue_id: str
    issue_identifier: str
    title: str
    summary: str
    goal: str
    scope: str
    nodes: tuple[ArchitectureNode, ...]
    edges: tuple[ArchitectureEdge, ...]
    implementation: tuple[str, ...]
    decisions: tuple[ReportDecision, ...]
    changed_files: tuple[str, ...]
    validation: tuple[ValidationEvidence, ...]
    risks: tuple[str, ...]
    follow_ups: tuple[str, ...]
    review: ReviewMetadata

    def to_payload(self) -> dict:
        return {
            "operation": "publish",
            "issue_id": self.issue_id,
            "issue_identifier": self.issue_identifier,
            "title": self.title,
            "summary": self.summary,
            "goal": self.goal,
            "scope": self.scope,
            "architecture": {
                "nodes": [
                    {"id": node.identifier, "label": node.label, "description": node.description} for node in self.nodes
                ],
                "edges": [{"from": edge.source, "to": edge.target, "label": edge.label} for edge in self.edges],
            },
            "implementation": list(self.implementation),
            "decisions": [
                {
                    "decision": decision.decision,
                    "rationale": decision.rationale,
                    "alternatives": list(decision.alternatives),
                    "tradeoffs": list(decision.tradeoffs),
                }
                for decision in self.decisions
            ],
            "changed_files": list(self.changed_files),
            "validation": [
                {"command": item.command, "result": item.result, "evidence": item.evidence} for item in self.validation
            ],
            "risks": list(self.risks),
            "follow_ups": list(self.follow_ups),
            "review": {
                "provider": self.review.provider,
                "url": self.review.url,
                "branch": self.review.branch,
                "commit": self.review.commit,
                "target": self.review.target,
            },
        }


def report_tool_spec() -> dict:
    """Return the deliberately narrow dynamic-tool contract for reports."""
    required = [
        "operation", "issue_id", "issue_identifier", "title", "summary", "goal", "scope", "architecture",
        "implementation", "decisions", "changed_files", "validation", "risks", "follow_ups", "review",
    ]
    return {
        "name": "symphonz_report",
        "description": "Publish one validated Symphonz implementation report.",
        "inputSchema": {
            "type": "object",
            "properties": {name: {} for name in required},
            "required": required,
            "additionalProperties": False,
        },
    }


def validate_report(payload: object) -> ReportDocument:
    """Validate the full report contract and return an immutable document."""
    required = {
        "operation", "issue_id", "issue_identifier", "title", "summary", "goal", "scope", "architecture",
        "implementation", "decisions", "changed_files", "validation", "risks", "follow_ups", "review",
    }
    report = _object(payload, "report")
    _keys(report, required, "report")
    if report["operation"] != "publish":
        raise ReportValidationError("report.operation must be 'publish'.")

    issue_id = _text(report["issue_id"], "issue_id", _MAX_SHORT_TEXT_LENGTH)
    identifier = _text(report["issue_identifier"], "issue_identifier", 64)
    if _ISSUE_IDENTIFIER.fullmatch(identifier) is None:
        raise ReportValidationError("issue_identifier must be a safe Linear-style identifier.")
    architecture = _object(report["architecture"], "architecture")
    _keys(architecture, {"nodes", "edges"}, "architecture")
    nodes = _nodes(architecture["nodes"])
    edges = _edges(architecture["edges"], {node.identifier for node in nodes})
    return ReportDocument(
        issue_id=issue_id,
        issue_identifier=identifier,
        title=_text(report["title"], "title", _MAX_SHORT_TEXT_LENGTH),
        summary=_text(report["summary"], "summary"),
        goal=_text(report["goal"], "goal"),
        scope=_text(report["scope"], "scope"),
        nodes=nodes,
        edges=edges,
        implementation=_text_collection(report["implementation"], "implementation"),
        decisions=_decisions(report["decisions"]),
        changed_files=_text_collection(report["changed_files"], "changed_files"),
        validation=_validation(report["validation"]),
        risks=_text_collection(report["risks"], "risks"),
        follow_ups=_text_collection(report["follow_ups"], "follow_ups"),
        review=_review(report["review"]),
    )


def render_report(document: ReportDocument) -> str:
    """Render a deterministic, escaped HTML document without agent-supplied markup."""
    escaped = lambda value: html.escape(value, quote=True)
    navigation = ["summary", "goal-scope", "architecture", "implementation", "decisions", "validation", "review"]
    nav = "".join(f'<a href="#{name}">{escaped(name.replace("-", " ").title())}</a>' for name in navigation)
    nodes = "".join(
        f'<article class="architecture-node"><h3>{escaped(node.label)}</h3><p>{escaped(node.description)}</p></article>'
        for node in document.nodes
    )
    node_names = {node.identifier: node.label for node in document.nodes}
    edges = "".join(
        "<li><strong>{}</strong> to <strong>{}</strong>{}</li>".format(
            escaped(node_names[edge.source]),
            escaped(node_names[edge.target]),
            f": {escaped(edge.label)}" if edge.label else "",
        )
        for edge in document.edges
    )
    decisions = "".join(
        "<details open><summary>{}</summary><p>{}</p><p><strong>Alternatives:</strong> {}</p>"
        "<p><strong>Trade-offs:</strong> {}</p></details>".format(
            escaped(decision.decision), escaped(decision.rationale),
            escaped(", ".join(decision.alternatives)), escaped(", ".join(decision.tradeoffs))
        )
        for decision in document.decisions
    )
    validation = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td></tr>".format(
            escaped(item.command), escaped(item.result), escaped(item.evidence)
        )
        for item in document.validation
    )
    review_link = _link(document.review.url, "Review request")
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
:root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; background: #f6f8fb; }}
body {{ margin: 0; line-height: 1.5; }} header {{ background: #172033; color: #fff; padding: 2rem max(1rem, calc((100vw - 70rem) / 2)); }}
main {{ max-width: 70rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }} nav {{ position: sticky; top: 0; z-index: 1; background: #f6f8fb; padding: .75rem 0; display: flex; gap: 1rem; overflow-x: auto; }}
nav a {{ color: #1c5a99; white-space: nowrap; }} section {{ border-top: 1px solid #d9e0eb; padding: 1.25rem 0; }} h1, h2, h3 {{ line-height: 1.2; }}
.architecture {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: .75rem; }} .architecture-node, details {{ border: 1px solid #c9d4e3; background: #fff; padding: .85rem; }}
.architecture-node h3 {{ margin-top: 0; }} ul {{ padding-left: 1.2rem; }} code {{ overflow-wrap: anywhere; }} table {{ width: 100%; border-collapse: collapse; }} td, th {{ border: 1px solid #c9d4e3; padding: .5rem; text-align: left; vertical-align: top; }}
.meta {{ color: #58657a; }} @media print {{ header {{ background: #fff; color: #000; padding: 0; }} nav {{ display: none; }} main {{ max-width: none; padding: 0; }} section {{ break-inside: avoid; }} }}
</style></head><body id="top"><header><p class="meta">{identifier}</p><h1>{title}</h1><p>{summary}</p></header>
<main><nav aria-label="Report sections">{nav}<a href="#top">Copy link</a></nav>
<section id="summary"><h2>Summary</h2><p>{summary}</p></section>
<section id="goal-scope"><h2>Goal and Scope</h2><h3>Goal</h3><p>{goal}</p><h3>Scope</h3><p>{scope}</p></section>
<section id="architecture"><h2>Architecture</h2><div class="architecture">{nodes}</div><h3>Data Flow</h3><ul>{edges}</ul></section>
<section id="implementation"><h2>Implementation</h2>{implementation}</section>
<section id="decisions"><h2>Decisions</h2>{decisions}</section>
<section id="changed-files"><h2>Changed Files</h2>{changed_files}</section>
<section id="validation"><h2>Validation</h2><table><thead><tr><th>Command</th><th>Result</th><th>Evidence</th></tr></thead><tbody>{validation}</tbody></table></section>
<section id="risks"><h2>Risks</h2>{risks}</section><section id="follow-ups"><h2>Follow-ups</h2>{follow_ups}</section>
<section id="review"><h2>Linear and Review</h2><p>Linear issue: {identifier}</p><p>Branch: <code>{branch}</code></p><p>Commit: <code>{commit}</code></p><p>Target: {target}</p><p>{review_link}</p></section>
</main></body></html>""".format(
        title=escaped(document.title), identifier=escaped(document.issue_identifier), summary=escaped(document.summary),
        goal=escaped(document.goal), scope=escaped(document.scope), nav=nav, nodes=nodes, edges=edges,
        implementation=_list(document.implementation), decisions=decisions, changed_files=_list(document.changed_files),
        validation=validation, risks=_list(document.risks), follow_ups=_list(document.follow_ups),
        branch=escaped(document.review.branch), commit=escaped(document.review.commit), target=escaped(document.review.target),
        review_link=review_link,
    )


class ReportPublisher:
    """Persist an issue report and maintain one runtime-owned Linear comment."""

    def __init__(
        self, store, artifact_root: Path, public_base_url: str, linear_client, active_issue_id: str, active_issue_identifier: str
    ):
        self.store = store
        self.artifact_root = Path(artifact_root)
        self.public_base_url = _public_base_url(public_base_url)
        self.linear_client = linear_client
        self.active_issue_id = _text(active_issue_id, "active_issue_id", _MAX_SHORT_TEXT_LENGTH)
        self.active_issue_identifier = _text(active_issue_identifier, "active_issue_identifier", 64)
        if _ISSUE_IDENTIFIER.fullmatch(self.active_issue_identifier) is None:
            raise ValueError("active_issue_identifier must be a safe Linear-style identifier.")
        _register_publisher(linear_client, self)

    def report_url(self, issue_identifier: str) -> str:
        if issue_identifier != self.active_issue_identifier:
            raise ValueError("Report issue identifier does not match the active issue.")
        return f"{self.public_base_url}/issues/{issue_identifier}/report"

    def publish(self, arguments: object) -> dict:
        document = validate_report(arguments)
        self._validate_active_issue(document)
        now = time.time()
        html_page = render_report(document)
        json_path, html_path = self._write_report_files(document, html_page)
        existing = self.store.get_report(document.issue_identifier)
        entry = self._save(
            document, json_path, html_path, existing=existing, status="pending", retry_count=0, next_retry_at=now, now=now
        )
        self._update_task(document, now)
        try:
            self._sync(document, entry, now)
        except Exception as exc:
            entry = self._pending_after_failure(document, entry, exc, now)
        return {"success": True, "report_url": entry["url"], "linear_sync_status": entry["linear_sync_status"]}

    def sync_pending(self, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        synced = 0
        cursor = None
        while True:
            page = self.store.list_reports(cursor=cursor, limit=50)
            for entry in page["items"]:
                if entry.get("linear_sync_status") != "pending" or float(entry.get("next_retry_at") or 0) > current:
                    continue
                payload = entry.get("document")
                try:
                    document = validate_report(payload)
                    self._sync(document, entry, current)
                    synced += 1
                except Exception as exc:
                    if isinstance(payload, dict):
                        self._pending_after_failure(validate_report(payload), entry, exc, current)
            cursor = page.get("next_cursor")
            if not cursor:
                return synced

    def _validate_active_issue(self, document: ReportDocument) -> None:
        if document.issue_id != self.active_issue_id or document.issue_identifier != self.active_issue_identifier:
            raise ValueError("Report issue identity does not match the active issue.")

    def _write_report_files(self, document: ReportDocument, html_page: str) -> tuple[Path, Path]:
        directory = self.artifact_root / document.issue_identifier
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / "report.json"
        html_path = directory / "report.html"
        self._atomic_replace(json_path, json.dumps(document.to_payload(), indent=2, sort_keys=True) + "\n")
        self._atomic_replace(html_path, html_page)
        return json_path, html_path

    @staticmethod
    def _atomic_replace(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def _save(self, document, json_path, html_path, *, existing, status, retry_count, next_retry_at, now, comment_id=None):
        entry = {
            "issue_identifier": document.issue_identifier,
            "report_version": 1,
            "json_path": str(json_path),
            "html_path": str(html_path),
            "url": self.report_url(document.issue_identifier),
            "review_metadata": document.to_payload()["review"],
            "linear_comment_id": comment_id if comment_id is not None else (existing or {}).get("linear_comment_id"),
            "linear_sync_status": status,
            "retry_count": retry_count,
            "next_retry_at": next_retry_at,
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
            "document": document.to_payload(),
            "issue_id": document.issue_id,
            "summary": document.summary,
        }
        self.store.save_report(entry)
        return self.store.get_report(document.issue_identifier)

    def _update_task(self, document: ReportDocument, now: float) -> None:
        self.store.upsert_issue(
            {
                "issue_identifier": document.issue_identifier,
                "issue_id": document.issue_id,
                "title": document.title,
                "branch": document.review.branch,
                "commit_hash": document.review.commit,
                "review_url": document.review.url,
                "report_url": self.report_url(document.issue_identifier),
                "report_published_at": now,
            }
        )

    def _sync(self, document: ReportDocument, entry: dict, now: float) -> None:
        comment_id = entry.get("linear_comment_id") or self._find_comment_id(document.issue_id)
        body = self._comment_body(document, entry["url"], now)
        if comment_id:
            result = self._graphql(
                "mutation SymphonzUpdateReportComment($id: String!, $input: CommentUpdateInput!) { "
                "commentUpdate(id: $id, input: $input) { success comment { id } } }",
                {"id": comment_id, "input": {"body": body}},
            )
            comment_id = result.get("data", {}).get("commentUpdate", {}).get("comment", {}).get("id") or comment_id
        else:
            result = self._graphql(
                "mutation SymphonzCreateReportComment($input: CommentCreateInput!) { "
                "commentCreate(input: $input) { success comment { id } } }",
                {"input": {"issueId": document.issue_id, "body": body}},
            )
            comment_id = result.get("data", {}).get("commentCreate", {}).get("comment", {}).get("id")
            if not comment_id:
                raise RuntimeError("Linear did not return a report comment identifier.")
        self._save(
            document, Path(entry["json_path"]), Path(entry["html_path"]), existing=entry, status="synced", retry_count=0,
            next_retry_at=None, now=now, comment_id=str(comment_id),
        )

    def _pending_after_failure(self, document: ReportDocument, entry: dict, error: Exception, now: float) -> dict:
        retries = int(entry.get("retry_count") or 0) + 1
        retry_at = now + min(300.0, float(2 ** min(retries - 1, 8)))
        pending = self._save(
            document, Path(entry["json_path"]), Path(entry["html_path"]), existing=entry, status="pending", retry_count=retries,
            next_retry_at=retry_at, now=now, comment_id=entry.get("linear_comment_id"),
        )
        self.store.record_error(
            RuntimeErrorRecord(
                issue_identifier=document.issue_identifier,
                stage="reporting",
                error_type=type(error).__name__,
                message=str(error),
                retryable=True,
                timestamp=now,
                context={"linear_sync_status": "pending", "retry_count": retries},
            )
        )
        return pending

    def _find_comment_id(self, issue_id: str) -> str | None:
        result = self._graphql(
            "query SymphonzFindReportComment($issueId: String!) { issue(id: $issueId) { comments { nodes { id body } } } }",
            {"issueId": issue_id},
        )
        comments = result.get("data", {}).get("issue", {}).get("comments", {}).get("nodes", [])
        for comment in comments:
            if isinstance(comment, dict) and str(comment.get("body") or "").startswith(_REPORT_HEADING):
                identifier = comment.get("id")
                if identifier:
                    return str(identifier)
        return None

    def _graphql(self, query: str, variables: dict) -> dict:
        if self.linear_client is None:
            raise RuntimeError("Linear client is unavailable.")
        response = self.linear_client.graphql(query, variables)
        if not isinstance(response, dict):
            raise RuntimeError("Linear returned an invalid response.")
        if response.get("errors"):
            raise RuntimeError(str(response["errors"]))
        return response

    @staticmethod
    def _comment_body(document: ReportDocument, report_url: str, now: float) -> str:
        return "\n".join(
            [
                _REPORT_HEADING,
                "",
                f"Report: {report_url}",
                f"Review: {document.review.url}",
                f"Branch: `{document.review.branch}`",
                f"Commit: `{document.review.commit}`",
                f"Published: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}",
                "",
                document.summary,
            ]
        )


def sync_pending(linear_client, now: float) -> int:
    """Retry due report synchronization for the publisher associated with this client."""
    publisher = _PUBLISHERS.get(linear_client) if linear_client is not None else None
    if publisher is None and linear_client is not None:
        publisher = _PUBLISHERS_BY_ID.get(id(linear_client))
    return publisher.sync_pending(now) if publisher is not None else 0


def _register_publisher(linear_client, publisher: ReportPublisher) -> None:
    if linear_client is None:
        return
    try:
        _PUBLISHERS[linear_client] = publisher
    except TypeError:
        _PUBLISHERS_BY_ID[id(linear_client)] = publisher


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ReportValidationError(f"{name} must be an object.")
    return value


def _keys(value: dict, expected: set[str], name: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise ReportValidationError(f"{name} contains unknown fields: {', '.join(sorted(unknown))}.")
    if missing:
        raise ReportValidationError(f"{name} is missing required fields: {', '.join(sorted(missing))}.")


def _text(value: object, name: str, limit: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError(f"{name} must be a non-empty string.")
    if len(value) > limit:
        raise ReportValidationError(f"{name} must be at most {limit} characters.")
    if "\x00" in value:
        raise ReportValidationError(f"{name} cannot contain NUL characters.")
    return value


def _collection(value: object, name: str, limit: int = _MAX_ITEMS) -> list:
    if not isinstance(value, list):
        raise ReportValidationError(f"{name} must be an array.")
    if len(value) > limit:
        raise ReportValidationError(f"{name} must contain at most {limit} items.")
    return value


def _text_collection(value: object, name: str) -> tuple[str, ...]:
    return tuple(_text(item, f"{name}[{index}]") for index, item in enumerate(_collection(value, name)))


def _nodes(value: object) -> tuple[ArchitectureNode, ...]:
    nodes = _collection(value, "architecture.nodes", _MAX_NODES)
    if not nodes:
        raise ReportValidationError("architecture.nodes must contain at least one item.")
    result = []
    identifiers = set()
    for index, raw in enumerate(nodes):
        node = _object(raw, f"architecture.nodes[{index}]")
        if set(node) not in ({"id", "label"}, {"id", "label", "description"}):
            raise ReportValidationError(f"architecture.nodes[{index}] contains unknown or missing fields.")
        identifier = _text(node["id"], f"architecture.nodes[{index}].id", _MAX_SHORT_TEXT_LENGTH)
        if identifier in identifiers:
            raise ReportValidationError("architecture node ids must be unique.")
        identifiers.add(identifier)
        result.append(ArchitectureNode(identifier, _text(node["label"], f"architecture.nodes[{index}].label", _MAX_SHORT_TEXT_LENGTH), _text(node.get("description", "Not specified."), f"architecture.nodes[{index}].description")))
    return tuple(result)


def _edges(value: object, node_ids: set[str]) -> tuple[ArchitectureEdge, ...]:
    edges = _collection(value, "architecture.edges", _MAX_EDGES)
    result = []
    for index, raw in enumerate(edges):
        edge = _object(raw, f"architecture.edges[{index}]")
        if set(edge) not in ({"from", "to"}, {"from", "to", "label"}):
            raise ReportValidationError(f"architecture.edges[{index}] contains unknown or missing fields.")
        source = _text(edge["from"], f"architecture.edges[{index}].from", _MAX_SHORT_TEXT_LENGTH)
        target = _text(edge["to"], f"architecture.edges[{index}].to", _MAX_SHORT_TEXT_LENGTH)
        if source not in node_ids or target not in node_ids:
            raise ReportValidationError("architecture edges must reference known nodes.")
        result.append(ArchitectureEdge(source, target, _text(edge.get("label", ""), f"architecture.edges[{index}].label", _MAX_SHORT_TEXT_LENGTH) if edge.get("label", "") else ""))
    return tuple(result)


def _decisions(value: object) -> tuple[ReportDecision, ...]:
    result = []
    for index, raw in enumerate(_collection(value, "decisions")):
        decision = _object(raw, f"decisions[{index}]")
        _keys(decision, {"decision", "rationale", "alternatives", "tradeoffs"}, f"decisions[{index}]")
        result.append(ReportDecision(_text(decision["decision"], f"decisions[{index}].decision"), _text(decision["rationale"], f"decisions[{index}].rationale"), _text_collection(decision["alternatives"], f"decisions[{index}].alternatives"), _text_collection(decision["tradeoffs"], f"decisions[{index}].tradeoffs")))
    return tuple(result)


def _validation(value: object) -> tuple[ValidationEvidence, ...]:
    result = []
    for index, raw in enumerate(_collection(value, "validation")):
        item = _object(raw, f"validation[{index}]")
        _keys(item, {"command", "result", "evidence"}, f"validation[{index}]")
        result.append(ValidationEvidence(_text(item["command"], f"validation[{index}].command"), _text(item["result"], f"validation[{index}].result", _MAX_SHORT_TEXT_LENGTH), _text(item["evidence"], f"validation[{index}].evidence")))
    return tuple(result)


def _review(value: object) -> ReviewMetadata:
    review = _object(value, "review")
    _keys(review, {"provider", "url", "branch", "commit", "target"}, "review")
    return ReviewMetadata(_text(review["provider"], "review.provider", _MAX_SHORT_TEXT_LENGTH), _url(review["url"], "review.url"), _text(review["branch"], "review.branch", _MAX_SHORT_TEXT_LENGTH), _text(review["commit"], "review.commit", _MAX_SHORT_TEXT_LENGTH), _text(review["target"], "review.target", _MAX_SHORT_TEXT_LENGTH))


def _url(value: object, name: str) -> str:
    text = _text(value, name, _MAX_TEXT_LENGTH)
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ReportValidationError(f"{name} must use an http or https URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or any(char.isspace() for char in text):
        raise ReportValidationError(f"{name} must use an http or https URL.")
    return text


def _public_base_url(value: str) -> str:
    return _url(value, "public_base_url").rstrip("/")


def _list(values: tuple[str, ...]) -> str:
    return "<ul>{}</ul>".format("".join(f"<li>{html.escape(value, quote=True)}</li>" for value in values))


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label, quote=True)}</a>'
