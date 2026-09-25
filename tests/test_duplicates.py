"""Duplicate detection must be exact, never merely probable."""

from __future__ import annotations

from purgelinux.scanners import duplicates, large_files
from purgelinux.scanners.base import CancelToken


def _rows(make_tree, files: dict[str, bytes]) -> list:
    rows = []
    for relative, payload in files.items():
        path = make_tree(relative, "")
        with open(path, "wb") as handle:
            handle.write(payload)
        import os

        stat = os.stat(path)
        rows.append(large_files._make_row(path, stat.st_size, stat.st_mtime))
    return rows


def test_identical_files_are_grouped(make_tree):
    payload = b"A" * 100_000
    rows = _rows(make_tree, {
        "Videos/clip.mp4": payload,
        "Downloads/clip.mp4": payload,
        "Documents/other.mp4": b"B" * 100_000,
    })
    groups = duplicates.find(rows, CancelToken())
    assert len(groups) == 1
    assert len(groups[0].items) == 2
    assert groups[0].reclaimable_bytes == 100_000


def test_one_byte_difference_is_not_a_duplicate(make_tree):
    rows = _rows(make_tree, {
        "Videos/a.bin": b"A" * 99_999 + b"X",
        "Videos/b.bin": b"A" * 99_999 + b"Y",
    })
    assert duplicates.find(rows, CancelToken()) == []


def test_difference_beyond_the_head_is_still_caught(make_tree):
    """The head hash must not be the last word."""
    head = b"A" * 8192
    rows = _rows(make_tree, {
        "Videos/a.bin": head + b"B" * 50_000,
        "Videos/b.bin": head + b"C" * 50_000,
    })
    assert duplicates.find(rows, CancelToken()) == []


def test_keeper_avoids_the_copy(make_tree):
    payload = b"Z" * 60_000
    rows = _rows(make_tree, {
        "Videos/holiday.mp4": payload,
        "Downloads/holiday (1).mp4": payload,
    })
    group = duplicates.find(rows, CancelToken())[0]
    keeper = next(i for i in group.items if i.item_id == group.keeper_id)
    assert "(1)" not in keeper.name

    to_remove = duplicates.cleanup_items(group)
    assert len(to_remove) == 1
    assert "(1)" in to_remove[0].name


def test_empty_files_are_ignored(make_tree):
    rows = _rows(make_tree, {"Documents/a.txt": b"", "Documents/b.txt": b""})
    assert duplicates.find(rows, CancelToken()) == []
