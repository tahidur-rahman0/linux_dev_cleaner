"""The generic results page used by App Caches, Dev Tools and System.

Structure mirrors the macOS app: filter chips, a select-all/sort strip, then
a virtualised list of rows. Gtk.ListView rather than Gtk.ListBox because a
dev machine easily produces thousands of rows and only the virtualised widget
stays smooth.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ...models import SafetyLevel, ScanItem, Tab, format_bytes
from ..filter_bar import FILTER_ALL, FilterBar
from ..row_model import RowObject
from ..scan_row import ScanRowFactory

SORT_LARGEST = 0
SORT_NEWEST = 1
SORT_NAME = 2


class ScanPage(Gtk.Box):
    __gtype_name__ = "PurgeScanPage"

    def __init__(self, store, tab: Tab, empty_title: str, empty_body: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.store = store
        self.tab = tab
        self._sort = SORT_LARGEST

        self.filter_bar = FilterBar()
        self.filter_bar.connect("filter-changed", lambda _b, _k: self.refilter())
        self.append(self.filter_bar)

        self.append(self._build_toolstrip())

        self.model = Gio.ListStore(item_type=RowObject)
        self.filter_model = Gtk.FilterListModel(model=self.model)
        self.filter_model.set_filter(Gtk.CustomFilter.new(self._filter_func))
        self.sort_model = Gtk.SortListModel(model=self.filter_model)
        self.sort_model.set_sorter(Gtk.CustomSorter.new(self._sort_func))

        self.row_factory = ScanRowFactory(self._on_toggle)
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection(model=self.sort_model),
            factory=self.row_factory,
            vexpand=True,
        )
        # Selection is owned by the store, and Select All / "keep one of each"
        # / clearing after a clean all change it without going through a row
        # widget. Without this the header updates and the ticks do not.
        self._sync_queued = False
        self.store.connect("selection-changed", self._queue_selection_sync)
        self.list_view.add_css_class("navigation-sidebar")

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.list_view)

        self.empty_state = Adw_status_page(empty_title, empty_body)

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty_state, "empty")
        self.append(self.stack)

    # -- toolstrip ---------------------------------------------------------

    def _build_toolstrip(self) -> Gtk.Widget:
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        strip.set_margin_start(12)
        strip.set_margin_end(12)
        strip.set_margin_bottom(6)

        self.select_all = Gtk.CheckButton(label="Select All")
        self._select_all_handler = self.select_all.connect("toggled", self._on_select_all)
        strip.append(self.select_all)

        strip.append(Gtk.Box(hexpand=True))

        self.sort_dropdown = Gtk.DropDown.new_from_strings(["Largest", "Recently used", "Name"])
        self.sort_dropdown.connect("notify::selected", self._on_sort_changed)
        strip.append(self.sort_dropdown)
        return strip

    def _on_select_all(self, button: Gtk.CheckButton) -> None:
        active = button.get_active()
        # Only what is currently visible under the active filter and search.
        for index in range(self.sort_model.get_n_items()):
            row = self.sort_model.get_item(index)
            self.store.set_selected(row.item.item_id, active)

    def _on_sort_changed(self, dropdown: Gtk.DropDown, _param) -> None:
        self._sort = dropdown.get_selected()
        self.sort_model.get_sorter().changed(Gtk.SorterChange.DIFFERENT)

    def _on_toggle(self, item_id: str, selected: bool) -> None:
        self.store.set_selected(item_id, selected)

    def _queue_selection_sync(self, *_args) -> None:
        """Coalesce a burst of selection changes into one widget sync.

        Select All emits one signal per row; syncing on each would be
        quadratic in the number of visible rows.
        """
        if self._sync_queued:
            return
        self._sync_queued = True

        def run() -> bool:
            self._sync_queued = False
            self.row_factory.sync_selection()
            self._sync_select_all()
            return False

        GLib.idle_add(run)

    def _sync_select_all(self) -> None:
        """Point the header checkbox at what is actually selected.

        Read-only rows (the Docker row) can never be selected, so they are left
        out of the total or the box could never reach "all". Its own handler is
        blocked, since setting it would otherwise re-run Select All.
        """
        rows = [
            self.sort_model.get_item(index).item
            for index in range(self.sort_model.get_n_items())
        ]
        selectable = [row for row in rows if not row.extra.get("read_only")]
        chosen = sum(1 for row in selectable if row.selected)

        self.select_all.handler_block(self._select_all_handler)
        self.select_all.set_active(bool(selectable) and chosen == len(selectable))
        self.select_all.set_inconsistent(0 < chosen < len(selectable))
        self.select_all.handler_unblock(self._select_all_handler)

    # -- model callbacks ---------------------------------------------------

    # PyGObject hands these callbacks the sorter/filter user_data as a
    # trailing argument (None here). Omitting it raises TypeError on every
    # single comparison, which silently leaves the list unsorted.
    def _filter_func(self, row: RowObject, _user_data=None) -> bool:
        item: ScanItem = row.item
        if item.size_resolved and item.size_bytes <= 0:
            return False
        return FilterBar.matches(self.filter_bar.active, item.safety)

    def _sort_func(self, left: RowObject, right: RowObject, _user_data=None) -> int:
        a, b = left.item, right.item
        if self._sort == SORT_NAME:
            return (a.name.lower() > b.name.lower()) - (a.name.lower() < b.name.lower())
        if self._sort == SORT_NEWEST:
            return (a.last_modified < b.last_modified) - (a.last_modified > b.last_modified)
        return (a.size_bytes < b.size_bytes) - (a.size_bytes > b.size_bytes)

    # -- public API --------------------------------------------------------

    def reload(self) -> None:
        """Rebuild the list from the store."""
        self.model.remove_all()
        for item in self.store.items_for(self.tab):
            self.model.append(RowObject(item))
        self.refilter()

    def add_item(self, item: ScanItem) -> None:
        if item.source not in _sources_for(self.tab):
            return
        self.model.append(RowObject(item))
        self.refilter()

    def update_item(self, item: ScanItem) -> None:
        for index in range(self.model.get_n_items()):
            row = self.model.get_item(index)
            if row.item.item_id == item.item_id:
                if item.size_resolved and item.size_bytes <= 0:
                    self.model.remove(index)
                else:
                    self.model.items_changed(index, 1, 1)
                break
        self.refilter()

    def refilter(self) -> None:
        self.filter_model.get_filter().changed(Gtk.FilterChange.DIFFERENT)
        self.sort_model.get_sorter().changed(Gtk.SorterChange.DIFFERENT)
        total, safe, check = self.store.counts_for(self.tab)
        self.filter_bar.set_counts(total, safe, check)
        has_rows = self.sort_model.get_n_items() > 0
        self.stack.set_visible_child_name("list" if has_rows else "empty")

    def subtitle(self) -> str:
        return self.store.summary_line(self.tab)


def _sources_for(tab: Tab) -> tuple[str, ...]:
    from ...services.store import TAB_SOURCES

    return TAB_SOURCES.get(tab, ())


def Adw_status_page(title: str, body: str):
    """Small helper so the import of Adw stays in one place."""
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    page = Adw.StatusPage(title=title, description=body)
    page.set_icon_name("emblem-ok-symbolic")
    return page
