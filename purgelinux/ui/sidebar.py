"""Sidebar: navigation, disk summary, and the one-click clean button."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from ..models import Tab, format_bytes
from ..services import disk

NAV_ITEMS = [
    (Tab.APP_CACHES, "App Caches", "drive-harddisk-symbolic"),
    (Tab.DEV_TOOLS, "Dev Tools", "applications-engineering-symbolic"),
    (Tab.LARGE_FILES, "Large Files", "folder-documents-symbolic"),
    (Tab.SYSTEM, "System", "applications-system-symbolic"),
    (Tab.SETTINGS, "Settings", "preferences-system-symbolic"),
    (Tab.ABOUT, "About", "help-about-symbolic"),
]


class Sidebar(Gtk.Box):
    __gtype_name__ = "PurgeSidebar"

    __gsignals__ = {
        "tab-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "clean-safe-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.list_box = Gtk.ListBox(vexpand=True)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.connect("row-selected", self._on_row_selected)

        self._rows: dict[str, Gtk.ListBoxRow] = {}
        for tab, label, icon in NAV_ITEMS:
            row = Adw.ActionRow(title=label)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.set_activatable(True)
            row.tab_value = tab.value
            self.list_box.append(row)
            self._rows[tab.value] = row

        self.append(self.list_box)
        self.append(self._build_footer())
        self.list_box.select_row(self._rows[Tab.APP_CACHES.value])

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        footer.add_css_class("sidebar-footer")

        self.disk_bar = Gtk.LevelBar()
        self.disk_bar.set_min_value(0.0)
        self.disk_bar.set_max_value(1.0)
        footer.append(self.disk_bar)

        self.disk_label = Gtk.Label(xalign=0.0)
        self.disk_label.add_css_class("disk-summary")
        footer.append(self.disk_label)

        safe_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image(icon_name="emblem-ok-symbolic")
        icon.add_css_class("success")
        safe_box.append(icon)
        safe_box.append(Gtk.Label(label="Safe to Clean", xalign=0.0, hexpand=True))
        self.safe_label = Gtk.Label(xalign=1.0)
        self.safe_label.add_css_class("safe-total")
        safe_box.append(self.safe_label)
        footer.append(safe_box)

        self.clean_button = Gtk.Button(label="Clean Safe Items")
        self.clean_button.add_css_class("suggested-action")
        self.clean_button.add_css_class("pill")
        self.clean_button.set_sensitive(False)
        self.clean_button.connect("clicked", lambda _b: self.emit("clean-safe-requested"))
        footer.append(self.clean_button)

        return footer

    def _on_row_selected(self, _list_box, row) -> None:
        if row is not None:
            self.emit("tab-selected", row.tab_value)

    def select(self, tab: Tab) -> None:
        row = self._rows.get(tab.value)
        if row is not None:
            self.list_box.select_row(row)

    def refresh_disk(self) -> None:
        usage = disk.usage()
        self.disk_bar.set_value(usage.used_fraction)
        self.disk_label.set_text(usage.summary)

    def set_safe_total(self, total_bytes: int, *, busy: bool = False) -> None:
        self.safe_label.set_text(format_bytes(total_bytes))
        self.clean_button.set_sensitive(total_bytes > 0 and not busy)
        if total_bytes > 0:
            self.clean_button.set_label(f"Clean Safe Items ({format_bytes(total_bytes)})")
        else:
            self.clean_button.set_label("Clean Safe Items")
