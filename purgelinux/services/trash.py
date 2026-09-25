"""Freedesktop Trash implementation.

Replaces macOS `FileManager.trashItem`. Implemented directly rather than via
`Gio.File.trash()` so the deletion engine has no GLib dependency, the
cross-filesystem case is handled explicitly instead of failing opaquely, and
the tests exercise the same code that runs in production.

Spec: https://specifications.freedesktop.org/trash-spec/trashspec-1.0.html
"""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from .. import paths


class TrashError(Exception):
    """Trashing failed for a reason the caller should surface."""


class CrossDeviceError(TrashError):
    """The file lives on another filesystem with no usable trash directory.

    The caller must ask the user whether to delete permanently instead of
    quietly falling back to an unrecoverable removal.
    """


@dataclass(frozen=True)
class TrashResult:
    original_path: str
    trashed_path: str
    info_path: str
    size_bytes: int


def _mount_point(path: str) -> str:
    path = os.path.abspath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            return path
        path = parent
    return path


def home_trash_dir() -> str:
    return paths.trash_dir()


def _nearest_existing(path: str) -> str:
    """Closest ancestor that exists, for device comparison before mkdir."""
    path = os.path.abspath(path)
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _same_filesystem(path: str, trash_root: str) -> bool:
    """Whether `path` can be renamed into `trash_root` without a copy.

    The trash directory usually does not exist yet on a fresh machine, so the
    comparison walks up to the nearest ancestor that does.
    """
    try:
        return os.lstat(path).st_dev == os.stat(_nearest_existing(trash_root)).st_dev
    except OSError:
        return False


def _ensure_trash_dirs(trash_root: str) -> tuple[str, str]:
    files_dir = os.path.join(trash_root, "files")
    info_dir = os.path.join(trash_root, "info")
    os.makedirs(files_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)
    return files_dir, info_dir


def _volume_trash_dir(mount: str) -> str | None:
    """Per-volume trash for files outside the home filesystem.

    The spec allows either `$topdir/.Trash/$uid` (only when the admin created
    `.Trash` with the sticky bit) or `$topdir/.Trash-$uid`, which any user may
    create. We never create `$topdir/.Trash` ourselves.
    """
    uid = os.getuid()
    shared = os.path.join(mount, ".Trash")
    if os.path.isdir(shared) and not os.path.islink(shared):
        try:
            mode = os.stat(shared).st_mode
        except OSError:
            mode = 0
        if mode & 0o1000:  # sticky bit
            candidate = os.path.join(shared, str(uid))
            try:
                os.makedirs(candidate, exist_ok=True)
                return candidate
            except OSError:
                pass
    candidate = os.path.join(mount, f".Trash-{uid}")
    try:
        os.makedirs(candidate, exist_ok=True)
        return candidate
    except OSError:
        return None


def _unique_name(files_dir: str, info_dir: str, name: str) -> str:
    """A basename free in both files/ and info/, per the spec's requirement."""
    stem, ext = os.path.splitext(name)
    candidate = name
    counter = 1
    while os.path.lexists(os.path.join(files_dir, candidate)) or os.path.lexists(
        os.path.join(info_dir, candidate + ".trashinfo")
    ):
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    return candidate


def _write_trashinfo(info_path: str, original_path: str, relative_to: str | None) -> None:
    if relative_to:
        stored = os.path.relpath(original_path, relative_to)
    else:
        stored = original_path
    body = (
        "[Trash Info]\n"
        f"Path={quote(stored, safe='/')}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    # Exclusive create: the spec requires the info file to be claimed before
    # the data is moved, so two processes cannot race onto the same name.
    fd = os.open(info_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)


def size_of(path: str) -> int:
    """Allocated size of a file or directory tree, in bytes."""
    try:
        stat = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            st = entry.stat(follow_symlinks=False)
                            total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def trash(path: str) -> TrashResult:
    """Move `path` to the trash, returning where it landed.

    Raises CrossDeviceError when the file cannot be trashed at all, so the
    caller can ask before deleting permanently.
    """
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        raise TrashError(f"{path} does not exist")

    size = size_of(path)
    home_trash = home_trash_dir()
    relative_to: str | None = None

    same_device = _same_filesystem(path, home_trash)

    if same_device:
        trash_root = home_trash
    else:
        mount = _mount_point(path)
        volume_trash = _volume_trash_dir(mount)
        if volume_trash is None:
            raise CrossDeviceError(
                f"{path} is on another filesystem with no writable trash directory"
            )
        trash_root = volume_trash
        relative_to = mount

    files_dir, info_dir = _ensure_trash_dirs(trash_root)
    name = _unique_name(files_dir, info_dir, os.path.basename(path.rstrip("/")) or "file")
    info_path = os.path.join(info_dir, name + ".trashinfo")
    target = os.path.join(files_dir, name)

    _write_trashinfo(info_path, path, relative_to)
    try:
        os.rename(path, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            os.unlink(info_path)
            raise TrashError(str(exc)) from exc
        # Same filesystem was expected but rename still crossed a boundary
        # (bind mount, overlay). Copy then remove.
        try:
            shutil.move(path, target)
        except OSError as move_exc:
            os.unlink(info_path)
            raise TrashError(str(move_exc)) from move_exc

    return TrashResult(original_path=path, trashed_path=target, info_path=info_path, size_bytes=size)


def trash_size() -> int:
    return size_of(os.path.join(home_trash_dir(), "files"))
