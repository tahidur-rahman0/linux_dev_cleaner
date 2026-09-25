"""ScanPage's sorter and filter must match PyGObject's calling convention.

PyGObject hands a CustomSorter/CustomFilter callback the user_data as a
trailing argument. A callback that does not accept it raises TypeError inside
the GObject trampoline, which *swallows* the error and returns 0 — so the bug
shows up as a silently unsorted list rather than a crash.
"""

from __future__ import annotations

import functools
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from purgelinux.models import Location, SafetyLevel, ScanItem  # noqa: E402
from purgelinux.ui.pages.scan_page import SORT_LARGEST, ScanPage  # noqa: E402
from purgelinux.ui.row_model import RowObject  # noqa: E402


def _row(name: str, size: int) -> RowObject:
    return RowObject(
        ScanItem(
            item_id=f"path:/tmp/{name}",
            name=name,
            locations=[Location(path=f"/tmp/{name}", key=name, size_bytes=size)],
            safety=SafetyLevel.SAFE,
        )
    )


def test_sorter_orders_largest_first_through_gtk():
    page = SimpleNamespace(_sort=SORT_LARGEST)
    sorter = Gtk.CustomSorter.new(functools.partial(ScanPage._sort_func, page))

    big, small = _row("big", 1000), _row("small", 10)
    # A swallowed TypeError makes both of these 0.
    assert sorter.compare(big, small) < 0
    assert sorter.compare(small, big) > 0


def test_filter_runs_through_gtk():
    page = SimpleNamespace(filter_bar=SimpleNamespace(active="all"))
    custom = Gtk.CustomFilter.new(functools.partial(ScanPage._filter_func, page))

    assert custom.match(_row("kept", 1000)) is True
