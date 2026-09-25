"""Project artifacts are only ever proposed next to their project marker."""

from __future__ import annotations

import os
import time

import pytest

from purgelinux.scanners import projects
from purgelinux.scanners.base import CancelToken
from purgelinux.services import prefs


@pytest.fixture(autouse=True)
def show_all_projects(fake_home):
    prefs.reset_cache()
    prefs.set_value("stale_project_days", 0)
    yield
    prefs.reset_cache()


def _scan() -> dict[str, str]:
    events = []
    projects.scan(events.append, CancelToken())
    return {e.item.path: e.item.name for e in events if e.kind == "item"}


def test_node_modules_needs_a_package_json(make_tree, fake_home):
    make_tree("code/with/package.json", "{}")
    make_tree("code/with/node_modules/lib/index.js", "x" * 2000)
    make_tree("code/without/node_modules/lib/index.js", "x" * 2000)

    found = _scan()
    assert str(fake_home / "code/with/node_modules") in found
    assert str(fake_home / "code/without/node_modules") not in found


def test_lone_build_folder_is_not_offered(make_tree, fake_home):
    make_tree("Documents/build/output.bin", "x" * 5000)
    assert str(fake_home / "Documents/build") not in _scan()


def test_flutter_artifacts_are_found(make_tree, fake_home):
    make_tree("dev/app/pubspec.yaml", "name: app")
    make_tree("dev/app/pubspec.lock", "")
    for relative in ("build", ".dart_tool", "android/.gradle", "android/app/build"):
        make_tree(f"dev/app/{relative}/blob.bin", "x" * 3000)

    found = _scan()
    for relative in ("build", ".dart_tool", "android/.gradle", "android/app/build"):
        assert str(fake_home / "dev/app" / relative) in found, relative


def test_refuse_when_present_disqualifies(make_tree, fake_home):
    make_tree("dev/dirty/pyproject.toml", "")
    make_tree("dev/dirty/.venv/lib/site.py", "x" * 2000)
    make_tree("dev/dirty/.venv/app.db", "data")

    make_tree("dev/clean/pyproject.toml", "")
    make_tree("dev/clean/.venv/lib/site.py", "x" * 2000)

    found = _scan()
    assert str(fake_home / "dev/dirty/.venv") not in found
    assert str(fake_home / "dev/clean/.venv") in found


def test_missing_lockfile_is_flagged(make_tree, fake_home):
    make_tree("dev/nolock/package.json", "{}")
    make_tree("dev/nolock/node_modules/lib/i.js", "x" * 2000)

    events = []
    projects.scan(events.append, CancelToken())
    rows = [e.item for e in events if e.kind == "item" and "nolock" in e.item.path]
    assert rows and rows[0].extra["has_lockfile"] is False
    assert "lockfile" in rows[0].note.lower()


def test_stale_threshold_hides_recent_projects(make_tree, fake_home):
    make_tree("dev/recent/Cargo.toml", "")
    make_tree("dev/recent/Cargo.lock", "")
    make_tree("dev/recent/target/debug.bin", "x" * 4000)

    prefs.set_value("stale_project_days", 30)
    assert str(fake_home / "dev/recent/target") not in _scan()

    # Age the project's own files past the threshold.
    old = time.time() - 60 * 86400
    for name in ("Cargo.toml", "Cargo.lock"):
        target = fake_home / "dev/recent" / name
        os.utime(target, (old, old))
    assert str(fake_home / "dev/recent/target") in _scan()


def test_reinstall_hint_is_carried(make_tree, fake_home):
    make_tree("dev/crate/Cargo.toml", "")
    make_tree("dev/crate/Cargo.lock", "")
    make_tree("dev/crate/target/debug.bin", "x" * 4000)

    events = []
    projects.scan(events.append, CancelToken())
    rows = [e.item for e in events if e.kind == "item"]
    assert rows and rows[0].reinstall_hint == "cargo build"
