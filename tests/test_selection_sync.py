"""Selection changed outside a row widget must still reach the checkboxes.

Select All, "keep one of each" and clearing after a clean all set
`ScanItem.selected` through the store. Gtk.ListView does not rebind for that,
so without an explicit sync the header updates ("Clean Selected (17.2 GB)")
while every visible tick stays empty.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from purgelinux.models import Location, SafetyLevel, ScanItem, Tab  # noqa: E402
from purgelinux.services.store import AppStore  # noqa: E402
from purgelinux.ui.pages.scan_page import ScanPage  # noqa: E402
from purgelinux.ui.row_model import RowObject  # noqa: E402
from purgelinux.ui.scan_row import ScanRowFactory  # noqa: E402


def _item(name: str, size: int = 100) -> ScanItem:
    return ScanItem(
        item_id=f"path:/tmp/{name}",
        name=name,
        locations=[Location(path=f"/tmp/{name}", key=name, size_bytes=size)],
        safety=SafetyLevel.SAFE,
        source="app_caches",
    )


def _drain() -> None:
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class _StubListItem:
    """Stands in for the Gtk.ListItem a realized ListView hands the factory."""

    def __init__(self, item: ScanItem, toggles: list) -> None:
        self.purge_check = Gtk.CheckButton()
        self.purge_handler = self.purge_check.connect(
            "toggled", lambda b: toggles.append(b.get_active())
        )
        self._row = RowObject(item)

    def get_item(self) -> RowObject:
        return self._row


def _stub_list_item(factory: ScanRowFactory, item: ScanItem, toggles: list):
    stub = _StubListItem(item, toggles)
    factory._bound.add(stub)
    return stub


def test_sync_selection_updates_the_checkbox():
    factory = ScanRowFactory(lambda *_: None)
    item = _item("a")
    toggles: list = []
    stub = _stub_list_item(factory, item, toggles)

    assert stub.purge_check.get_active() is False
    item.selected = True
    factory.sync_selection()

    assert stub.purge_check.get_active() is True


def test_sync_selection_does_not_report_itself_as_a_click():
    factory = ScanRowFactory(lambda *_: None)
    item = _item("a")
    toggles: list = []
    stub = _stub_list_item(factory, item, toggles)

    item.selected = True
    factory.sync_selection()

    assert toggles == [], "programmatic sync fired the toggled handler"


def test_unbound_rows_are_dropped():
    factory = ScanRowFactory(lambda *_: None)
    stub = _stub_list_item(factory, _item("a"), [])
    assert stub in factory._bound
    factory._unbind(factory, stub)
    assert stub not in factory._bound


def _page_with(items: list[ScanItem]) -> tuple[ScanPage, AppStore]:
    store = AppStore()
    for item in items:
        store.items["app_caches"].append(item)
        store._index[item.item_id] = item
    page = ScanPage(store, Tab.APP_CACHES, "Nothing", "nothing here")
    page.reload()
    return page, store


def test_select_all_selects_every_visible_row_and_syncs_once():
    items = [_item(name) for name in ("a", "b", "c")]
    page, store = _page_with(items)

    calls: list = []
    page.row_factory.sync_selection = lambda: calls.append(1)

    page.select_all.set_active(True)
    _drain()

    assert all(i.selected for i in items), "Select All did not reach the model"
    assert len(calls) == 1, f"expected one coalesced sync, got {len(calls)}"


def test_clearing_the_selection_also_syncs():
    items = [_item(name) for name in ("a", "b")]
    page, store = _page_with(items)
    page.select_all.set_active(True)
    _drain()

    calls: list = []
    page.row_factory.sync_selection = lambda: calls.append(1)
    store.clear_selection()
    _drain()

    assert not any(i.selected for i in items)
    assert calls, "clearing the selection did not refresh the checkboxes"


def test_select_all_box_reflects_the_real_selection():
    items = [_item(name) for name in ("a", "b")]
    page, store = _page_with(items)

    store.set_selected(items[0].item_id, True)
    _drain()
    assert page.select_all.get_active() is False
    assert page.select_all.get_inconsistent() is True, "partial selection should show as mixed"

    store.set_selected(items[1].item_id, True)
    _drain()
    assert page.select_all.get_active() is True
    assert page.select_all.get_inconsistent() is False


def test_select_all_box_clears_after_the_selection_is_cleared():
    items = [_item(name) for name in ("a", "b")]
    page, store = _page_with(items)
    page.select_all.set_active(True)
    _drain()
    assert page.select_all.get_active() is True

    store.clear_selection()
    _drain()

    assert page.select_all.get_active() is False, "box still ticked with nothing selected"
    assert page.select_all.get_inconsistent() is False


def test_a_read_only_row_does_not_block_select_all():
    items = [_item("a"), _item("docker")]
    items[1].extra = {"read_only": True}
    page, store = _page_with(items)

    page.select_all.set_active(True)
    _drain()

    assert items[0].selected is True
    assert items[1].selected is False, "read-only row must never be selected"
    assert page.select_all.get_active() is True, "read-only row left the box unreachable"
    assert page.select_all.get_inconsistent() is False
