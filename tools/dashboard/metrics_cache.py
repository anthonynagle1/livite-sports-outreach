"""
SQLite-based metrics cache for pre-computed daily metrics.

Stores the full metrics dict as JSON per date. Range dashboards
read from cache instead of re-computing from CSVs (~100ms vs ~5s per day).

Usage:
    from metrics_cache import get_cached_metrics, cache_metrics

    # Check cache first, compute on miss
    metrics = get_cached_metrics("20260217")
    if metrics is None:
        metrics = compute_all_metrics(data, date)
        cache_metrics("20260217", metrics)

    # Batch operations (reuses single connection):
    with batch_connection():
        for ds in date_strings:
            m = get_cached_metrics(ds)
"""

import logging
import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(PROJECT_ROOT, '.tmp', 'metrics_cache.db')

# Thread-local-ish batch connection (Flask is single-threaded per worker)
_batch_conn = None


def _get_db():
    """Get SQLite connection, reusing batch connection if active."""
    if _batch_conn is not None:
        return _batch_conn
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date_str TEXT PRIMARY KEY,
            metrics_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


@contextmanager
def batch_connection():
    """Reuse a single SQLite connection for multiple cache operations."""
    global _batch_conn
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _batch_conn = sqlite3.connect(DB_PATH)
    _batch_conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date_str TEXT PRIMARY KEY,
            metrics_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """)
    _batch_conn.commit()
    try:
        yield _batch_conn
    finally:
        _batch_conn.close()
        _batch_conn = None


def _serialize_metrics(metrics):
    """Convert metrics dict to JSON-safe format."""
    def _convert(obj):
        if isinstance(obj, set):
            return list(obj)
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        if hasattr(obj, 'item'):  # numpy scalar
            return obj.item()
        if hasattr(obj, 'tolist'):  # numpy array
            return obj.tolist()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    return json.dumps(metrics, default=_convert)


def get_cached_metrics(date_str):
    """Get cached metrics for a date. Returns None on cache miss."""
    try:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT metrics_json FROM daily_metrics WHERE date_str = ?",
                (date_str,)
            ).fetchone()
            if row:
                return json.loads(row[0])
        finally:
            if _batch_conn is None:
                conn.close()
    except Exception as e:
        logger.debug("Cache read failed for %s: %s", date_str, e)
    return None


def cache_metrics(date_str, metrics):
    """Store metrics for a date in the cache."""
    try:
        conn = _get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO daily_metrics (date_str, metrics_json, cached_at) VALUES (?, ?, ?)",
                (date_str, _serialize_metrics(metrics), datetime.now().isoformat())
            )
            conn.commit()
        finally:
            if _batch_conn is None:
                conn.close()
    except Exception as e:
        logger.error("Cache write failed for %s: %s", date_str, e)


def invalidate_cache(date_str):
    """Remove cached metrics for a specific date."""
    try:
        conn = _get_db()
        try:
            conn.execute("DELETE FROM daily_metrics WHERE date_str = ?", (date_str,))
            conn.commit()
        finally:
            if _batch_conn is None:
                conn.close()
    except Exception as e:
        logger.debug("Cache invalidation failed for %s: %s", date_str, e)


def cache_stats():
    """Return cache statistics."""
    try:
        conn = _get_db()
        try:
            count = conn.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
            oldest = conn.execute("SELECT MIN(date_str) FROM daily_metrics").fetchone()[0]
            newest = conn.execute("SELECT MAX(date_str) FROM daily_metrics").fetchone()[0]
        finally:
            if _batch_conn is None:
                conn.close()
        return {"count": count, "oldest": oldest, "newest": newest}
    except Exception:
        return {"count": 0, "oldest": None, "newest": None}


def is_today(date_str):
    """Check if date_str is today (today's data may be incomplete)."""
    return date_str == datetime.now().strftime("%Y%m%d")
