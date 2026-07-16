from __future__ import annotations

from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from urllib.parse import unquote, urlsplit, urlunsplit
import weakref

from symphonz.service.models import RuntimeErrorRecord


_ISSUE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+$")
_REPORT_HEADING = "## Symphonz Implementation Report"
_MAX_TEXT_LENGTH = 4_000
_MAX_SHORT_TEXT_LENGTH = 255
_MAX_ITEMS = 50
_MAX_NODES = 24
_MAX_EDGES = 48
_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
_SYNC_LEASE_SECONDS = 30.0
_BUNDLE_NAME = re.compile(r"report-[a-z0-9]+\.(?:json|html)$")
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
    text = _text_schema(_MAX_TEXT_LENGTH)
    short_text = _text_schema(_MAX_SHORT_TEXT_LENGTH)
    text_list = {"type": "array", "maxItems": _MAX_ITEMS, "items": text}
    node = {
        "type": "object",
        "properties": {"id": short_text, "label": short_text, "description": text},
        "required": ["id", "label"],
        "additionalProperties": False,
    }
    edge = {
        "type": "object",
        "properties": {"from": short_text, "to": short_text, "label": short_text},
        "required": ["from", "to"],
        "additionalProperties": False,
    }
    decision = {
        "type": "object",
        "properties": {
            "decision": text,
            "rationale": text,
            "alternatives": text_list,
            "tradeoffs": text_list,
        },
        "required": ["decision", "rationale", "alternatives", "tradeoffs"],
        "additionalProperties": False,
    }
    evidence = {
        "type": "object",
        "properties": {"command": text, "result": short_text, "evidence": text},
        "required": ["command", "result", "evidence"],
        "additionalProperties": False,
    }
    review = {
        "type": "object",
        "properties": {
            "provider": short_text,
            "url": {**text, "format": "uri"},
            "branch": short_text,
            "commit": short_text,
            "target": short_text,
        },
        "required": ["provider", "url", "branch", "commit", "target"],
        "additionalProperties": False,
    }
    properties = {
        "operation": {"type": "string", "const": "publish"},
        "issue_id": short_text,
        "issue_identifier": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": f"^{_ISSUE_IDENTIFIER.pattern}",
        },
        "title": short_text,
        "summary": text,
        "goal": text,
        "scope": text,
        "architecture": {
            "type": "object",
            "properties": {
                "nodes": {"type": "array", "minItems": 1, "maxItems": _MAX_NODES, "items": node},
                "edges": {"type": "array", "maxItems": _MAX_EDGES, "items": edge},
            },
            "required": ["nodes", "edges"],
            "additionalProperties": False,
        },
        "implementation": text_list,
        "decisions": {"type": "array", "maxItems": _MAX_ITEMS, "items": decision},
        "changed_files": text_list,
        "validation": {"type": "array", "maxItems": _MAX_ITEMS, "items": evidence},
        "risks": text_list,
        "follow_ups": text_list,
        "review": review,
    }
    return {
        "name": "symphonz_report",
        "description": "Publish one validated Symphonz implementation report.",
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
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
        self,
        store,
        artifact_root: Path,
        public_base_url: str,
        linear_client,
        active_issue_id: str,
        active_issue_identifier: str,
        error_sink=None,
    ):
        self.store = store
        self.artifact_root = Path(os.path.abspath(os.fspath(artifact_root)))
        self.public_base_url = _public_base_url(public_base_url)
        self.linear_client = linear_client
        self.error_sink = error_sink
        self.active_issue_id = _text(active_issue_id, "active_issue_id", _MAX_SHORT_TEXT_LENGTH)
        self.active_issue_identifier = _text(active_issue_identifier, "active_issue_identifier", 64)
        if _ISSUE_IDENTIFIER.fullmatch(self.active_issue_identifier) is None:
            raise ValueError("active_issue_identifier must be a safe Linear-style identifier.")
        artifact_root_fd = _open_pinned_directory(self.artifact_root)
        try:
            root_metadata = os.fstat(artifact_root_fd)
            self._artifact_root_identity = (root_metadata.st_dev, root_metadata.st_ino)
        finally:
            os.close(artifact_root_fd)
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
        existing = self.store.get_report(document.issue_identifier)
        generation = secrets.token_hex(12)
        json_name = f"report-{generation}.json"
        html_name = f"report-{generation}.html"
        json_path = self.artifact_root / document.issue_identifier / json_name
        html_path = self.artifact_root / document.issue_identifier / html_name
        directory_fd = self._open_issue_directory(document.issue_identifier, create=True)
        try:
            try:
                self._write_bundle_file(
                    directory_fd,
                    json_name,
                    json.dumps(document.to_payload(), indent=2, sort_keys=True) + "\n",
                )
                self._write_bundle_file(directory_fd, html_name, html_page)
                os.fsync(directory_fd)
                self._assert_root_unchanged()
                self.store.save_report(
                    {
                        "issue_identifier": document.issue_identifier,
                        "report_version": 1,
                        "json_path": str(json_path),
                        "html_path": str(html_path),
                        "url": self.report_url(document.issue_identifier),
                        "review_metadata": document.to_payload()["review"],
                        "linear_comment_id": (existing or {}).get("linear_comment_id"),
                        "linear_sync_status": "pending",
                        "retry_count": 0,
                        "next_retry_at": now,
                        "created_at": (existing or {}).get("created_at") or now,
                        "updated_at": now,
                        "issue_id": document.issue_id,
                        "summary": document.summary,
                    }
                )
            except Exception:
                self._unlink_bundle_files(directory_fd, json_name, html_name)
                raise
            entry = self.store.get_report(document.issue_identifier)
            self._cleanup_previous_bundle(directory_fd, existing, entry)
        finally:
            os.close(directory_fd)
        self._update_task(document, now)
        owner = f"publish-{secrets.token_hex(16)}"
        claimed = self.store.claim_report_sync(
            document.issue_identifier,
            owner=owner,
            now=now,
            lease_seconds=_SYNC_LEASE_SECONDS,
        )
        if claimed is not None:
            self._sync_claimed(claimed, owner, now)
        entry = self.store.get_report(document.issue_identifier)
        return {"success": True, "report_url": entry["url"], "linear_sync_status": entry["linear_sync_status"]}

    def sync_pending(self, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        synced = 0
        cursor = None
        while True:
            page = self.store.list_reports(cursor=cursor, limit=50)
            for entry in page["items"]:
                if entry.get("issue_identifier") != self.active_issue_identifier:
                    continue
                owner = f"retry-{secrets.token_hex(16)}"
                claimed = self.store.claim_report_sync(
                    entry["issue_identifier"],
                    owner=owner,
                    now=current,
                    lease_seconds=_SYNC_LEASE_SECONDS,
                )
                if claimed is not None and self._sync_claimed(claimed, owner, current):
                    synced += 1
            cursor = page.get("next_cursor")
            if not cursor:
                return synced

    def _validate_active_issue(self, document: ReportDocument) -> None:
        if document.issue_id != self.active_issue_id or document.issue_identifier != self.active_issue_identifier:
            raise ValueError("Report issue identity does not match the active issue.")

    def _assert_root_unchanged(self) -> None:
        descriptor = _open_existing_directory(self.artifact_root, self._artifact_root_identity)
        os.close(descriptor)

    def _open_issue_directory(self, issue_identifier: str, *, create: bool) -> int:
        if _ISSUE_IDENTIFIER.fullmatch(issue_identifier) is None:
            raise RuntimeError("Report issue directory is outside the artifact root.")
        root_fd = _open_existing_directory(self.artifact_root, self._artifact_root_identity)
        try:
            if create:
                try:
                    os.mkdir(issue_identifier, 0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
            try:
                metadata = os.stat(issue_identifier, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError as error:
                raise RuntimeError("Report artifact issue directory is missing.") from error
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError("Report artifact issue directory must not be a symbolic link.")
            if not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError("Report artifact issue path must be a directory.")
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(issue_identifier, flags, dir_fd=root_fd)
            except OSError as error:
                raise RuntimeError("Report artifact issue directory must not be a symbolic link.") from error
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                os.close(descriptor)
                raise RuntimeError("Report artifact issue directory changed while opening.")
            return descriptor
        finally:
            os.close(root_fd)

    @staticmethod
    def _write_bundle_file(directory_fd: int, filename: str, content: str) -> None:
        temporary_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            output = os.fdopen(descriptor, "w", encoding="utf-8")
            descriptor = -1
            with output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = ""
            os.fsync(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _unlink_bundle_files(directory_fd: int, *filenames: str) -> None:
        changed = False
        for filename in filenames:
            try:
                os.unlink(filename, dir_fd=directory_fd)
                changed = True
            except FileNotFoundError:
                pass
        if changed:
            os.fsync(directory_fd)

    def _cleanup_previous_bundle(self, directory_fd: int, previous: dict | None, current: dict) -> None:
        if previous is None:
            return
        current_names = {Path(current["json_path"]).name, Path(current["html_path"]).name}
        stale_names = []
        for key in ("json_path", "html_path"):
            value = previous.get(key)
            if not value:
                continue
            candidate = Path(value)
            if candidate.parent == self.artifact_root / current["issue_identifier"] and candidate.name not in current_names:
                stale_names.append(candidate.name)
        self._unlink_bundle_files(directory_fd, *stale_names)

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

    def _sync_claimed(self, entry: dict, owner: str, now: float) -> bool:
        try:
            document = self._load_report_document(entry)
            self._validate_active_issue(document)
            self._sync(document, entry, now)
            return True
        except Exception as error:
            self._pending_after_failure(entry, error, now)
            return False
        finally:
            self.store.release_report_sync(entry["issue_identifier"], owner=owner)

    def _load_report_document(self, entry: dict) -> ReportDocument:
        issue_identifier = str(entry.get("issue_identifier") or "")
        filename = self._artifact_filename(entry.get("json_path"), issue_identifier, ".json")
        directory_fd = self._open_issue_directory(issue_identifier, create=False)
        descriptor = -1
        try:
            try:
                descriptor = os.open(
                    filename,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError as error:
                raise RuntimeError("Report JSON artifact is missing.") from error
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("Report JSON artifact must be a regular file.")
            source = os.fdopen(descriptor, "r", encoding="utf-8")
            descriptor = -1
            with source:
                content = source.read(_MAX_ARTIFACT_BYTES + 1)
            if len(content.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
                raise RuntimeError("Report JSON artifact exceeds the safe size limit.")
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, UnicodeError) as error:
                raise RuntimeError("Report JSON artifact is corrupt.") from error
            return validate_report(payload)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory_fd)

    def _artifact_filename(self, value: object, issue_identifier: str, suffix: str) -> str:
        if not isinstance(value, str):
            raise RuntimeError("Report artifact path is missing from RuntimeStore.")
        candidate = Path(value)
        expected_parent = self.artifact_root / issue_identifier
        if not candidate.is_absolute() or candidate.parent != expected_parent:
            raise RuntimeError("Report artifact path is outside the pinned artifact root.")
        if candidate.suffix != suffix or _BUNDLE_NAME.fullmatch(candidate.name) is None:
            raise RuntimeError("Report artifact path is not a versioned report bundle.")
        return candidate.name

    def _sync(self, document: ReportDocument, entry: dict, now: float) -> None:
        comment_id = entry.get("linear_comment_id") or self._find_comment_id(document.issue_id)
        body = self._comment_body(document, entry["url"], now)
        if comment_id:
            result = self._graphql(
                "mutation SymphonzUpdateReportComment($id: String!, $input: CommentUpdateInput!) { "
                "commentUpdate(id: $id, input: $input) { success comment { id } } }",
                {"id": comment_id, "input": {"body": body}},
            )
            comment_id = self._mutation_comment_id(result, "commentUpdate")
        else:
            result = self._graphql(
                "mutation SymphonzCreateReportComment($input: CommentCreateInput!) { "
                "commentCreate(input: $input) { success comment { id } } }",
                {"input": {"issueId": document.issue_id, "body": body}},
            )
            comment_id = self._mutation_comment_id(result, "commentCreate")
        updated = self._save_sync_state(
            entry,
            status="synced",
            retry_count=0,
            next_retry_at=None,
            now=now,
            comment_id=comment_id,
        )
        if updated.get("json_path") == entry.get("json_path") and updated.get("linear_sync_status") == "synced":
            self.store.resolve_report_sync_errors(
                updated["issue_identifier"],
                resolving_event="report_sync_succeeded",
                resolved_at=now,
            )

    @staticmethod
    def _mutation_comment_id(result: dict, mutation_name: str) -> str:
        mutation = result.get("data", {}).get(mutation_name, {})
        if not isinstance(mutation, dict) or mutation.get("success") is not True:
            raise RuntimeError(f"Linear {mutation_name} did not report success.")
        comment = mutation.get("comment")
        comment_id = comment.get("id") if isinstance(comment, dict) else None
        if not isinstance(comment_id, str) or not comment_id.strip():
            raise RuntimeError(f"Linear {mutation_name} did not return a valid comment identifier.")
        return comment_id

    def _pending_after_failure(self, entry: dict, error: Exception, now: float) -> dict:
        retries = int(entry.get("retry_count") or 0) + 1
        retry_at = now + min(300.0, float(2 ** min(retries - 1, 8)))
        pending = self._save_sync_state(
            entry,
            status="pending",
            retry_count=retries,
            next_retry_at=retry_at,
            now=now,
            comment_id=entry.get("linear_comment_id"),
        )
        record = RuntimeErrorRecord(
            issue_identifier=entry["issue_identifier"],
            stage="report_sync",
            error_type=type(error).__name__,
            message=str(error),
            retryable=True,
            timestamp=now,
            context={"linear_sync_status": "pending", "retry_count": retries},
        )
        self.store.record_error(record)
        self._emit_error(record)
        return pending

    def _save_sync_state(
        self,
        entry: dict,
        *,
        status: str,
        retry_count: int,
        next_retry_at: float | None,
        now: float,
        comment_id: str | None,
    ) -> dict:
        self.store.update_report_sync_state(
            entry["issue_identifier"],
            expected_json_path=entry["json_path"],
            linear_sync_status=status,
            linear_comment_id=comment_id,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
            updated_at=now,
        )
        return self.store.get_report(entry["issue_identifier"])

    def _emit_error(self, record: RuntimeErrorRecord) -> None:
        if self.error_sink is None:
            return
        writer = self.error_sink.write if hasattr(self.error_sink, "write") else self.error_sink
        try:
            writer(record)
        except Exception:
            return

    def _find_comment_id(self, issue_id: str) -> str | None:
        after = None
        while True:
            result = self._graphql(
                "query SymphonzFindReportComment($issueId: String!, $after: String) { "
                "issue(id: $issueId) { comments(first: 50, after: $after) { "
                "nodes { id body } pageInfo { hasNextPage endCursor } } } }",
                {"issueId": issue_id, "after": after},
            )
            comments = result.get("data", {}).get("issue", {}).get("comments", {})
            if not isinstance(comments, dict):
                raise RuntimeError("Linear returned invalid report comment pagination data.")
            nodes = comments.get("nodes", [])
            if not isinstance(nodes, list):
                raise RuntimeError("Linear returned invalid report comments.")
            for comment in nodes:
                if isinstance(comment, dict) and str(comment.get("body") or "").startswith(_REPORT_HEADING):
                    identifier = comment.get("id")
                    if isinstance(identifier, str) and identifier:
                        return identifier
            page_info = comments.get("pageInfo") or {}
            if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
                return None
            after = page_info.get("endCursor")
            if not isinstance(after, str) or not after:
                raise RuntimeError("Linear report comment pagination is missing an end cursor.")

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
        review_url = _neutralize_markdown(document.review.url)
        branch = _neutralize_markdown(document.review.branch)
        commit = _neutralize_markdown(document.review.commit)
        summary = _neutralize_markdown(document.summary)
        return "\n".join(
            [
                _REPORT_HEADING,
                "",
                f"Report: {report_url}",
                f"Review: {review_url}",
                f"Branch: `{branch}`",
                f"Commit: `{commit}`",
                f"Published: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}",
                "",
                f"Summary: {summary}",
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
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ReportValidationError(f"{name} must use an http or https URL.") from exc
    has_control = any(char.isspace() or ord(char) <= 31 or ord(char) == 127 for char in text)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or has_control
    ):
        raise ReportValidationError(f"{name} must use an http or https URL.")
    return text


def _public_base_url(value: str) -> str:
    text = _url(value, "public_base_url")
    parsed = urlsplit(text)
    if parsed.query or parsed.fragment or "?" in text or "#" in text:
        raise ReportValidationError("public_base_url must not contain a query or fragment.")
    segments = []
    for raw_segment in parsed.path.split("/"):
        if not raw_segment:
            continue
        decoded = unquote(raw_segment)
        if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
            raise ReportValidationError("public_base_url path must not contain traversal segments.")
        segments.append(raw_segment)
    normalized_path = f"/{'/'.join(segments)}" if segments else ""
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _text_schema(maximum_length: int) -> dict:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": maximum_length,
        "pattern": r"^(?=.*\S)[^\u0000]*$",
    }


def _neutralize_markdown(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed.translate(
        {
            ord("@"): "\uFF20",
            ord("#"): "\uFF03",
            ord("["): "\uFF3B",
            ord("]"): "\uFF3D",
            ord("("): "\uFF08",
            ord(")"): "\uFF09",
            ord("<"): "\uFF1C",
            ord(">"): "\uFF1E",
            ord("`"): "'",
            ord("\\"): "\uFF3C",
        }
    )


def _open_pinned_directory(path: Path) -> int:
    if not path.is_absolute() or not path.name:
        raise RuntimeError("Report artifact root must be an absolute directory and not a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(path.parent, flags)
    except OSError as error:
        raise RuntimeError("Report artifact root parent must be a directory and not a symbolic link.") from error
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise RuntimeError("Report artifact root must be a directory and not a symbolic link.") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("Report artifact root must be a directory and not a symbolic link.")
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        except OSError as error:
            raise RuntimeError("Report artifact root must be a directory and not a symbolic link.") from error
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            os.close(descriptor)
            raise RuntimeError("Report artifact root changed while opening.")
        return descriptor
    finally:
        os.close(parent_fd)


def _open_existing_directory(path: Path, expected_identity: tuple[int, int]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError("Report artifact root changed after it was pinned.") from error
    metadata = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != expected_identity:
        os.close(descriptor)
        raise RuntimeError("Report artifact root changed after it was pinned.")
    return descriptor


def _list(values: tuple[str, ...]) -> str:
    return "<ul>{}</ul>".format("".join(f"<li>{html.escape(value, quote=True)}</li>" for value in values))


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label, quote=True)}</a>'
