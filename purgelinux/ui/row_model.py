"""GObject wrapper around ScanItem.

Gtk.ListView needs a Gio.ListStore of GObjects, and the app's own model is a
plain dataclass. This is the adapter, kept as thin as possible: the dataclass
stays the source of truth and this only exposes what the row widget binds to.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject  # noqa: E402

from ..models import ScanItem  # noqa: E402


class RowObject(GObject.Object):
    __gtype_name__ = "PurgeRowObject"

    def __init__(self, item: ScanItem) -> None:
        super().__init__()
        self.item = item

    @GObject.Property(type=str)
    def item_id(self) -> str:
        return self.item.item_id

    @GObject.Property(type=str)
    def name(self) -> str:
        return self.item.name

    @GObject.Property(type=str)
    def size_text(self) -> str:
        return self.item.formatted_size if self.item.size_resolved else "…"

    @GObject.Property(type=bool, default=False)
    def selected(self) -> bool:
        return self.item.selected
