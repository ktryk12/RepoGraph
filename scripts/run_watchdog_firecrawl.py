"""
scripts/run_watchdog_firecrawl.py — WatchdogAgent + Firecrawl live demo.

Scrapes live sources for each verified topic, runs policy gates,
and shows what the agent actually finds.

Usage:
    FIRECRAWL_API_KEY=fc-... .venv/Scripts/python scripts/run_watchdog_firecrawl.py
    # Without key: runs in stub mode and shows YAML-sourced facts only
"""
from __future__ import annotations

import io
import os
import sys
import textwrap
from pathlib import Path

# Force UTF-8 output on Windows so scraped Unicode content prints cleanly
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools.firecrawl_client import FirecrawlClient
from agents.watchdog_agent import WatchdogAgent

_DIVIDER = "=" * 70
_SEP     = "-" * 70
_HAS_KEY = (
    bool(os.getenv("FIRECRAWL_API_KEY", "").strip())
    or bool(os.getenv("FIRECRAWL_API_URL", "").strip())
)
_BACKEND = (
    f"self-hosted ({os.getenv('FIRECRAWL_API_URL')})"
    if os.getenv("FIRECRAWL_API_URL")
    else f"cloud (key set)" if os.getenv("FIRECRAWL_API_KEY")
    else "NOT SET (stub mode)"
)


def _wrap(text: str, width: int = 68, indent: str = "  ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


def scrape_topic_sources(
    client: FirecrawlClient,
    topic: dict,
) -> list[dict]:
    """Scrape each named source URL in the topic and return findings."""
    findings = []
    for src in topic.get("sources", []):
        url = src.get("url", "") if isinstance(src, dict) else ""
        if not url:
            continue
        result = client.scrape_page(url)
        if result and result.get("content"):
            findings.append({
                "source_name": src.get("name", url),
                "source_type": src.get("type", "unknown"),
                "url":         url,
                "excerpt":     result["content"][:500].strip(),
            })
    return findings


def search_recent_coverage(
    client: FirecrawlClient,
    topic: dict,
) -> list[dict]:
    """Search for recent web coverage of the scandal."""
    query = f"{topic['title']} scandal facts verdict"
    results = client.search_and_scrape(query, limit=3)
    findings = []
    for r in results:
        if r.get("content"):
            findings.append({
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "excerpt": r["content"][:400].strip(),
            })
    return findings


def run_demo() -> None:
    agent  = WatchdogAgent()
    client = FirecrawlClient()

    print(_DIVIDER)
    print("  WATCHDOG AGENT — Live Firecrawl Demo")
    print(f"  Firecrawl backend: {_BACKEND}")
    print(_DIVIDER)
    print()

    topics = list(agent._topics.values())
    if not topics:
        print("ERROR: No topics loaded from config/watchdog_topics.yaml")
        return

    print(f"Loaded {len(topics)} verified topics from YAML.\n")

    for topic in topics:
        topic_id = topic["id"]
        print(_DIVIDER)
        print(f"  TOPIC: {topic['title']}")
        print(f"  ID: {topic_id}  |  Confidence: {topic.get('confidence', '?')}  |  Verdict: {topic.get('final_judgment', '?')}")
        print(_SEP)

        # 1. Policy-gated research (from YAML facts)
        result = agent.research_topic(topic_id)

        if result.policy_violations:
            print(f"  [BLOCKED] Policy violations: {result.policy_violations}")
            print()
            continue

        print(f"  Verified claims from YAML: {len(result.facts)}")
        for i, claim in enumerate(result.facts, 1):
            print(f"\n  [{i}] {claim.text}")
            print(f"      confidence={claim.confidence:.0%}  sources={len(claim.sources)}")

        # 2. Live source scraping via Firecrawl
        print(f"\n  -- Live source scrape ({len(topic.get('sources', []))} source URLs) --")
        if _HAS_KEY:
            source_findings = scrape_topic_sources(client, topic)
            if source_findings:
                for f in source_findings:
                    print(f"\n  [{f['source_type'].upper()}] {f['source_name'][:60]}")
                    print(f"  URL: {f['url'][:70]}")
                    print(_wrap(f["excerpt"], width=66))
            else:
                print("  (no live content returned from source URLs)")
        else:
            print("  [STUB] Would scrape:")
            for src in topic.get("sources", []):
                if isinstance(src, dict) and src.get("url"):
                    print(f"    {src['type']:12s}  {src['url'][:65]}")

        # 3. Recent web search coverage
        print(f"\n  -- Recent web search coverage --")
        if _HAS_KEY:
            recent = search_recent_coverage(client, topic)
            if recent:
                for r in recent:
                    print(f"\n  [{r['title'][:60]}]")
                    print(f"  {r['url'][:70]}")
                    print(_wrap(r["excerpt"], width=66))
            else:
                print("  (no results from web search)")
        else:
            print(f"  [STUB] Would search: \"{topic['title']} scandal facts verdict\"")

        # 4. Generate YouTube script preview
        print(f"\n  -- YouTube script preview --")
        script = agent.generate_script(
            {"topic_id": topic_id, "title": result.title, "claims": result.facts},
            platform="youtube",
        )
        content_lines = script["content"].split("\n")
        for line in content_lines[:20]:
            print(f"  {line}")
        if len(content_lines) > 20:
            print(f"  ... ({len(content_lines) - 20} more lines)")
        print(f"\n  Chapters: {len(script.get('chapters', []))}")
        print(f"  human_approval_required: {script['metadata']['human_approval_required']}")
        print()

    print(_DIVIDER)
    print("  DEMO COMPLETE")
    if not _HAS_KEY:
        print()
        print("  To run with live Firecrawl scraping:")
        print("  set FIRECRAWL_API_KEY=fc-<your-key> && .venv/Scripts/python scripts/run_watchdog_firecrawl.py")
    print(_DIVIDER)


if __name__ == "__main__":
    run_demo()
