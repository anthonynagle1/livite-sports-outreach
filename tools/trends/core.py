"""Trend Scout core — Instagram food trend scraper + Claude analysis."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


# ---------------------------------------------------------------------------
# Apify scraping
# ---------------------------------------------------------------------------

def _term_to_hashtag(term: str) -> str:
    """Convert search term to Instagram hashtag (no spaces, lowercase)."""
    return re.sub(r"\s+", "", term.lower())


def scrape_hashtag(term: str, results_limit: int = 30) -> list:
    """Scrape Instagram posts for a search term via Apify.

    Uses instagram-scraper with search mode (algorithmically ranked results,
    like Instagram's Explore page) instead of the hashtag-scraper which only
    returns low-engagement "Recent" feed posts.

    Oversamples 3x and returns top posts sorted by engagement.
    """
    from apify_client import ApifyClient

    client = ApifyClient(APIFY_API_TOKEN)
    hashtag = _term_to_hashtag(term)
    # Oversample to get a larger pool, then pick top by engagement
    fetch_limit = max(results_limit * 3, 50)
    logger.info("Scraping #%s (fetching %d, returning top %d)...",
                hashtag, fetch_limit, results_limit)

    run = client.actor("apify/instagram-scraper").call(
        run_input={
            "search": hashtag,
            "searchType": "hashtag",
            "searchLimit": 1,
            "resultsType": "posts",
            "resultsLimit": fetch_limit,
        }
    )

    posts = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        likes = item.get("likesCount", 0) or 0
        comments = item.get("commentsCount", 0) or 0
        posts.append({
            "caption": (item.get("caption") or "")[:2000],
            "likes": likes,
            "comments": comments,
            "timestamp": item.get("timestamp", ""),
            "hashtags": item.get("hashtags", []),
            "image_url": item.get("displayUrl", ""),
            "owner": item.get("ownerUsername", ""),
            "url": item.get("url", ""),
            "engagement": likes + comments,
        })

    # Sort by engagement (likes + comments) descending, return top N
    posts.sort(key=lambda p: p["engagement"], reverse=True)
    posts = posts[:results_limit]

    total_fetched = len(posts)
    logger.info("Got %d top posts for #%s (sorted by engagement)", total_fetched, hashtag)
    return posts


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a culinary trend analyst for Livite, a fast-casual healthy restaurant "
    "in Brookline, MA. You analyze Instagram food content to identify actionable trends, "
    "recipe ideas, and menu inspiration.\n\n"
    "Focus on:\n"
    "- Practical recipe ideas the kitchen team can test\n"
    "- Sauce and condiment trends (new flavors, combinations, techniques)\n"
    "- Plating and presentation trends\n"
    "- Ingredient trends gaining traction\n"
    "- What's getting the most engagement and why\n\n"
    "Be specific and actionable. The kitchen team needs concrete ideas, not vague observations."
)


def build_analysis_prompt(search_terms: list, all_posts: dict) -> str:
    """Build the user prompt with all scraped data grouped by search term."""
    sections = []
    for term in search_terms:
        posts = all_posts.get(term, [])
        if not posts:
            continue
        lines = [f"\n## #{_term_to_hashtag(term)} ({len(posts)} posts)\n"]
        for i, p in enumerate(posts, 1):
            caption = p["caption"][:500] if p["caption"] else "(no caption)"
            lines.append(
                f"{i}. [{p['likes']} likes, {p['comments']} comments] "
                f"@{p['owner']}: {caption}"
            )
        sections.append("\n".join(lines))

    return (
        "Analyze these Instagram posts scraped from food-related hashtags. "
        "Extract trends, recipe ideas, and actionable insights for our restaurant.\n\n"
        "Return your analysis as JSON with these exact keys:\n"
        '- "summary": 2-3 sentence trend overview\n'
        '- "trending_themes": [{"theme": str, "description": str, "strength": "hot|warm|emerging"}]\n'
        '- "recipe_ideas": [{"name": str, "description": str, "why_trending": str, "difficulty": "easy|medium|hard"}]\n'
        '- "sauce_spotlight": [{"name": str, "description": str, "pairs_with": str}]\n'
        '- "ingredient_watch": [{"ingredient": str, "trend": str, "usage_ideas": str}]\n'
        '- "top_posts": [{"owner": str, "caption_snippet": str, "likes": int, "why_notable": str}]\n'
        '- "action_items": [str] — concrete next steps for the kitchen team\n\n'
        + "".join(sections)
    )


def analyze_with_claude(user_prompt: str) -> dict:
    """Send scraped data to Claude for analysis, return parsed JSON."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("Analyzing with Claude...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text
    return _parse_json_response(text)


def _parse_json_response(text: str) -> dict:
    """Extract JSON from Claude response (handles markdown code fences)."""
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise ValueError(f"Could not parse JSON from response:\n{text[:500]}")


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(analysis: dict, search_terms: list, post_counts: dict) -> str:
    """Convert Claude's JSON analysis into a readable markdown report."""
    today = datetime.now().strftime("%Y-%m-%d")
    terms_str = ", ".join(f"#{_term_to_hashtag(t)}" for t in search_terms)
    total_posts = sum(post_counts.values())

    lines = [
        f"# Livite Trend Scout Report — {today}",
        f"**Search terms:** {terms_str}",
        f"**Posts analyzed:** {total_posts}",
        "",
    ]

    if summary := analysis.get("summary"):
        lines += ["## Summary", summary, ""]

    if themes := analysis.get("trending_themes"):
        lines.append("## Trending Themes")
        for t in themes:
            strength = t.get("strength", "").upper()
            lines.append(f"- **{t['theme']}** [{strength}]: {t['description']}")
        lines.append("")

    if recipes := analysis.get("recipe_ideas"):
        lines.append("## Recipe Ideas")
        for r in recipes:
            diff = r.get("difficulty", "medium")
            lines.append(f"### {r['name']} ({diff})")
            lines.append(f"{r['description']}")
            lines.append(f"*Why trending: {r['why_trending']}*")
            lines.append("")

    if sauces := analysis.get("sauce_spotlight"):
        lines.append("## Sauce Spotlight")
        for s in sauces:
            lines.append(f"- **{s['name']}**: {s['description']} — pairs with {s.get('pairs_with', 'various')}")
        lines.append("")

    if ingredients := analysis.get("ingredient_watch"):
        lines.append("## Ingredient Watch")
        for i in ingredients:
            lines.append(f"- **{i['ingredient']}**: {i['trend']} — {i.get('usage_ideas', '')}")
        lines.append("")

    if top := analysis.get("top_posts"):
        lines.append("## Top Posts Worth Checking")
        for p in top:
            lines.append(f"- @{p['owner']} ({p.get('likes', '?')} likes): {p.get('why_notable', '')}")
        lines.append("")

    if actions := analysis.get("action_items"):
        lines.append("## Action Items")
        for a in actions:
            lines.append(f"- [ ] {a}")
        lines.append("")

    return "\n".join(lines)
