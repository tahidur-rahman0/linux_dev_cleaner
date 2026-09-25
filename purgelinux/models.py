"""Core data types shared by scanners, services and the UI.

Ported from Purge's `purge/Models/`. The shapes are deliberately close to
upstream so the safety reasoning transfers: a row is a set of *locations*, a
safety tier, and an explanation key.
"""

from __future__ import annotations

import enum
import os
import time
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence


class SafetyLevel(enum.Enum):
    """Two visible tiers, exactly as upstream.

    Anything the app cannot classify is never shown at all, so there is no
    third "unknown" tier on screen.
    """

    SAFE = "safe"
    MEDIUM = "medium"

    @property
    def label(self) -> str:
        return "Safe to Clean" if self is SafetyLevel.SAFE else "Check First"

    @property
    def css_class(self) -> str:
        return "pill-safe" if self is SafetyLevel.SAFE else "pill-medium"


class DeletionRoute(enum.Enum):
    """How a candidate is removed.

    TRASH  — personal files and anything ambiguous; restorable from Files.
    REMOVE — caches and build artifacts; trashing them would not free space.
    HELPER — system paths, executed by the root helper via pkexec.
    """

    TRASH = "trash"
    REMOVE = "remove"
    HELPER = "helper"


class Tab(enum.Enum):
    APP_CACHES = "app_caches"
    DEV_TOOLS = "dev_tools"
    LARGE_FILES = "large_files"
    SYSTEM = "system"
    SETTINGS = "settings"
    ABOUT = "about"


def format_bytes(num: int) -> str:
    """Human size, matching the app's compact style (1.33 GB, 54.5 MB, 15 KB)."""
    if num < 0:
        num = 0
    if num < 1000:
        return f"{num} B"
    value = float(num)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1000.0
        if value < 1000.0:
            if value >= 100:
                return f"{value:.0f} {unit}"
            if value >= 10:
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}"
    return f"{value:.1f} EB"


@dataclass(frozen=True)
class Location:
    """One directory or file on disk belonging to a row."""

    path: str
    size_bytes: int = 0
    last_modified: float = 0.0
    #: Folder name (or bundle-ish id) used to match an explanation entry.
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", os.path.basename(self.path.rstrip("/")))


@dataclass
class ScanItem:
    """A single row in any of the scan lists.

    One type covers caches, dev tools, project artifacts, large files and
    system rows; the `source` field says which list it belongs to. Upstream
    uses a separate struct per tab, but they carry the same fields and the
    deletion pipeline treats them identically, so one type removes a lot of
    duplicated plumbing.
    """

    #: Stable identity. `def:<key>` for grouped rows, `path:<path>` otherwise.
    item_id: str
    name: str
    locations: list[Location]
    safety: SafetyLevel
    source: str = "app_caches"
    #: Canonical explanations.json key; None means "not grouped with others".
    definition_key: str | None = None
    explanation: str = ""
    icon_name: str = "folder-symbolic"
    route: DeletionRoute = DeletionRoute.REMOVE
    selected: bool = False
    #: Free-form note shown under the row ("Chrome is running — …").
    note: str = ""
    #: How the user brings this back, when that is knowable.
    reinstall_hint: str = ""
    #: Set for rows the deletion engine must hand to the privileged helper.
    helper_verb: str = ""
    #: Populated lazily; False while a size job is still queued.
    size_resolved: bool = True
    #: Personal file chosen by the user rather than matched by a catalog.
    user_selected: bool = False
    #: Extra per-source payload (project root, duplicate group id, …).
    extra: dict = field(default_factory=dict)

    @property
    def size_bytes(self) -> int:
        return sum(loc.size_bytes for loc in self.locations)

    @property
    def formatted_size(self) -> str:
        return format_bytes(self.size_bytes)

    @property
    def last_modified(self) -> float:
        return max((loc.last_modified for loc in self.locations), default=0.0)

    @property
    def paths(self) -> list[str]:
        return [loc.path for loc in self.locations]

    @property
    def path(self) -> str:
        return self.locations[0].path

    @property
    def is_safe(self) -> bool:
        return self.safety is SafetyLevel.SAFE

    def with_locations(self, locations: Sequence[Location]) -> "ScanItem":
        return replace(self, locations=list(locations))

    @staticmethod
    def make_id(definition_key: str | None, path: str) -> str:
        if definition_key:
            return f"def:{definition_key}"
        return f"path:{os.path.normpath(path)}"


@dataclass
class ProjectGroup:
    """Removable artifacts belonging to one developer project."""

    root: str
    name: str
    types: list[str]
    artifacts: list[ScanItem]
    last_modified: float = 0.0
    #: Resolved lazily by the git guard.
    git_dirty: bool | None = None

    @property
    def size_bytes(self) -> int:
        return sum(a.size_bytes for a in self.artifacts)

    @property
    def formatted_size(self) -> str:
        return format_bytes(self.size_bytes)

    @property
    def stale_days(self) -> int:
        if not self.last_modified:
            return 0
        return max(0, int((time.time() - self.last_modified) / 86400))


@dataclass
class DuplicateGroup:
    """A set of byte-identical files."""

    group_id: str
    size_each: int
    items: list[ScanItem]
    keeper_id: str = ""

    def __post_init__(self) -> None:
        if not self.keeper_id and self.items:
            self.keeper_id = self.suggested_keeper().item_id

    def suggested_keeper(self) -> ScanItem:
        """Prefer the shallowest path, then the oldest — the likely original.

        Upstream's DuplicateKeeper picks the copy that looks least like a
        duplicate rather than simply the first one found.
        """
        def rank(item: ScanItem) -> tuple:
            p = item.path
            depth = p.count(os.sep)
            looks_copied = any(
                marker in os.path.basename(p).lower()
                for marker in ("copy", "(1)", "(2)", "_1", "-1", "duplicate")
            )
            return (looks_copied, depth, item.last_modified)

        return min(self.items, key=rank)

    @property
    def reclaimable_bytes(self) -> int:
        return self.size_each * max(0, len(self.items) - 1)


@dataclass
class DeletionCandidate:
    """One unit of work for the deletion engine."""

    item_id: str
    name: str
    path: str
    size_bytes: int
    route: DeletionRoute
    safety: SafetyLevel
    source: str = ""
    helper_verb: str = ""
    #: True for personal files the user picked by hand in Large Files. These
    #: are not on the cache allowlist and never could be, so they take the
    #: user-selected route through the safety policy instead.
    user_selected: bool = False

    @property
    def formatted_size(self) -> str:
        return format_bytes(self.size_bytes)


@dataclass
class SkippedItem:
    path: str
    reason: str


@dataclass
class DeletionReport:
    """Outcome of one clean, shown in the result sheet and history."""

    freed_bytes: int = 0
    trashed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    failed: list[SkippedItem] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def item_count(self) -> int:
        return len(self.trashed) + len(self.removed)

    @property
    def formatted_freed(self) -> str:
        return format_bytes(self.freed_bytes)

    def merge(self, other: "DeletionReport") -> None:
        self.freed_bytes += other.freed_bytes
        self.trashed.extend(other.trashed)
        self.removed.extend(other.removed)
        self.skipped.extend(other.skipped)
        self.failed.extend(other.failed)


def candidates_from(items: Iterable[ScanItem]) -> list[DeletionCandidate]:
    """Flatten rows into per-path candidates for the deletion engine."""
    out: list[DeletionCandidate] = []
    for item in items:
        for loc in item.locations:
            out.append(
                DeletionCandidate(
                    item_id=item.item_id,
                    name=item.name,
                    path=loc.path,
                    size_bytes=loc.size_bytes,
                    route=item.route,
                    safety=item.safety,
                    source=item.source,
                    helper_verb=item.helper_verb,
                    user_selected=item.user_selected,
                )
            )
    return out
