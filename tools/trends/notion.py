"""Store and retrieve trend reports in Notion."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_TRENDS_PARENT_PAGE_ID = os.getenv("NOTION_TRENDS_PARENT_PAGE_ID", "")

# In-memory cache for recent reports list
_cache = {"ts": 0.0, "data": None}
_CACHE_TTL = 300  # 5 minutes


def _get_notion():
    """Lazy-load Notion client."""
    if not NOTION_API_KEY:
        return None
    from notion_client import Client
    return Client(auth=NOTION_API_KEY)


def save_report_to_notion(
    report_md: str,
    analysis: dict,
    search_terms: list,
    post_counts: dict,
) -> str:
    """Create a Notion page with the trend report.

    Returns the page URL on success, empty string on failure.
    """
    notion = _get_notion()
    if not notion or not NOTION_TRENDS_PARENT_PAGE_ID:
        logger.warning("Notion not configured for trend reports")
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    terms_str = ", ".join(search_terms[:3])
    title = f"Trend Report — {terms_str} — {today}"

    blocks = _markdown_to_blocks(report_md)

    try:
        page = notion.pages.create(
            parent={"page_id": NOTION_TRENDS_PARENT_PAGE_ID},
            properties={
                "title": [{"text": {"content": title}}]
            },
            children=blocks[:100],  # Notion API limit: 100 blocks per create
        )
        page_id = page["id"]
        page_url = page.get("url", "")

        # Append remaining blocks in batches if > 100
        if len(blocks) > 100:
            for i in range(100, len(blocks), 100):
                notion.blocks.children.append(
                    block_id=page_id,
                    children=blocks[i:i + 100],
                )

        # Invalidate cache
        _cache["ts"] = 0

        logger.info("Saved trend report to Notion: %s", page_url)
        return page_url
    except Exception as e:
        logger.error("Failed to save trend report to Notion: %s", e)
        return ""


def get_recent_reports(limit: int = 10) -> list:
    """Fetch recent trend report pages from Notion."""
    if _cache["data"] is not None and (time.time() - _cache["ts"] < _CACHE_TTL):
        return _cache["data"][:limit]

    notion = _get_notion()
    if not notion or not NOTION_TRENDS_PARENT_PAGE_ID:
        return []

    try:
        children = notion.blocks.children.list(
            block_id=NOTION_TRENDS_PARENT_PAGE_ID,
            page_size=50,
        )
        reports = []
        for block in children.get("results", []):
            if block["type"] == "child_page":
                bid = block["id"].replace("-", "")
                reports.append({
                    "title": block["child_page"]["title"],
                    "id": block["id"],
                    "url": f"https://notion.so/{bid}",
                    "created_time": block.get("created_time", ""),
                })
        reports.sort(key=lambda r: r["created_time"], reverse=True)

        _cache["data"] = reports
        _cache["ts"] = time.time()

        return reports[:limit]
    except Exception as e:
        logger.error("Failed to fetch trend reports from Notion: %s", e)
        return []


def _markdown_to_blocks(md: str) -> list:
    """Convert markdown report to Notion block objects."""
    blocks = []
    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("### "):
            blocks.append({
                "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": stripped[4:]}}]}
            })
        elif stripped.startswith("## "):
            blocks.append({
                "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": stripped[3:]}}]}
            })
        elif stripped.startswith("# "):
            blocks.append({
                "type": "heading_1",
                "heading_1": {"rich_text": [{"text": {"content": stripped[2:]}}]}
            })
        elif stripped.startswith("- [ ] "):
            blocks.append({
                "type": "to_do",
                "to_do": {
                    "rich_text": [{"text": {"content": stripped[6:]}}],
                    "checked": False,
                }
            })
        elif stripped.startswith("- "):
            blocks.append({
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"text": {"content": stripped[2:][:2000]}}]
                }
            })
        else:
            blocks.append({
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": stripped[:2000]}}]
                }
            })
    return blocks
