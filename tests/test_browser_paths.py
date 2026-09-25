"""Cleaning browser cache must never log you out.

Builds Chrome and Firefox profile trees in all three Ubuntu packagings and
asserts what the scanner offers and what the policy refuses.
"""

from __future__ import annotations

import pytest

from purgelinux.safety.policy import Decision, evaluate
from purgelinux.scanners import app_caches
from purgelinux.scanners.base import CancelToken

IDENTITY_FILES = [
    ".config/google-chrome/Default/Cookies",
    ".config/google-chrome/Default/Login Data",
    ".config/google-chrome/Default/Web Data",
    ".config/google-chrome/Default/History",
    ".config/google-chrome/Default/Bookmarks",
    ".config/google-chrome/Default/Local State",
    ".config/google-chrome/Default/Local Storage/leveldb",
    ".config/google-chrome/Default/IndexedDB",
    ".config/google-chrome/Default/Service Worker/Database",
    ".mozilla/firefox/p.default/logins.json",
    ".mozilla/firefox/p.default/key4.db",
    ".mozilla/firefox/p.default/places.sqlite",
    ".mozilla/firefox/p.default/storage/default",
    ".mozilla/firefox/p.default/sessionstore-backups",
    "snap/firefox/common/.mozilla/firefox/p.default/logins.json",
]

CACHE_DIRS = [
    ".cache/google-chrome/Default/Cache/Cache_Data",
    ".cache/google-chrome/Default/Code Cache/js",
    ".cache/google-chrome/ShaderCache",
    ".config/google-chrome/Default/GPUCache",
    ".config/google-chrome/Default/Service Worker/CacheStorage",
    ".cache/mozilla/firefox/p.default/cache2",
    "snap/firefox/common/.cache/mozilla/firefox/p.default/cache2",
    ".var/app/org.mozilla.firefox/cache/mozilla/firefox/p.default/cache2",
]


@pytest.fixture
def browser_tree(make_tree):
    for relative in CACHE_DIRS:
        make_tree(relative + "/blob.bin", "x" * 5000)
    for relative in IDENTITY_FILES:
        if relative.endswith(("leveldb", "IndexedDB", "Database", "default", "sessionstore-backups")):
            make_tree(relative + "/data", "secret")
        else:
            make_tree(relative, "secret")
    return make_tree


@pytest.mark.parametrize("relative", IDENTITY_FILES)
def test_identity_data_is_never_deletable(browser_tree, fake_home, relative):
    assert evaluate(str(fake_home / relative)) is Decision.BLOCKED_NEVER_DELETE


@pytest.mark.parametrize("relative", CACHE_DIRS)
def test_cache_directories_are_deletable(browser_tree, fake_home, relative):
    assert evaluate(str(fake_home / relative)) is Decision.ALLOW


def test_scanner_finds_caches_in_all_three_packagings(browser_tree, fake_home):
    events = []
    app_caches.scan(events.append, CancelToken())
    found = {path for e in events if e.kind == "item" for path in e.item.paths}

    assert any(".cache/google-chrome" in p for p in found), "deb Chrome cache not found"
    assert any("snap/firefox/common/.cache" in p for p in found), "snap Firefox cache not found"
    assert any(".var/app/org.mozilla.firefox/cache" in p for p in found), "flatpak Firefox cache not found"


def test_scanner_never_offers_identity_data(browser_tree, fake_home):
    events = []
    app_caches.scan(events.append, CancelToken())
    found = {path for e in events if e.kind == "item" for path in e.item.paths}

    for relative in IDENTITY_FILES:
        assert str(fake_home / relative) not in found, f"scanner offered {relative}"


def test_chrome_caches_merge_into_one_row(browser_tree):
    events = []
    app_caches.scan(events.append, CancelToken())
    chrome_rows = [
        e.item for e in events
        if e.kind == "item" and any("google-chrome" in p for p in e.item.paths)
    ]
    assert len(chrome_rows) == 1, "Chrome should be one row, not one per cache directory"
    assert len(chrome_rows[0].locations) > 1
