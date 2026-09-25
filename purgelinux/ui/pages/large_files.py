"""Large Files page: search, category chips, and duplicate handling."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ...models import Tab, format_bytes
from ...scanners.large_files import CATEGORY_LABELS
from ..row_model import RowObject
from .scan_page import ScanPage

CATEGORY_ORDER = ("video", "audio", "image", "pdf", "archive", "document", "ai_model", "other")


class LargeFilesPage(ScanPage):
    __gtype_name__ = "PurgeLargeFilesPage"

    def __init__(self, store) -> None:
        super().__init__(
            store,
            Tab.LARGE_FILES,
            "No large files found",
            "Nothing in your Documents, Downloads, Videos, Music or Pictures folders is above the size threshold. "
            "Lower it in Settings to see more.",
        )
        self.category = "all"
        self.search_text = ""
        self.duplicates_only = False

        self.prepend(self._build_category_bar())
        self.prepend(self._build_search())

        # These are personal files, not caches — say so once, at the top.
        banner = Gtk.Label(
            label="These are your own files. Anything removed here goes to the Trash, so it can be restored.",
            xalign=0.0, wrap=True,
        )
        banner.add_css_class("dim-label-small")
        banner.set_margin_start(12)
        banner.set_margin_end(12)
        banner.set_margin_top(6)
        self.prepend(banner)

    def _build_search(self) -> Gtk.Widget:
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search files, folders and categories")
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
        self.search_entry.set_margin_top(8)
        self.search_entry.connect("search-changed", self._on_search)
        return self.search_entry

    def _build_category_bar(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_margin_start(12)
        scroller.set_margin_end(12)
        scroller.set_margin_top(8)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._category_buttons: dict[str, Gtk.ToggleButton] = {}

        first: Gtk.ToggleButton | None = None
        entries = [("all", "All")] + [(key, CATEGORY_LABELS[key]) for key in CATEGORY_ORDER]
        entries.append(("duplicates", "Duplicates"))

        for key, label in entries:
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("filter-chip")
            if first is None:
                first = button
                button.set_active(True)
            else:
                button.set_group(first)
            button.connect("toggled", self._on_category, key)
            self._category_buttons[key] = button
            box.append(button)

        scroller.set_child(box)
        return scroller

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self.search_text = entry.get_text().strip().lower()
        self.refilter()

    def _on_category(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active():
            return
        self.duplicates_only = key == "duplicates"
        self.category = "all" if self.duplicates_only else key
        self.refilter()

    def _filter_func(self, row: RowObject) -> bool:
        if not super()._filter_func(row):
            return False
        item = row.item
        if self.duplicates_only and not item.extra.get("duplicate_group"):
            return False
        if self.category != "all" and item.extra.get("category") != self.category:
            return False
        if self.search_text:
            haystack = " ".join(
                (
                    item.name,
                    item.extra.get("folder", ""),
                    item.extra.get("source_label", ""),
                    item.explanation,
                )
            ).lower()
            if self.search_text not in haystack:
                return False
        return True

    def keep_one_of_each(self) -> list:
        """Select every duplicate except the suggested keeper in each set."""
        from ...scanners import duplicates as duplicate_finder

        selected = []
        for group in self.store.duplicate_groups:
            for item in duplicate_finder.cleanup_items(group):
                self.store.set_selected(item.item_id, True)
                selected.append(item)
        self.refilter()
        return selected

    def duplicates_summary(self) -> str:
        groups = self.store.duplicate_groups
        if not groups:
            return ""
        total = sum(g.reclaimable_bytes for g in groups)
        return f"{len(groups)} sets of duplicates · {format_bytes(total)} reclaimable"
