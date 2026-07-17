from __future__ import annotations

import json
import re

from symphonz.service.reporting import report_tool_spec


_NAME_PATTERN = re.compile(r"[_A-Za-z][_0-9A-Za-z]*")


def linear_graphql_tool_spec() -> dict:
    return {
        "name": "linear_graphql",
        "description": "Execute one Linear GraphQL query or mutation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "variables": {"type": "object"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def dynamic_tool_specs(*, report_publisher=None) -> list[dict]:
    """Advertise the stable Linear and report dynamic-tool contracts."""
    return [linear_graphql_tool_spec(), report_tool_spec()]


def execute_dynamic_tool(tool_name: str, arguments: object, *, linear_client, report_publisher) -> dict:
    """Dispatch an advertised dynamic tool without allowing arbitrary tool names."""
    if tool_name == "linear_graphql":
        if linear_client is None:
            return _failure("Linear client is unavailable.")
        return execute_linear_graphql(linear_client, arguments)
    if tool_name == "symphonz_report":
        if report_publisher is None:
            return _failure("Report publisher is unavailable.")
        try:
            body = report_publisher.publish(arguments)
        except Exception as exc:
            return _failure(str(exc))
        output = json.dumps(body)
        return {"success": bool(body.get("success")), "output": output, "contentItems": [{"type": "inputText", "text": output}]}
    return _failure("Unsupported dynamic tool.")


def execute_linear_graphql(client, arguments: object) -> dict:
    if not isinstance(arguments, dict):
        return _failure("Linear GraphQL arguments must be an object.")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _failure("Linear GraphQL query must be a non-empty string.")
    if not _has_one_operation(query):
        return _failure("Linear GraphQL requests must contain exactly one operation.")
    variables = arguments.get("variables", {})
    if not isinstance(variables, dict):
        return _failure("Linear GraphQL variables must be an object.")
    try:
        body = client.graphql(query, variables)
    except Exception as exc:
        return _failure(str(exc))
    output = json.dumps(body)
    if isinstance(body, dict) and body.get("errors"):
        return {
            "success": False,
            "output": output,
            "contentItems": [{"type": "inputText", "text": output}],
        }
    return {
        "success": True,
        "output": output,
        "contentItems": [{"type": "inputText", "text": output}],
    }


def _has_one_operation(query: str) -> bool:
    source = _strip_ignored_graphql_text(query)
    position = 0
    operations = 0
    while True:
        position = _skip_whitespace(source, position)
        if position >= len(source):
            break
        name = _read_name(source, position)
        if name is None:
            return False
        token, next_position = name
        if token in {"query", "mutation"}:
            operations += 1
            selection_start = _find_selection_set_start(source, next_position)
            if selection_start is None:
                return False
            if not _selection_set_has_content(source, selection_start):
                return False
            position = _consume_balanced_block(source, selection_start, "{", "}")
            if position is None:
                return False
            continue
        if token == "fragment":
            selection_start = _find_selection_set_start(source, next_position)
            if selection_start is None:
                return False
            if not _selection_set_has_content(source, selection_start):
                return False
            position = _consume_balanced_block(source, selection_start, "{", "}")
            if position is None:
                return False
            continue
        return False
    return operations == 1


def _strip_ignored_graphql_text(source: str) -> str:
    result: list[str] = []
    position = 0
    while position < len(source):
        if source.startswith('"""', position):
            position = _consume_string(source, position + 3, '"""')
            result.append(" ")
            continue
        char = source[position]
        if char == "#":
            while position < len(source) and source[position] != "\n":
                position += 1
            continue
        if char == '"':
            position = _consume_string(source, position + 1, '"')
            result.append(" ")
            continue
        result.append(char)
        position += 1
    return "".join(result)


def _consume_string(source: str, position: int, delimiter: str) -> int:
    while position < len(source):
        if delimiter == '"' and source[position] == "\\":
            position += 2
            continue
        if source.startswith(delimiter, position):
            return position + len(delimiter)
        position += 1
    return position


def _skip_whitespace(source: str, position: int) -> int:
    while position < len(source) and source[position] in " \t\r\n,":
        position += 1
    return position


def _read_name(source: str, position: int) -> tuple[str, int] | None:
    match = _NAME_PATTERN.match(source, position)
    if match is None:
        return None
    return match.group(0), match.end()


def _find_selection_set_start(source: str, position: int) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    while position < len(source):
        char = source[position]
        if char == "(":
            paren_depth += 1
        elif char == ")":
            if paren_depth == 0:
                return None
            paren_depth -= 1
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            if bracket_depth == 0:
                return None
            bracket_depth -= 1
        elif char == "{" and paren_depth == 0 and bracket_depth == 0:
            return position
        position += 1
    return None


def _selection_set_has_content(source: str, opening_brace: int) -> bool:
    position = _skip_whitespace(source, opening_brace + 1)
    return position < len(source) and source[position] != "}"


def _consume_balanced_block(source: str, position: int, opening: str, closing: str) -> int | None:
    depth = 0
    while position < len(source):
        char = source[position]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def _failure(message: str) -> dict:
    return {"success": False, "output": message, "contentItems": []}
