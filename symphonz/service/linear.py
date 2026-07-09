from __future__ import annotations

import json
import urllib.request

from symphonz.service.models import Issue


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

POLL_QUERY = """
query SymphonzPoll($projectSlug: String!, $stateNames: [String!]!, $first: Int!) {
  issues(filter: {project: {slugId: {eq: $projectSlug}}, state: {name: {in: $stateNames}}}, first: $first) {
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
      createdAt
      updatedAt
    }
  }
}
"""

ISSUES_BY_ID_QUERY = """
query SymphonzIssuesById($ids: [ID!]!, $first: Int!) {
  issues(filter: {id: {in: $ids}}, first: $first) {
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
      createdAt
      updatedAt
    }
  }
}
"""


class LinearClient:
    def __init__(self, api_key: str, project_slug: str, endpoint: str = LINEAR_GRAPHQL_URL):
        self.api_key = api_key
        self.project_slug = project_slug
        self.endpoint = endpoint

    def fetch_candidate_issues(self, active_states: list[str]) -> list[Issue]:
        body = self.graphql(
            POLL_QUERY,
            {
                "projectSlug": self.project_slug,
                "stateNames": active_states,
                "first": 50,
            },
        )
        return normalize_issue_nodes(body)

    def fetch_issues_by_ids(self, ids: list[str]) -> list[Issue]:
        if not ids:
            return []
        body = self.graphql(ISSUES_BY_ID_QUERY, {"ids": ids, "first": len(ids)})
        return normalize_issue_nodes(body)

    def graphql(self, query: str, variables: dict | None = None) -> dict:
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
    )

