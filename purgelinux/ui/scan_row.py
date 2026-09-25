"""The list row: checkbox, icon, name, explanation, size, safety pill.

One factory serves every tab. Gtk.ListView recycles widgets, so the factory
builds the structure once in `setup` and only rebinds data in `bind`.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango  # noqa: E402

from ..models import ScanItem


def _icon_for(item: ScanItem) -> str:
    return item.icon_name or "folder-symbolic"


class ScanRowFactory(Gtk.SignalListItemFactory):
    """Builds and binds rows, reporting checkbox changes to `on_toggle`."""

    def __init__(self, on_toggle, on_activate=None) -> None:
        super().__init__()
        self._on_toggle = on_toggle
        self._on_activate = on_activate
        # The rows currently on screen. Selection can change from outside the
        # list — Select All, "keep one of each", clearing after a clean — and
        # none of those touch the widgets, so the bound checkboxes have to be
        # re-synced on demand. Only bound rows are held; `unbind` drops them.
        self._bound: set = set()
        self.connect("setup", self._setup)
        self.connect("bind", self._bind)
        self.connect("unbind", self._unbind)

    def sync_selection(self) -> None:
        """Re-read `item.selected` into every visible checkbox.

        The toggled handler is blocked while the widget is updated, so a
        programmatic sync never reports itself back as a user click.
        """
        for list_item in list(self._bound):
            row = list_item.get_item()
            if row is None:
                continue
            check = list_item.purge_check
            handler = getattr(list_item, "purge_handler", None)
            if handler is not None:
                check.handler_block(handler)
            check.set_active(row.item.selected)
            if handler is not None:
                check.handler_unblock(handler)

    # -- widget construction ----------------------------------------------

    def _setup(self, _factory, list_item: Gtk.ListItem) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("scan-row")

        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        box.append(check)

        icon = Gtk.Image(pixel_size=24, valign=Gtk.Align.CENTER)
        box.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(xalign=0.0, wrap=False, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("scan-row-title")
        explanation = Gtk.Label(xalign=0.0, wrap=True, lines=3, ellipsize=Pango.EllipsizeMode.END)
        explanation.add_css_class("scan-row-explanation")
        note = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        note.add_css_class("scan-row-note")
        text.append(title)
        text.append(explanation)
        text.append(note)
        box.append(text)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        size = Gtk.Label(xalign=1.0)
        size.add_css_class("scan-row-size")
        pill = Gtk.Label()
        pill.add_css_class("safety-pill")
        right.append(size)
        right.append(pill)
        box.append(right)

        list_item.set_child(box)
        # Keep references to the parts that get rebound, so `bind` does not
        # have to walk the widget tree on every recycle.
        list_item.purge_check = check
        list_item.purge_icon = icon
        list_item.purge_title = title
        list_item.purge_explanation = explanation
        list_item.purge_note = note
        list_item.purge_size = size
        list_item.purge_pill = pill
        list_item.purge_handler = None

    # -- data binding ------------------------------------------------------

    def _bind(self, _factory, list_item: Gtk.ListItem) -> None:
        row = list_item.get_item()
        item: ScanItem = row.item

        list_item.purge_title.set_text(item.name)
        list_item.purge_explanation.set_text(item.explanation)
        list_item.purge_size.set_text(item.formatted_size if item.size_resolved else "…")
        list_item.purge_icon.set_from_icon_name(_icon_for(item))

        pill = list_item.purge_pill
        pill.set_text(item.safety.label)
        pill.remove_css_class("pill-safe")
        pill.remove_css_class("pill-medium")
        pill.add_css_class(item.safety.css_class)

        note = list_item.purge_note
        if item.note:
            note.set_text(item.note)
            note.set_visible(True)
        else:
            note.set_visible(False)

        check = list_item.purge_check
        check.set_sensitive(not item.extra.get("read_only", False))
        # Set the state before connecting, so restoring a row's checkbox does
        # not fire a toggle and change the selection behind the user's back.
        check.set_active(item.selected)
        list_item.purge_handler = check.connect(
            "toggled", lambda button, i=item: self._on_toggle(i.item_id, button.get_active())
        )
        self._bound.add(list_item)

    def _unbind(self, _factory, list_item: Gtk.ListItem) -> None:
        # The checkbox handler must be disconnected before the widget is
        # recycled, or scrolling silently toggles whatever row lands on it.
        self._bound.discard(list_item)
        handler = getattr(list_item, "purge_handler", None)
        if handler is not None:
            list_item.purge_check.disconnect(handler)
            list_item.purge_handler = None
