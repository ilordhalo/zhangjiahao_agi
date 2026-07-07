#!/usr/bin/env python3
"""Validate the AGI interactive briefing static site."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "index.html",
    "src/styles.css",
    "src/main.js",
    "docs/research/agi-related-ai-progress-2026-07.md",
    "docs/PRD.md",
    "docs/ARCH.md",
    "docs/ROADMAP.md",
]

REQUIRED_SECTIONS = [
    "hero",
    "ranking",
    "domains",
    "signals",
    "uncertainty",
    "sources",
]

REQUIRED_TERMS = [
    "长周期智能体",
    "推理时扩展",
    "世界模型",
    "具身智能",
    "安全评估",
    "ARC-AGI",
    "METR",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read(path: str) -> str:
    file_path = ROOT / path
    if not file_path.is_file():
        fail(f"missing required file: {path}")
    return file_path.read_text(encoding="utf-8")


def require(text: str, pattern: str, message: str) -> None:
    if not re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
        fail(message)


def main() -> None:
    for path in REQUIRED_FILES:
        if not (ROOT / path).is_file():
            fail(f"missing required file: {path}")

    html = read("index.html")
    css = read("src/styles.css")
    js = read("src/main.js")
    report = read("docs/research/agi-related-ai-progress-2026-07.md")
    docs = "\n".join(read(path) for path in ["docs/PRD.md", "docs/ARCH.md", "docs/ROADMAP.md"])

    require(html, r"<html[^>]+lang=[\"']zh-CN[\"']", "index.html must declare zh-CN language")
    require(html, r"<title>[^<]*AGI", "index.html title must mention AGI")
    require(html, r"<link[^>]+href=[\"']src/styles.css[\"']", "index.html must load src/styles.css")
    require(html, r"<script[^>]+src=[\"']src/main.js[\"'][^>]*defer", "index.html must defer-load src/main.js")

    for section in REQUIRED_SECTIONS:
        require(html, rf"id=[\"']{section}[\"']", f"missing section id: {section}")

    for term in REQUIRED_TERMS:
        if term not in html + js + report:
            fail(f"missing AGI source term: {term}")

    require(html, r"<button[^>]+data-filter=", "site must include filter buttons")
    require(html, r"aria-pressed=", "filter buttons must expose aria-pressed state")
    require(html, r"aria-live=", "site must expose live update text for interactions")
    require(css, r"@media\s*\(prefers-reduced-motion:\s*reduce\)", "CSS must support reduced motion")
    require(css, r"@keyframes\s+", "CSS must define animation keyframes")
    require(js, r"addEventListener\([\"']click[\"']", "JS must handle click interactions")
    require(js, r"IntersectionObserver", "JS must progressively reveal sections")
    require(js, r"const\s+domainData\s*=", "JS must define domainData")
    require(js, r"const\s+signalData\s*=", "JS must define signalData")

    link_count = len(re.findall(r"https://", html + report))
    if link_count < 12:
        fail(f"expected at least 12 https source links, found {link_count}")

    for doc_heading in ["# PRD", "# Architecture", "# Roadmap"]:
        if doc_heading not in docs:
            fail(f"project docs missing heading: {doc_heading}")

    print("PASS: AGI static site structure, interactions, docs, and source traceability validated.")


if __name__ == "__main__":
    main()
