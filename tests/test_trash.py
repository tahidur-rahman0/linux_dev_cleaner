"""XDG trash spec compliance."""

from __future__ import annotations

import os
from urllib.parse import unquote

import pytest

from purgelinux.services import trash


def _read_info(info_path: str) -> dict[str, str]:
    values = {}
    with open(info_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def test_file_is_moved_and_info_written(fake_home, make_tree):
    path = make_tree("Documents/report.pdf", "x" * 4000)
    result = trash.trash(path)

    assert not os.path.exists(path)
    assert os.path.exists(result.trashed_path)
    assert result.trashed_path.startswith(str(fake_home / ".local" / "share" / "Trash" / "files"))

    info = _read_info(result.info_path)
    assert info["Path"] and unquote(info["Path"]) == path
    assert info["DeletionDate"].count("-") == 2 and "T" in info["DeletionDate"]


def test_name_collisions_are_renamed(make_tree):
    first = make_tree("Documents/a/report.pdf", "one")
    second = make_tree("Documents/b/report.pdf", "two")

    result_one = trash.trash(first)
    result_two = trash.trash(second)

    assert os.path.basename(result_one.trashed_path) == "report.pdf"
    assert os.path.basename(result_two.trashed_path) == "report_1.pdf"
    assert os.path.exists(result_one.info_path)
    assert os.path.exists(result_two.info_path)


def test_trashinfo_is_claimed_exclusively(make_tree):
    """A stale info file must not be silently overwritten."""
    path = make_tree("Documents/note.txt", "x")
    result = trash.trash(path)
    again = make_tree("Documents/note.txt", "y")
    second = trash.trash(again)
    assert second.info_path != result.info_path


def test_directories_are_trashed_whole(make_tree):
    make_tree("projects/app/node_modules/pkg/index.js", "x" * 2000)
    target = os.path.join(str(make_tree("projects/app")), "node_modules")

    result = trash.trash(target)
    assert not os.path.exists(target)
    assert os.path.isdir(result.trashed_path)
    assert result.size_bytes > 0


def test_missing_file_raises(fake_home):
    with pytest.raises(trash.TrashError):
        trash.trash(str(fake_home / "nope"))


def test_size_of_counts_a_tree(make_tree):
    make_tree("data/a.bin", "x" * 10000)
    make_tree("data/sub/b.bin", "y" * 10000)
    root = str(make_tree("data"))
    assert trash.size_of(root) >= 20000
