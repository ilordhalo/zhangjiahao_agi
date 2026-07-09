from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Any

from symphonz.service.models import Issue, WorkflowDefinition


def load_workflow(path: Path) -> WorkflowDefinition:
    content = path.read_text()
    config_text, prompt_template = split_front_matter(content)
    return WorkflowDefinition(path=path, config=parse_yaml_subset(config_text), prompt_template=prompt_template.strip())


def split_front_matter(content: str) -> tuple[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", content

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])

    return "\n".join(lines[1:]), ""


def parse_yaml_subset(text: str) -> dict:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            index += 1
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if line.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"List item without list parent: {raw}")
            parent.append(parse_scalar(line[2:].strip()))
            index += 1
            continue

        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"Invalid workflow YAML line: {raw}")
        key = key.strip()
        value = value.strip()

        if value == "|":
            block_lines: list[str] = []
            block_indent: int | None = None
            index += 1
            while index < len(lines):
                block_raw = lines[index]
                if block_raw.strip():
                    current_indent = len(block_raw) - len(block_raw.lstrip(" "))
                    if current_indent <= indent:
                        break
                    if block_indent is None:
                        block_indent = current_indent
                    block_lines.append(block_raw[min(block_indent, len(block_raw)) :])
                else:
                    block_lines.append("")
                index += 1
            parent[key] = "\n".join(block_lines).rstrip() + "\n"
            continue

        if value:
            parent[key] = parse_scalar(value)
            index += 1
            continue

        next_line = next_non_empty_line(lines, index + 1)
        child: Any = [] if next_line and next_line[1].strip().startswith("- ") else {}
        parent[key] = child
        stack.append((indent, child))
        index += 1

    return root


def next_non_empty_line(lines: list[str], start: int) -> tuple[int, str] | None:
    for index in range(start, len(lines)):
        if lines[index].strip():
            return index, lines[index]
    return None


def parse_scalar(value: str) -> Any:
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def render_prompt(template: str, issue: Issue, attempt: int | None = None) -> str:
    context = {
        "issue": asdict(issue),
        "attempt": attempt,
    }
    rendered = render_conditionals(template, context)
    rendered = render_variables(rendered, context)
    return rendered


def render_conditionals(template: str, context: dict[str, Any]) -> str:
    pattern = re.compile(r"{%\s*if\s+([^%]+?)\s*%}(.*?)(?:{%\s*else\s*%}(.*?))?{%\s*endif\s*%}", re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        truthy = bool(resolve_path(expression, context))
        return match.group(2) if truthy else (match.group(3) or "")

    previous = None
    current = template
    while previous != current:
        previous = current
        current = pattern.sub(replace, current)
    return current


def render_variables(template: str, context: dict[str, Any]) -> str:
    pattern = re.compile(r"{{\s*([^}]+?)\s*}}")

    def replace(match: re.Match[str]) -> str:
        value = resolve_path(match.group(1).strip(), context)
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    return pattern.sub(replace, template)


def resolve_path(path: str, context: dict[str, Any]) -> Any:
    value: Any = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
        if value is None:
            return None
    return value

