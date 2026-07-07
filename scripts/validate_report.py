#!/usr/bin/env python3
"""Validate the AGI research report structure and citation hygiene."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_HEADINGS = [
    "# 当前AI领域最新进展：哪些新东西和AGI更相关",
    "## 执行摘要",
    "## AGI相关性排序",
    "## 主要进展",
    "## 哪些新东西更接近AGI",
    "## 不确定性与反信号",
    "## 未来6-12个月观察指标",
    "## 参考资料",
]

REQUIRED_TERMS = [
    "长周期智能体",
    "推理时扩展",
    "世界模型",
    "机器人",
    "安全评估",
    "ARC-AGI",
    "Humanity's Last Exam",
    "METR",
]

TRUSTED_DOMAINS = {
    "openai.com",
    "deploymentsafety.openai.com",
    "anthropic.com",
    "platform.claude.com",
    "deepmind.google",
    "blog.google",
    "gemini.google",
    "ai.meta.com",
    "alibabacloud.com",
    "arxiv.org",
    "metr.org",
    "arcprize.org",
    "agi.safe.ai",
    "isomorphiclabs.com",
    "nvidianews.nvidia.com",
    "blog.google",
    "aisi.gov.uk",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: validate_report.py <report.md>")

    report_path = Path(sys.argv[1])
    if not report_path.is_file():
        fail(f"report does not exist: {report_path}")

    text = report_path.read_text(encoding="utf-8")

    if "2026-07-07" not in text:
        fail("report must include the dated scope 2026-07-07")

    forbidden = ["TODO", "TBD", "FIXME"]
    for token in forbidden:
        if token in text:
            fail(f"report contains unfinished marker: {token}")

    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            fail(f"missing required heading: {heading}")

    for term in REQUIRED_TERMS:
        if term not in text:
            fail(f"missing required term: {term}")

    urls = re.findall(r"https?://[^\s)\]>\"']+", text)
    unique_urls = sorted(set(urls))
    if len(unique_urls) < 20:
        fail(f"expected at least 20 unique source URLs, found {len(unique_urls)}")

    trusted_count = 0
    for url in unique_urls:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            fail(f"non-https URL found: {url}")
        host = parsed.netloc.lower()
        host = host[4:] if host.startswith("www.") else host
        if any(host == domain or host.endswith(f".{domain}") for domain in TRUSTED_DOMAINS):
            trusted_count += 1

    if trusted_count < 16:
        fail(f"expected at least 16 trusted-domain source URLs, found {trusted_count}")

    score_rows = re.findall(r"\|\s*\d+\s*\|", text)
    if len(score_rows) < 7:
        fail("AGI relevance ranking table should contain at least 7 ranked rows")

    print(
        "PASS: report structure, dated scope, AGI themes, and citation hygiene validated "
        f"({len(unique_urls)} unique URLs, {trusted_count} trusted-domain URLs)."
    )


if __name__ == "__main__":
    main()
