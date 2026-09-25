"""The suite that must never go red.

If any of these start passing something they used to refuse, the app has
become capable of deleting something it should not.
"""

from __future__ import annotations

import os

import pytest

from purgelinux.safety.policy import Decision, evaluate


NEVER_DELETE = [
    ".ssh",
    ".ssh/id_rsa",
    ".gnupg",
    ".password-store",
    ".local/share/keyrings",
    ".config/systemd/user",
    ".config/autostart",
    ".aws",
    ".kube",
    ".mozilla",
    ".mozilla/firefox/abc.default",
    ".mozilla/firefox/abc.default/storage/default",
    "snap/firefox/common/.mozilla/firefox/p/storage/default",
    ".config/google-chrome/Default",
    ".config/google-chrome/Default/Cookies",
    ".config/google-chrome/Default/Login Data",
    ".config/google-chrome/Default/Local Storage",
    ".config/google-chrome/Default/Local Storage/leveldb",
    ".config/google-chrome/Default/IndexedDB",
    ".config/google-chrome/Default/Sessions",
    ".config/google-chrome/Default/Bookmarks",
    ".config/google-chrome/Default/Service Worker",
    ".config/google-chrome/Default/Service Worker/Database",
    ".config/google-chrome/Profile 1",
]

ALLOWED = [
    ".cache/google-chrome/Default/Cache/Cache_Data",
    ".cache/google-chrome/Default/Code Cache/js",
    ".cache/google-chrome/ShaderCache",
    ".config/google-chrome/Default/GPUCache",
    ".config/google-chrome/Default/Service Worker/CacheStorage",
    ".config/google-chrome/Default/Service Worker/ScriptCache",
    ".cache/mozilla/firefox/p.default/cache2",
    "snap/firefox/common/.cache/mozilla/firefox/p/cache2",
    ".var/app/org.mozilla.firefox/cache/mozilla/firefox/p/cache2",
    ".npm/_cacache",
    ".gradle/caches/modules-2",
    "projects/app/node_modules",
    "projects/app/target",
    ".cache/pip",
]


@pytest.mark.parametrize("relative", NEVER_DELETE)
def test_never_delete_paths_are_refused(make_tree, relative):
    path = make_tree(relative)
    assert evaluate(path) is Decision.BLOCKED_NEVER_DELETE


@pytest.mark.parametrize("relative", NEVER_DELETE)
def test_never_delete_survives_the_user_selected_route(make_tree, relative):
    """Picking a file by hand must not unlock a protected location."""
    path = make_tree(relative)
    assert evaluate(path, user_selected=True) is not Decision.ALLOW


@pytest.mark.parametrize("relative", ALLOWED)
def test_cache_paths_are_allowed(make_tree, relative):
    path = make_tree(relative)
    assert evaluate(path) is Decision.ALLOW


def test_home_itself_is_refused(fake_home):
    assert evaluate(str(fake_home)) is Decision.BLOCKED_NEVER_DELETE


def test_root_is_refused():
    assert evaluate("/") is Decision.BLOCKED_NEVER_DELETE


def test_relative_path_is_refused():
    assert evaluate("relative/path") is Decision.BLOCKED_NEVER_DELETE


def test_unknown_folder_is_not_allowlisted(make_tree):
    path = make_tree("Documents/holiday-photos")
    assert evaluate(path) is Decision.BLOCKED_NOT_ALLOWLISTED


def test_symlink_escaping_home_is_refused(fake_home, tmp_path, make_tree):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = fake_home / ".cache" / "escape"
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(outside), str(link))
    assert evaluate(str(link)) is Decision.BLOCKED_NEVER_DELETE


def test_paths_outside_home_need_the_system_route(make_tree):
    assert evaluate("/var/cache/apt/archives/foo.deb") is Decision.BLOCKED_NEVER_DELETE
    assert evaluate("/var/cache/apt/archives/foo.deb", allow_system=True) is Decision.ALLOW


def test_system_route_still_refuses_arbitrary_paths():
    for path in ("/etc/passwd", "/usr/bin/python3", "/home/someone-else/.ssh", "/boot/vmlinuz"):
        assert evaluate(path, allow_system=True) is not Decision.ALLOW


def test_system_route_refuses_the_journal_directory():
    """The journal is vacuumed through journalctl, never removed directly."""
    assert evaluate("/var/log/journal", allow_system=True) is Decision.BLOCKED_NEVER_DELETE
    assert evaluate("/var/log/journal/abc", allow_system=True) is Decision.BLOCKED_NEVER_DELETE


def test_system_route_refuses_the_prefix_root_itself():
    """Emptying the archives directory is a verb; removing it is not."""
    assert evaluate("/var/cache/apt/archives", allow_system=True) is not Decision.ALLOW


def test_user_selected_personal_file_is_allowed(make_tree):
    path = make_tree("Documents/movie.mp4", "x")
    assert evaluate(path) is Decision.BLOCKED_NOT_ALLOWLISTED
    assert evaluate(path, user_selected=True) is Decision.ALLOW


def test_user_selected_is_bounded_to_scan_directories(make_tree):
    """A hand-picked file still cannot be anywhere at all."""
    path = make_tree(".config/someapp/important.db", "x")
    assert evaluate(path, user_selected=True) is not Decision.ALLOW
