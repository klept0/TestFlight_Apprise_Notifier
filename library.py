"""
Library: archive of apps that were removed from monitoring (History), plus a
future-ready Favorites section (backend only).

Persisted to ``data/library.json`` using the shared atomic JSON helpers in
``persistence``. History entries are pure records — they are never monitored,
never send notifications, and never participate in retry/backoff. The Library
is kept entirely separate from runtime state (``data/state.json``).
"""

import os
import threading
import time

import persistence

LIBRARY_FILE = os.getenv("LIBRARY_FILE", "data/library.json")
LIBRARY_VERSION = 1

_lock = threading.Lock()
_history = {}  # tf_id -> archive record
_favorites = []  # future-ready; backend only


def load_from_disk() -> None:
    """Load the Library from disk. Tolerates a missing or corrupt file."""
    global _history, _favorites
    raw = persistence.read_json(LIBRARY_FILE, default={})
    history = raw.get("history", {}) if isinstance(raw, dict) else {}
    favorites = raw.get("favorites", []) if isinstance(raw, dict) else []
    cleaned = {
        tf_id: entry
        for tf_id, entry in (history or {}).items()
        if isinstance(entry, dict)
    }
    with _lock:
        _history = cleaned
        _favorites = favorites if isinstance(favorites, list) else []


def _save() -> None:
    with _lock:
        snapshot = {
            "version": LIBRARY_VERSION,
            "history": {k: dict(v) for k, v in _history.items()},
            "favorites": list(_favorites),
        }
    persistence.atomic_write_json(LIBRARY_FILE, snapshot)


def archive(tf_id, app_name=None, icon_url=None, last_status=None) -> None:
    """Archive an app removed from monitoring.

    Never creates duplicates: if the ID is already in History, the existing
    entry is updated (and ``last_archived`` refreshed) instead.
    """
    now = time.time()
    with _lock:
        existing = _history.get(tf_id)
        if existing:
            existing["last_archived"] = now
            if app_name:
                existing["app_name"] = app_name
            if icon_url:
                existing["icon_url"] = icon_url
            if last_status is not None:
                existing["last_status"] = last_status
        else:
            _history[tf_id] = {
                "id": tf_id,
                "app_name": app_name,
                "icon_url": icon_url,
                "last_status": last_status,
                "first_archived": now,
                "last_archived": now,
            }
    _save()


def get_entry(tf_id):
    """Return a copy of a History entry, or None."""
    with _lock:
        entry = _history.get(tf_id)
        return dict(entry) if entry else None


def get_history(search=None, sort="archived"):
    """Return History entries, optionally filtered by ``search`` and sorted.

    ``sort`` is 'name' (app name A→Z) or 'archived' (most recently archived
    first, the default).
    """
    with _lock:
        items = [dict(v) for v in _history.values()]

    if search:
        s = search.lower()
        items = [
            i
            for i in items
            if s in (i.get("app_name") or "").lower() or s in (i.get("id") or "").lower()
        ]

    if sort == "name":
        items.sort(key=lambda i: (i.get("app_name") or i.get("id") or "").lower())
    else:
        items.sort(key=lambda i: i.get("last_archived") or 0, reverse=True)
    return items


def get_favorites():
    """Return the Favorites list (future-ready; backend only)."""
    with _lock:
        return list(_favorites)


def remove(tf_id) -> bool:
    """Remove an entry from History. Returns True if it existed."""
    with _lock:
        present = tf_id in _history
        if present:
            del _history[tf_id]
    if present:
        _save()
    return present


def clear() -> int:
    """Remove all History entries. Returns the number removed."""
    with _lock:
        count = len(_history)
        _history.clear()
    _save()
    return count
