"""Shared scanning machinery.

Discovery and sizing are separated, exactly as upstream's DevScanner does:
rows appear the moment their path is found, and their sizes fill in afterwards
from a worker pool. Walking a large tree to size it is the slow part, and the
UI should never wait for it.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator

from ..models import Location, SafetyLevel, ScanItem
from ..safety.explanations import database
from ..safety.policy import is_offered_for_cleanup
from ..safety.tiers import evaluate_level
from ..services import prefs

#: Directories never descended into while walking the home tree.
SKIP_WALK_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".Trash",
        "Trash",
        ".cache",
        "snap",
        ".var",
        ".local",
        ".steam",
        ".wine",
        "Steam",
        "lost+found",
    }
)

#: A directory with more entries than this is not descended into. Mirrors
#: upstream's maxDirectoryEntriesBeforeSkip: past this point it is a data
#: dump, not a project tree, and walking it costs more than it finds.
MAX_DIRECTORY_ENTRIES = 2000


class CancelToken:
    """Cooperative cancellation for a running scan."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()


@dataclass
class ScanEvent:
    """One message from a scanner to the UI pump."""

    kind: str  # "status" | "item" | "size" | "done"
    status: str = ""
    item: ScanItem | None = None
    item_id: str = ""
    sizes: dict[str, int] | None = None
    last_modified: float = 0.0


EventSink = Callable[[ScanEvent], None]


# -- sizing -----------------------------------------------------------------

def path_size(path: str) -> tuple[int, float]:
    """Allocated bytes and newest mtime for a file or directory tree.

    Allocated size (st_blocks) rather than apparent size, so the number
    matches what the disk actually gets back. Sparse files and hard links
    otherwise inflate the total wildly.
    """
    try:
        stat = os.lstat(path)
    except OSError:
        return 0, 0.0

    if not os.path.isdir(path) or os.path.islink(path):
        size = stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
        return size, stat.st_mtime

    total = 0
    newest = stat.st_mtime
    seen_inodes: set[tuple[int, int]] = set()
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        st = entry.stat(follow_symlinks=False)
                        if st.st_nlink > 1:
                            key = (st.st_dev, st.st_ino)
                            if key in seen_inodes:
                                continue
                            seen_inodes.add(key)
                        total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                        if st.st_mtime > newest:
                            newest = st.st_mtime
                    except OSError:
                        continue
        except OSError:
            continue
    return total, newest


def subdirectories(root: str, *, skip_hidden: bool = False) -> Iterator[os.DirEntry]:
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if skip_hidden and entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        yield entry
                except OSError:
                    continue
    except OSError:
        return


def directory_is_too_large(path: str) -> bool:
    count = 0
    try:
        with os.scandir(path) as entries:
            for _ in entries:
                count += 1
                if count > MAX_DIRECTORY_ENTRIES:
                    return True
    except OSError:
        return True
    return False


def is_empty(path: str) -> bool:
    try:
        with os.scandir(path) as entries:
            for _ in entries:
                return False
    except OSError:
        return True
    return True


# -- row construction -------------------------------------------------------

def build_item(
    path: str,
    *,
    source: str,
    definition_key: str | None = None,
    name: str | None = None,
    level: SafetyLevel | None = None,
    scope: str | None = None,
    icon_name: str = "folder-symbolic",
    route=None,
    reinstall_hint: str = "",
    note: str = "",
    user_selected: bool = False,
    fallback_key: str | None = None,
) -> ScanItem | None:
    """Make a row for `path`, or None if it must not be shown.

    A row is only produced when the safety policy allows the path *and*
    something can classify it. Unknown folders are left out of the list
    entirely — the app only ever shows what it knows about.
    """
    from ..models import DeletionRoute

    if prefs.is_excluded(path):
        return None
    if not is_offered_for_cleanup(path, user_selected=user_selected):
        return None

    db = database()
    folder = os.path.basename(path.rstrip("/"))
    entry = db.resolve(key=definition_key, folder_name=folder, path=path, scope=scope)
    if entry is None and fallback_key:
        # A folder under a root that is cache by definition still gets a row;
        # it just carries the generic explanation rather than a specific one.
        entry = db.by_key(fallback_key)

    resolved_level = level
    if resolved_level is None and entry is not None:
        resolved_level = entry.level
    if resolved_level is None:
        resolved_level = evaluate_level(folder, path)
    if resolved_level is None:
        return None

    if name:
        display_name = name
    elif entry is not None and not entry.key.startswith("generic-"):
        display_name = entry.display_name
    else:
        # A generic entry describes a whole class of folders, so the row is
        # titled after the folder itself rather than repeating "App Cache".
        display_name = prettify(folder)
    explanation = entry.explanation if entry else ""
    if not explanation:
        # Without an explanation the row cannot meet the app's own standard:
        # every item carries a plain-English reason. Leave it out.
        return None

    return ScanItem(
        item_id=ScanItem.make_id(definition_key, path),
        name=display_name,
        locations=[Location(path=path, key=definition_key or folder)],
        safety=resolved_level,
        source=source,
        definition_key=definition_key or (entry.key if entry else None),
        explanation=explanation,
        icon_name=icon_name,
        route=route or DeletionRoute.REMOVE,
        reinstall_hint=reinstall_hint,
        note=note,
        size_resolved=False,
        user_selected=user_selected,
    )


#: Cosmetic cleanups for folder names shown as a row title.
_NAME_FIXUPS = {
    "vlc": "VLC", "gimp": "GIMP", "obs": "OBS", "vscode": "VS Code",
    "jetbrains": "JetBrains", "npm": "npm", "pip": "pip", "nvim": "Neovim",
    "gstreamer": "GStreamer", "libreoffice": "LibreOffice", "ibus": "IBus",
    "qt": "Qt", "kde": "KDE", "gnome": "GNOME", "xorg": "Xorg",
}


def prettify(folder: str) -> str:
    """A readable title for a bare cache folder name.

    `com.spotify.Client` becomes `Spotify`, `google-chrome` becomes
    `Google Chrome`. Reverse-DNS ids are common under ~/.var/app and ~/.cache.
    """
    name = folder.strip()
    if not name:
        return folder
    parts = name.split(".")
    if len(parts) >= 3 and parts[0] in ("com", "org", "io", "net", "dev", "app", "me", "co"):
        name = parts[-1] if parts[-1].lower() not in ("client", "desktop", "app") else parts[-2]
    name = name.lstrip(".")
    lowered = name.lower()
    if lowered in _NAME_FIXUPS:
        return _NAME_FIXUPS[lowered]
    words = [w for w in name.replace("_", "-").replace(" ", "-").split("-") if w]
    out = []
    for word in words:
        low = word.lower()
        if low in _NAME_FIXUPS:
            out.append(_NAME_FIXUPS[low])
        elif word.isupper():
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out) or folder


def merge_by_definition(items: list[ScanItem]) -> list[ScanItem]:
    """Collapse rows sharing a definition key into one multi-location row.

    Port of DefinitionCacheGrouper: one app with four cache directories should
    be one line in the list, not four.

    A `generic-` key is not a merge key. It names a whole *class* of folders
    (see build_item), so merging on it would collapse every unrecognised
    ~/.cache folder into a single row wearing the first folder's name.
    """
    grouped: dict[str, ScanItem] = {}
    ordered: list[ScanItem] = []

    for item in items:
        key = item.definition_key
        if not key or key.startswith("generic-"):
            ordered.append(item)
            continue
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = item
            ordered.append(item)
            continue
        merged_locations = list(existing.locations)
        known = {loc.path for loc in merged_locations}
        for loc in item.locations:
            if loc.path not in known:
                merged_locations.append(loc)
        existing.locations = merged_locations
        # Keep the more cautious tier when two locations disagree.
        if item.safety is SafetyLevel.MEDIUM:
            existing.safety = SafetyLevel.MEDIUM
    return ordered


# -- size pump --------------------------------------------------------------

def resolve_sizes(
    items: list[ScanItem],
    sink: EventSink,
    token: CancelToken,
    *,
    workers: int = 8,
    drop_empty: bool = True,
) -> None:
    """Size every row's locations off the main thread, emitting as they land.

    Rows that turn out to be empty are reported with a zero size so the UI can
    drop them: an empty cache folder is noise, not a finding.
    """

    def size_one(item: ScanItem) -> None:
        if token.cancelled:
            return
        sizes: dict[str, int] = {}
        newest = 0.0
        for loc in item.locations:
            size, mtime = path_size(loc.path)
            sizes[loc.path] = size
            newest = max(newest, mtime)
        if token.cancelled:
            return
        if drop_empty and sum(sizes.values()) == 0:
            sink(ScanEvent(kind="size", item_id=item.item_id, sizes=sizes, last_modified=newest))
            return
        sink(ScanEvent(kind="size", item_id=item.item_id, sizes=sizes, last_modified=newest))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(size_one, items))
