"""All / Safe to Clean / Check First chips (Ctrl+1..3)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from ..models import SafetyLevel

FILTER_ALL = "all"
FILTER_SAFE = "safe"
FILTER_CHECK = "check"


class FilterBar(Gtk.Box):
    __gtype_name__ = "PurgeFilterBar"

    __gsignals__ = {
        "filter-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_bottom(8)

        self.active = FILTER_ALL
        self._buttons: dict[str, Gtk.ToggleButton] = {}
        self._updating = False

        first: Gtk.ToggleButton | None = None
        for key, label in ((FILTER_ALL, "All"), (FILTER_SAFE, "Safe to Clean"), (FILTER_CHECK, "Check First")):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("filter-chip")
            if first is None:
                first = button
                button.set_active(True)
            else:
                button.set_group(first)
            button.connect("toggled", self._on_toggled, key)
            self._buttons[key] = button
            self.append(button)

    def _on_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if self._updating or not button.get_active():
            return
        self.active = key
        self.emit("filter-changed", key)

    def set_active(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is None or button.get_active():
            return
        self._updating = True
        button.set_active(True)
        self._updating = False
        self.active = key
        self.emit("filter-changed", key)

    def set_counts(self, total: int, safe: int, check: int) -> None:
        self._buttons[FILTER_ALL].set_label(f"All  {total}")
        self._buttons[FILTER_SAFE].set_label(f"Safe to Clean  {safe}")
        self._buttons[FILTER_CHECK].set_label(f"Check First  {check}")

    @staticmethod
    def matches(key: str, level: SafetyLevel) -> bool:
        if key == FILTER_ALL:
            return True
        if key == FILTER_SAFE:
            return level is SafetyLevel.SAFE
        return level is SafetyLevel.MEDIUM
