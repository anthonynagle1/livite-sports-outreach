"""Google Review Monitor — fetch reviews via Outscraper API."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
GOOGLE_PLACE_ID = os.getenv("GOOGLE_PLACE_ID", "")

# In-memory cache (5 min TTL)
_cache: dict = {"reviews": [], "fetched_at": 0}
CACHE_TTL = 300  # seconds


def fetch_reviews(limit: int = 50, force: bool = False) -> list[dict]:
    """Fetch Google reviews via Outscraper API with caching.

    Returns list of normalized review dicts sorted newest-first.
    """
    now = time.time()
    if not force and _cache["reviews"] and (now - _cache["fetched_at"]) < CACHE_TTL:
        logger.info("Returning %d cached reviews", len(_cache["reviews"]))
        return _cache["reviews"]

    if not OUTSCRAPER_API_KEY:
        logger.warning("OUTSCRAPER_API_KEY not set — returning empty reviews")
        return []

    query = GOOGLE_PLACE_ID if GOOGLE_PLACE_ID else "Livite Brookline MA"
    url = "https://api.app.outscraper.com/maps/reviews-v3"
    params = {
        "query": query,
        "reviewsLimit": limit,
        "async": "false",
        "sort": "newest",
        "language": "en",
    }
    headers = {"X-API-KEY": OUTSCRAPER_API_KEY}

    try:
        logger.info("Fetching reviews from Outscraper (limit=%d)", limit)
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Outscraper API error: %s", e)
        return _cache["reviews"]  # return stale cache on error

    reviews = _parse_response(data)
    _cache["reviews"] = reviews
    _cache["fetched_at"] = now
    logger.info("Fetched %d reviews from Outscraper", len(reviews))
    return reviews


def _parse_response(data: dict) -> list[dict]:
    """Normalize Outscraper response into clean review dicts."""
    reviews = []
    # Outscraper returns {"data": [[{place_info + reviews_data}]]}
    results = data.get("data", [])
    if not results:
        return []

    # First result set, first place
    place_data = results[0] if results else []
    if not place_data:
        return []

    place = place_data[0] if isinstance(place_data, list) and place_data else place_data
    reviews_data = place.get("reviews_data", [])

    for r in reviews_data:
        review = {
            "author": r.get("autor_name", "Anonymous"),
            "author_image": r.get("autor_image", ""),
            "author_link": r.get("autor_link", ""),
            "rating": r.get("review_rating", 0),
            "text": r.get("review_text", ""),
            "date": r.get("review_datetime_utc", ""),
            "timestamp": r.get("review_timestamp", 0),
            "likes": r.get("review_likes", 0),
            "link": r.get("review_link", ""),
            "response_text": r.get("owner_answer") or "",
            "response_date": r.get("owner_answer_timestamp_datetime_utc") or "",
        }
        reviews.append(review)

    # Sort newest first
    reviews.sort(key=lambda x: x["timestamp"], reverse=True)
    return reviews


def get_review_stats(reviews: list[dict]) -> dict:
    """Compute aggregate stats from reviews."""
    if not reviews:
        return {
            "avg_rating": 0,
            "total": 0,
            "distribution": {5: 0, 4: 0, 3: 0, 2: 0, 1: 0},
            "response_rate": 0,
            "needs_response": 0,
            "recent_avg": 0,
            "prior_avg": 0,
            "trend": "stable",
        }

    total = len(reviews)
    avg_rating = sum(r["rating"] for r in reviews) / total

    distribution = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    for r in reviews:
        rating = min(max(int(r["rating"]), 1), 5)
        distribution[rating] += 1

    responded = sum(1 for r in reviews if r["response_text"])
    response_rate = (responded / total * 100) if total else 0
    needs_response = total - responded

    # 30-day trend vs prior 30 days
    now = time.time()
    thirty_days = 30 * 86400
    recent = [r for r in reviews if r["timestamp"] and (now - r["timestamp"]) < thirty_days]
    prior = [r for r in reviews if r["timestamp"] and thirty_days <= (now - r["timestamp"]) < (sixty_days := 60 * 86400)]

    recent_avg = (sum(r["rating"] for r in recent) / len(recent)) if recent else 0
    prior_avg = (sum(r["rating"] for r in prior) / len(prior)) if prior else 0

    if recent_avg > prior_avg + 0.2:
        trend = "improving"
    elif recent_avg < prior_avg - 0.2:
        trend = "declining"
    else:
        trend = "stable"

    return {
        "avg_rating": round(avg_rating, 2),
        "total": total,
        "distribution": distribution,
        "response_rate": round(response_rate, 1),
        "needs_response": needs_response,
        "recent_avg": round(recent_avg, 2),
        "prior_avg": round(prior_avg, 2),
        "recent_count": len(recent),
        "trend": trend,
    }
