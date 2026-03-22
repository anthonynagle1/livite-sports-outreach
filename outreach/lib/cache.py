"""In-memory cache with stale-while-revalidate support.

Serves stale data instantly while refreshing in the background.
This eliminates perceived latency for repeat requests.
"""

import time
import threading
import logging

logger = logging.getLogger(__name__)

_cache = {}
_lock = threading.Lock()
_refreshing = set()  # Keys currently being refreshed


def cache_get(key, ttl=600, stale_ttl=1800):
    """Get cached value. Returns data even if stale (up to stale_ttl).

    Returns (data, is_stale) tuple if found, (None, False) if not cached.
    """
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None, False

        age = time.time() - entry['ts']

        if age < ttl:
            # Fresh
            return entry['data'], False
        elif age < stale_ttl:
            # Stale but servable
            return entry['data'], True
        else:
            # Too old
            return None, False


def cache_get_simple(key, ttl=600):
    """Simple cache_get for backward compat — returns data or None."""
    data, _ = cache_get(key, ttl)
    return data


def cache_set(key, data):
    """Store a value in the cache with current timestamp."""
    with _lock:
        _cache[key] = {'data': data, 'ts': time.time()}
        _refreshing.discard(key)


def cache_age(key):
    """Return seconds since the cache entry was set, or None."""
    with _lock:
        entry = _cache.get(key)
        return int(time.time() - entry['ts']) if entry else None


def cache_clear(key=None):
    """Clear one key or the entire cache."""
    with _lock:
        if key:
            _cache.pop(key, None)
        else:
            _cache.clear()


def needs_refresh(key):
    """Check if a key is already being refreshed (prevents duplicate work)."""
    with _lock:
        if key in _refreshing:
            return False
        _refreshing.add(key)
        return True


# ── Persistent school name cache (survives cache_clear) ──

_school_names = {}
_school_lock = threading.Lock()


def get_school_name(school_id):
    """Get school name from persistent cache, or fetch and cache it."""
    with _school_lock:
        if school_id in _school_names:
            return _school_names[school_id]

    # Fetch from Notion
    from .notion import resolve_school_name
    name = resolve_school_name(school_id)

    with _school_lock:
        _school_names[school_id] = name

    return name


# ── Persistent contact summary cache (survives cache_clear) ──

_contacts = {}
_contact_lock = threading.Lock()


def get_contact_summary(contact_id):
    """Get contact summary from persistent cache, or fetch and cache it."""
    with _contact_lock:
        if contact_id in _contacts:
            return _contacts[contact_id]

    from .notion import resolve_contact_summary_with_response
    summary = resolve_contact_summary_with_response(contact_id)

    with _contact_lock:
        _contacts[contact_id] = summary

    return summary


def invalidate_contact(contact_id=None):
    """Invalidate one or all contact summaries (call after response type updates)."""
    with _contact_lock:
        if contact_id:
            _contacts.pop(contact_id, None)
        else:
            _contacts.clear()
