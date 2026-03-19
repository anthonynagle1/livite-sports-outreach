"""In-memory TTL cache — same pattern as tools/hub/__init__.py."""

import time
import threading

_cache = {}
_lock = threading.Lock()


def cache_get(key, ttl=300):
    """Get cached value if it exists and hasn't expired."""
    with _lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry['ts'] < ttl):
            return entry['data']
    return None


def cache_set(key, data):
    """Store a value in the cache with current timestamp."""
    with _lock:
        _cache[key] = {'data': data, 'ts': time.time()}


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
