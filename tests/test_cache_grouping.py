"""Rows may only merge when they genuinely belong to the same app.

The generic ~/.cache sweep gives every unrecognised folder the same fallback
explanation key. That key must not also act as a merge key, or a dozen
unrelated caches collapse into one row wearing the first folder's name — and
the user then approves a deletion the row does not describe.
"""

from __future__ import annotations

from purgelinux.scanners.app_caches import _generic_cache_items


def _names_and_paths(items):
    return {item.name: sorted(item.paths) for item in items}


def test_unrelated_generic_caches_stay_separate(make_tree):
    from purgelinux.scanners.base import merge_by_definition

    for folder in ("gnome-calculator", "firebase", "obexd", "cursor-compile-cache"):
        make_tree(f".cache/{folder}/blob.bin", "x" * 1024)

    items: list = []
    _generic_cache_items(items)
    merged = merge_by_definition(items)

    by_name = _names_and_paths(merged)
    assert len(merged) == 4, f"generic caches collapsed into {len(merged)} row(s): {by_name}"
    for item in merged:
        assert len(item.paths) == 1, f"{item.name} absorbed {item.paths}"


def test_a_real_definition_key_still_merges():
    from purgelinux.models import Location, SafetyLevel, ScanItem
    from purgelinux.scanners.base import merge_by_definition

    def row(path: str) -> ScanItem:
        return ScanItem(
            item_id=ScanItem.make_id("chrome-cache", path),
            name="Google Chrome Cache",
            locations=[Location(path=path, key="chrome-cache")],
            safety=SafetyLevel.SAFE,
            definition_key="chrome-cache",
        )

    merged = merge_by_definition([row("/a/Cache_Data"), row("/a/Code Cache/js")])

    assert len(merged) == 1
    assert merged[0].paths == ["/a/Cache_Data", "/a/Code Cache/js"]
