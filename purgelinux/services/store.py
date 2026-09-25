"""Central application state.

Equivalent of Purge's PurgeStore, kept deliberately thin: scanning lives in
the scanners, deleting lives in the deleter, and this holds the results, the
selection, and the scan lifecycle.

The store never imports GTK. The UI passes a `dispatch` callable that
schedules work on the main loop (GLib.idle_add); tests pass a direct call.
This is what keeps the whole pipeline testable without a display.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, Iterable, Sequence

from ..models import (
    DeletionCandidate,
    DeletionReport,
    DuplicateGroup,
    ProjectGroup,
    SafetyLevel,
    ScanItem,
    Tab,
    candidates_from,
    format_bytes,
)
from ..scanners import ai_models, app_caches, dev_tools, duplicates, large_files, projects, system
from ..scanners.base import CancelToken, ScanEvent
from . import history, prefs
from .deleter import Deleter

Dispatch = Callable[[Callable[[], None]], None]
Listener = Callable[..., None]

#: Which scanners feed which tab.
TAB_SOURCES = {
    Tab.APP_CACHES: ("app_caches",),
    Tab.DEV_TOOLS: ("dev_tools", "projects"),
    Tab.LARGE_FILES: ("large_files",),
    Tab.SYSTEM: ("system",),
}


def _immediate(fn: Callable[[], None]) -> None:
    fn()


class AppStore:
    def __init__(self, dispatch: Dispatch | None = None) -> None:
        self.dispatch: Dispatch = dispatch or _immediate
        self.items: dict[str, list[ScanItem]] = defaultdict(list)
        self.project_groups: list[ProjectGroup] = []
        self.duplicate_groups: list[DuplicateGroup] = []
        self.selected: set[str] = set()
        self.status = ""
        self.scanning = False
        self.deleting = False
        self.last_report: DeletionReport | None = None

        self._token = CancelToken()
        self._index: dict[str, ScanItem] = {}
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # -- observer plumbing -------------------------------------------------

    def connect(self, signal: str, callback: Listener) -> None:
        self._listeners[signal].append(callback)

    def _emit(self, signal: str, *args) -> None:
        def fire() -> None:
            for callback in list(self._listeners[signal]):
                callback(*args)

        self.dispatch(fire)

    # -- queries -----------------------------------------------------------

    def items_for(self, tab: Tab) -> list[ScanItem]:
        result: list[ScanItem] = []
        for source in TAB_SOURCES.get(tab, ()):
            result.extend(self.items[source])
        result.sort(key=lambda item: -item.size_bytes)
        return result

    def item(self, item_id: str) -> ScanItem | None:
        return self._index.get(item_id)

    def total_bytes(self, tab: Tab | None = None) -> int:
        if tab is None:
            return sum(item.size_bytes for group in self.items.values() for item in group)
        return sum(item.size_bytes for item in self.items_for(tab))

    def safe_bytes(self) -> int:
        """Total the one-click clean would free."""
        return sum(c.size_bytes for c in self.safe_candidates())

    def selected_bytes(self) -> int:
        return sum(c.size_bytes for c in self.selected_candidates())

    def counts_for(self, tab: Tab) -> tuple[int, int, int]:
        """(all, safe, check-first) row counts for the filter chips."""
        rows = self.items_for(tab)
        safe = sum(1 for r in rows if r.safety is SafetyLevel.SAFE)
        return len(rows), safe, len(rows) - safe

    # -- selection ---------------------------------------------------------

    def set_selected(self, item_id: str, selected: bool) -> None:
        item = self._index.get(item_id)
        if item is None or item.extra.get("read_only"):
            return
        item.selected = selected
        if selected:
            self.selected.add(item_id)
        else:
            self.selected.discard(item_id)
        self._emit("selection-changed")

    def select_all(self, tab: Tab, selected: bool) -> None:
        for item in self.items_for(tab):
            if item.extra.get("read_only"):
                continue
            item.selected = selected
            if selected:
                self.selected.add(item.item_id)
            else:
                self.selected.discard(item.item_id)
        self._emit("selection-changed")

    def clear_selection(self) -> None:
        for item_id in list(self.selected):
            item = self._index.get(item_id)
            if item:
                item.selected = False
        self.selected.clear()
        self._emit("selection-changed")

    # -- candidates --------------------------------------------------------

    def selected_candidates(self) -> list[DeletionCandidate]:
        chosen = [self._index[i] for i in self.selected if i in self._index]
        return candidates_from(chosen)

    def safe_candidates(self) -> list[DeletionCandidate]:
        """Rows eligible for one-click and scheduled cleaning.

        Safe tier only, and never personal files: those are not rebuildable,
        so they are only ever removed when picked by hand.
        """
        eligible = [
            item
            for source, group in self.items.items()
            for item in group
            if item.safety is SafetyLevel.SAFE
            and not item.user_selected
            and not item.extra.get("read_only")
            and item.size_bytes > 0
        ]
        return candidates_from(eligible)

    # -- scanning ----------------------------------------------------------

    def start_scan(self, tabs: Iterable[Tab] | None = None) -> None:
        if self.scanning:
            return
        wanted = list(tabs) if tabs else [Tab.APP_CACHES, Tab.DEV_TOOLS, Tab.LARGE_FILES, Tab.SYSTEM]
        self._token = CancelToken()
        self.scanning = True
        self._emit("scan-started")

        self._thread = threading.Thread(target=self._run_scan, args=(wanted,), daemon=True)
        self._thread.start()

    def cancel_scan(self) -> None:
        self._token.cancel()

    def _sink(self, source: str) -> Callable[[ScanEvent], None]:
        def handle(event: ScanEvent) -> None:
            if event.kind == "status":
                self.status = event.status
                self._emit("status-changed", event.status)
            elif event.kind == "item" and event.item is not None:
                with self._lock:
                    self.items[source].append(event.item)
                    self._index[event.item.item_id] = event.item
                self._emit("item-added", event.item)
            elif event.kind == "size":
                self._apply_size(event)

        return handle

    def _apply_size(self, event: ScanEvent) -> None:
        from ..models import Location

        item = self._index.get(event.item_id)
        if item is None or not event.sizes:
            return
        with self._lock:
            item.locations = [
                Location(
                    path=loc.path,
                    size_bytes=event.sizes.get(loc.path, loc.size_bytes),
                    last_modified=event.last_modified or loc.last_modified,
                    key=loc.key,
                )
                for loc in item.locations
            ]
            item.size_resolved = True
            if item.size_bytes == 0:
                # An empty cache folder is noise, not a finding.
                self._drop(item)
        self._emit("item-updated", item)

    def _drop(self, item: ScanItem) -> None:
        for source, group in self.items.items():
            if item in group:
                group.remove(item)
                break
        self._index.pop(item.item_id, None)
        self.selected.discard(item.item_id)

    def _run_scan(self, tabs: Sequence[Tab]) -> None:
        try:
            for source in ("app_caches", "dev_tools", "projects", "large_files", "system"):
                if any(source in TAB_SOURCES.get(tab, ()) for tab in tabs):
                    with self._lock:
                        self.items[source] = []
                    for item_id, item in list(self._index.items()):
                        if item.source == source:
                            self._index.pop(item_id, None)
                            self.selected.discard(item_id)

            if Tab.APP_CACHES in tabs:
                app_caches.scan(self._sink("app_caches"), self._token)
            if Tab.DEV_TOOLS in tabs:
                dev_tools.scan(self._sink("dev_tools"), self._token)
                self.project_groups = projects.scan(self._sink("projects"), self._token)
            if Tab.LARGE_FILES in tabs:
                large_files.scan(self._sink("large_files"), self._token)
                ai_models.scan(self._sink("large_files"), self._token)
                self.duplicate_groups = duplicates.find(self.items["large_files"], self._token)
                self._mark_duplicates()
            if Tab.SYSTEM in tabs:
                system.scan(self._sink("system"), self._token)
        finally:
            self.scanning = False
            prefs.set_value("last_scan_at", __import__("time").time())
            self._emit("scan-finished")

    def _mark_duplicates(self) -> None:
        for group in self.duplicate_groups:
            for item in group.items:
                item.extra["duplicate_group"] = group.group_id
                item.extra["is_keeper"] = item.item_id == group.keeper_id

    # -- cleaning ----------------------------------------------------------

    def clean(
        self,
        candidates: Sequence[DeletionCandidate],
        *,
        trigger: str = "manual",
        allow_permanent_fallback: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        """Run a clean on a worker thread; results arrive via signals."""
        if self.deleting or not candidates:
            return
        self.deleting = True
        self._emit("clean-started", len(candidates))

        def work() -> None:
            def progress(done: int, total: int, name: str) -> None:
                if on_progress:
                    self.dispatch(lambda: on_progress(done, total, name))
                self._emit("clean-progress", done, total, name)

            report = Deleter().delete(
                candidates,
                progress=progress,
                allow_permanent_fallback=allow_permanent_fallback,
            )
            history.record(report, trigger)
            lifetime = int(prefs.get("lifetime_freed_bytes", 0)) + report.freed_bytes
            prefs.set_value("lifetime_freed_bytes", lifetime)

            removed = set(report.removed) | set(report.trashed)
            with self._lock:
                for item in list(self._index.values()):
                    if all(path in removed for path in item.paths):
                        self._drop(item)

            self.last_report = report
            self.deleting = False
            self._emit("clean-finished", report)

        threading.Thread(target=work, daemon=True).start()

    # -- summary -----------------------------------------------------------

    def summary_line(self, tab: Tab) -> str:
        rows = self.items_for(tab)
        total = sum(r.size_bytes for r in rows)
        return f"{len(rows)} items · {format_bytes(total)} recoverable"
