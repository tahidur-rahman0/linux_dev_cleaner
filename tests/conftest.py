"""Shared fixtures.

Every test runs against a temporary HOME. Nothing in this suite is allowed to
look at, let alone touch, the real home directory.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point the whole app at an empty temporary home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("PURGE_DRY_RUN", raising=False)

    from purgelinux import paths

    paths._user_dirs_from_config.cache_clear()
    yield home
    paths._user_dirs_from_config.cache_clear()


@pytest.fixture
def make_tree(fake_home):
    """Create a directory (and optional file) under the fake home."""

    def _make(relative: str, contents: str | None = None) -> str:
        target = fake_home / relative
        if contents is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
        return str(target)

    return _make
