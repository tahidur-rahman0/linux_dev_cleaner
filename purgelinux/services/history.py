"""Cleaning history — what was cleaned, when, and how much it freed."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

from .. import paths
from ..models import DeletionReport, format_bytes

MAX_ENTRIES = 50


@dataclass
class HistoryEntry:
    timestamp: float
    freed_bytes: int
    item_count: int
    trigger: str  # "manual" | "safe" | "scheduled"
    trashed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def formatted_freed(self) -> str:
        return format_bytes(self.freed_bytes)

    @property
    def formatted_date(self) -> str:
        return time.strftime("%-d %b %Y, %H:%M", time.localtime(self.timestamp))


def _history_file() -> str:
    return os.path.join(paths.app_config_dir(), "history.json")


def load() -> list[HistoryEntry]:
    try:
        with open(_history_file(), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return []
    entries = []
    for item in raw if isinstance(raw, list) else []:
        try:
            entries.append(HistoryEntry(**item))
        except TypeError:
            continue
    return entries


def record(report: DeletionReport, trigger: str = "manual") -> HistoryEntry:
    entry = HistoryEntry(
        timestamp=report.finished_at or time.time(),
        freed_bytes=report.freed_bytes,
        item_count=report.item_count,
        trigger=trigger,
        # Cap the stored lists: history is a summary, not an audit log, and a
        # clean can involve tens of thousands of paths.
        trashed=report.trashed[:200],
        removed=report.removed[:200],
        skipped=[s.reason for s in report.skipped[:50]],
    )
    entries = [entry] + load()
    _write(entries[:MAX_ENTRIES])
    return entry


def _write(entries: list[HistoryEntry]) -> None:
    path = _history_file()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump([asdict(e) for e in entries], handle, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def clear() -> None:
    _write([])
