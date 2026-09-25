"""User preferences, stored as JSON under ~/.config/purge-linux.

Deliberately not GSettings: no schema to compile, no schema to install, and
the file is easy to inspect when someone reports a problem.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from .. import paths

DEFAULTS: dict[str, Any] = {
    "appearance": "system",             # system | light | dark
    "onboarding_complete": False,
    "stale_project_days": 90,           # 0 means "show all"
    "large_file_min_bytes": 100 * 1000 * 1000,
    "journal_keep_size": "200M",
    "scheduled_cleaning_enabled": False,
    "scheduled_cleaning_interval_days": 30,
    "excluded_paths": [],
    "last_scan_at": 0,
    "lifetime_freed_bytes": 0,
    "confirmed_permanent_fallback": False,
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def _config_file() -> str:
    return os.path.join(paths.app_config_dir(), "settings.json")


def load() -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return dict(_CACHE)
        data = dict(DEFAULTS)
        try:
            with open(_config_file(), "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        _CACHE = data
        return dict(data)


def get(key: str, default: Any = None) -> Any:
    return load().get(key, DEFAULTS.get(key, default))


def set_value(key: str, value: Any) -> None:
    global _CACHE
    with _LOCK:
        data = dict(_CACHE) if _CACHE is not None else None
    if data is None:
        data = load()
    data[key] = value
    _write(data)


def update(values: dict[str, Any]) -> None:
    data = load()
    data.update(values)
    _write(data)


def _write(data: dict[str, Any]) -> None:
    global _CACHE
    path = _config_file()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, path)
    except OSError:
        return
    with _LOCK:
        _CACHE = dict(data)


def reset_cache() -> None:
    """Drop the in-memory copy. Used by tests after changing HOME."""
    global _CACHE
    with _LOCK:
        _CACHE = None


# -- excluded paths ---------------------------------------------------------

def excluded_paths() -> set[str]:
    return {os.path.normpath(p) for p in get("excluded_paths", [])}


def exclude_path(path: str) -> None:
    current = excluded_paths()
    current.add(os.path.normpath(path))
    set_value("excluded_paths", sorted(current))


def unexclude_path(path: str) -> None:
    current = excluded_paths()
    current.discard(os.path.normpath(path))
    set_value("excluded_paths", sorted(current))


def is_excluded(path: str) -> bool:
    """Whether a path sits inside any excluded folder.

    Excluding only ever narrows what the app looks at, so a parent exclusion
    covers everything beneath it.
    """
    norm = os.path.normpath(path)
    for excluded in excluded_paths():
        if norm == excluded or norm.startswith(excluded + os.sep):
            return True
    return False
