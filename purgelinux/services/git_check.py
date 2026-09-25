"""Dirty-worktree guard for project artifacts.

Port of Purge's GitStatusChecker. Removing `node_modules` from a repo with
uncommitted work is not dangerous in itself, but it is the moment a user most
wants to be asked first.
"""

from __future__ import annotations

import os
import subprocess
from enum import Enum
from functools import lru_cache


class GitStatus(Enum):
    UNKNOWN = "unknown"
    NOT_A_REPO = "not_a_repo"
    CLEAN = "clean"
    DIRTY = "dirty"


def _find_repo_root(path: str) -> str | None:
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


@lru_cache(maxsize=512)
def status(path: str) -> GitStatus:
    """Worktree status for the repo containing `path`, if any."""
    root = _find_repo_root(path)
    if root is None:
        return GitStatus.NOT_A_REPO
    try:
        completed = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return GitStatus.UNKNOWN
    if completed.returncode != 0:
        return GitStatus.UNKNOWN
    return GitStatus.DIRTY if completed.stdout.strip() else GitStatus.CLEAN


def clear_cache() -> None:
    status.cache_clear()
