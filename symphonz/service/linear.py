from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlparse
import urllib.request

from symphonz.service.models import BlockerRef, Issue


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

POLL_QUERY = """
query SymphonzPoll($projectSlug: String!, $stateNames: [String!]!, $first: Int!, $after: String) {
  issues(filter: {project: {slugId: {eq: $projectSlug}}, state: {name: {in: $stateNames}}}, first: $first, after: $after) {
    nodes {
      id
      identifier
      title
      description
      priority
      state { name }
      branchName
      url
      labels { nodes { name } }
      inverseRelations { nodes { type issue { id identifier state { name } } } }
      createdAt
      updatedAt
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

ISSUES_BY_ID_QUERY = """
query SymphonzIssuesById($ids: [ID!]!, $first: Int!, $after: String) {
  issues(filter: {id: {in: $ids}}, first: $first, after: $after) {
    nodes {
      id
      identifier
      title
      description
      priority
      state { name }
      branchName
      url
      labels { nodes { name } }
      inverseRelations { nodes { type issue { id identifier state { name } } } }
      createdAt
      updatedAt
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

ISSUES_BY_STATE_QUERY = """
query SymphonzIssuesByState($projectSlug: String!, $stateNames: [String!]!, $first: Int!, $after: String) {
  issues(filter: {project: {slugId: {eq: $projectSlug}}, state: {name: {in: $stateNames}}}, first: $first, after: $after) {
    nodes {
      id
      identifier
      title
      description
      priority
      state { name }
      branchName
      url
      labels { nodes { name } }
      inverseRelations { nodes { type issue { id identifier state { name } } } }
      createdAt
      updatedAt
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class LinearClient:
    def __init__(self, api_key: str, project_slug: str, endpoint: str = LINEAR_GRAPHQL_URL):
        self.api_key = api_key
        self.project_slug = project_slug
        self.endpoint = endpoint

    def fetch_candidate_issues(self, active_states: list[str]) -> list[Issue]:
        return self.fetch_issues_by_states(active_states, query=POLL_QUERY)

    def fetch_issues_by_states(self, states: list[str], query: str = ISSUES_BY_STATE_QUERY) -> list[Issue]:
        return self._fetch_paginated_issues(
            query,
            {"projectSlug": self.project_slug, "stateNames": states},
        )

    def fetch_issues_by_ids(self, ids: list[str]) -> list[Issue]:
        if not ids:
            return []
        return self._fetch_paginated_issues(ISSUES_BY_ID_QUERY, {"ids": ids})

    def _fetch_paginated_issues(self, query: str, variables: dict) -> list[Issue]:
        issues: list[Issue] = []
        after: str | None = None
        while True:
            body = self.graphql(query, {**variables, "first": 50, "after": after})
            issues.extend(normalize_issue_nodes(body))
            page_info = body.get("data", {}).get("issues", {}).get("pageInfo") or {}
            if not page_info.get("hasNextPage", False):
                return issues
            after = page_info.get("endCursor")
            if not after:
                raise RuntimeError("Linear GraphQL page hasNextPage=true without an end cursor.")

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        if self.endpoint.startswith("file://"):
            return self.graphql_fixture(query, variables or {})

        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())

    def graphql_fixture(self, query: str, variables: dict) -> dict:
        fixture_root = Path(urlparse(self.endpoint).path)
        fixture_root.mkdir(parents=True, exist_ok=True)
        operation = graphql_operation_name(query)
        request_record = {
            "authorization": self.api_key,
            "operation": operation,
            "variables": variables,
        }
        requests_path = fixture_root / "requests.jsonl"
        with requests_path.open("a") as requests_file:
            requests_file.write(json.dumps(request_record, sort_keys=True) + "\n")

        state_path = fixture_root / "state.json"
        if state_path.exists():
            stateful = stateful_fixture_response(state_path, operation, variables)
            if stateful is not None:
                return stateful

        responses_path = fixture_root / "responses.json"
        if not responses_path.exists():
            raise RuntimeError(f"Linear fixture responses file is missing: {responses_path}")
        responses = json.loads(responses_path.read_text())
        if operation in responses:
            return responses[operation]
        if "default" in responses:
            return responses["default"]
        raise RuntimeError(f"Linear fixture has no response for operation {operation}.")


def stateful_fixture_response(state_path: Path, operation: str, variables: dict) -> dict | None:
    state = json.loads(state_path.read_text())
    issue = state.get("issue")
    if not isinstance(issue, dict):
        raise RuntimeError(f"Linear fixture state is missing an issue: {state_path}")

    if operation == "SymphonzSetState":
        state_name = variables.get("stateName")
        if not isinstance(state_name, str) or not state_name.strip():
            return {"errors": [{"message": "stateName is required"}]}
        issue["state"] = {"name": state_name}
        _write_json_atomic(state_path, state)
        return {"data": {"issueUpdate": {"success": True}}}

    if operation in {"SymphonzPoll", "SymphonzIssuesByState"}:
        states = {str(value).strip().lower() for value in variables.get("stateNames", [])}
        current = str((issue.get("state") or {}).get("name") or "").strip().lower()
        return _fixture_issue_page([issue] if current in states else [])

    if operation == "SymphonzIssuesById":
        ids = {str(value) for value in variables.get("ids", [])}
        return _fixture_issue_page([issue] if str(issue.get("id")) in ids else [])

    return None


def _fixture_issue_page(nodes: list[dict]) -> dict:
    return {
        "data": {
            "issues": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def graphql_operation_name(query: str) -> str:
    match = re.search(r"\b(?:query|mutation)\s+([A-Za-z_][A-Za-z0-9_]*)", query)
    if match:
        return match.group(1)
    return "anonymous"


def normalize_issue_nodes(body: dict) -> list[Issue]:
    if body.get("errors"):
        raise RuntimeError(f"Linear GraphQL returned errors: {body['errors']}")
    nodes = body.get("data", {}).get("issues", {}).get("nodes", [])
    return [issue for node in nodes if (issue := normalize_issue(node)) is not None]


def normalize_issue(node: dict) -> Issue | None:
    if not isinstance(node, dict):
        return None
    labels = [
        str(label.get("name", "")).strip().lower()
        for label in node.get("labels", {}).get("nodes", [])
        if str(label.get("name", "")).strip()
    ]
    blocked_by = [
        blocker
        for relation in node.get("inverseRelations", {}).get("nodes", [])
        if (blocker := normalize_blocker_relation(relation)) is not None
    ]
    state = node.get("state") or {}
    return Issue(
        id=str(node.get("id") or ""),
        identifier=str(node.get("identifier") or ""),
        title=str(node.get("title") or ""),
        description=node.get("description"),
        priority=node.get("priority"),
        state=state.get("name"),
        branch_name=node.get("branchName"),
        url=node.get("url"),
        labels=labels,
        created_at=node.get("createdAt"),
        updated_at=node.get("updatedAt"),
        blocked_by=blocked_by,
    )


def normalize_blocker_relation(relation: object) -> BlockerRef | None:
    if not isinstance(relation, dict) or relation.get("type") != "blocks":
        return None
    issue = relation.get("issue")
    if not isinstance(issue, dict):
        return None
    state = issue.get("state") or {}
    return BlockerRef(
        id=str(issue.get("id") or ""),
        identifier=str(issue.get("identifier") or ""),
        state=state.get("name"),
    )
