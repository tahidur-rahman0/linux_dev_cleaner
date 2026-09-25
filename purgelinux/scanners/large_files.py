"""Large Files scanner.

Personal files, not rebuildable caches — so everything here is routed to the
Trash and marked `user_selected`, which is the only route that bypasses the
cache allowlist (and still obeys every never-delete rule).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .. import paths
from ..models import DeletionRoute, Location, SafetyLevel, ScanItem
from ..services import prefs
from .base import SKIP_WALK_NAMES, CancelToken, EventSink, ScanEvent

SOURCE = "large_files"

CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "video": frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts"}),
    "audio": frozenset({".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".opus", ".aiff", ".wma"}),
    "image": frozenset({".jpg", ".jpeg", ".png", ".gif", ".tiff", ".tif", ".bmp", ".webp", ".heic", ".raw", ".cr2", ".nef", ".arw", ".psd", ".xcf", ".svg"}),
    "pdf": frozenset({".pdf"}),
    "archive": frozenset({".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".iso", ".img", ".dmg", ".deb", ".rpm", ".appimage", ".snap", ".flatpak", ".zst"}),
    "document": frozenset({".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".epub", ".mobi", ".txt", ".md", ".csv"}),
    "ai_model": frozenset({".gguf", ".ggml", ".safetensors", ".bin", ".pt", ".pth", ".onnx", ".h5", ".ckpt"}),
}

CATEGORY_LABELS = {
    "video": "Video",
    "audio": "Audio",
    "image": "Image",
    "pdf": "PDF",
    "archive": "Archive",
    "document": "Document",
    "ai_model": "AI Model",
    "other": "Other",
}

CATEGORY_ICONS = {
    "video": "video-x-generic-symbolic",
    "audio": "audio-x-generic-symbolic",
    "image": "image-x-generic-symbolic",
    "pdf": "x-office-document-symbolic",
    "archive": "package-x-generic-symbolic",
    "document": "text-x-generic-symbolic",
    "ai_model": "applications-science-symbolic",
    "other": "text-x-generic-symbolic",
}

#: Directories skipped on top of the shared walk list. These are managed
#: libraries and dependency trees: deleting one file out of them breaks the
#: whole thing, so they belong to other tabs (or to nothing at all).
LARGE_FILE_SKIP = frozenset(
    {
        "node_modules", "Pods", "target", ".venv", "venv", "__pycache__",
        "build", "dist", ".git", ".gradle", "vendor", "_build", "deps",
        "Photos Library", "Pictures Library", "darktable", "digikam",
    }
)


@dataclass(frozen=True)
class LargeFileFilter:
    min_bytes: int = 100 * 1000 * 1000
    max_age_days: int = 0  # 0 = any age


def categorize(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "other"


def _source_label(path: str) -> str:
    """Which user directory a file came from, for the row's subtitle."""
    for root in paths.scan_user_dirs():
        if path.startswith(root + os.sep):
            return os.path.basename(root)
    return os.path.basename(os.path.dirname(path))


def _make_row(path: str, size: int, mtime: float) -> ScanItem:
    category = categorize(path)
    name = os.path.basename(path)
    folder = os.path.dirname(path)
    home = paths.home()
    shown_folder = folder[len(home) + 1:] if folder.startswith(home + os.sep) else folder

    return ScanItem(
        item_id=ScanItem.make_id(None, path),
        name=name,
        locations=[Location(path=path, size_bytes=size, last_modified=mtime, key=category)],
        # Personal files are never "safe to clean" automatically: they are not
        # rebuildable, so they stay out of one-click and scheduled cleaning.
        safety=SafetyLevel.MEDIUM,
        source=SOURCE,
        definition_key=None,
        explanation=f"{CATEGORY_LABELS[category]} in {shown_folder}",
        icon_name=CATEGORY_ICONS[category],
        route=DeletionRoute.TRASH,
        size_resolved=True,
        user_selected=True,
        extra={"category": category, "folder": folder, "source_label": _source_label(path)},
    )


def scan(sink: EventSink, token: CancelToken, *, filters: LargeFileFilter | None = None) -> None:
    filters = filters or LargeFileFilter(min_bytes=int(prefs.get("large_file_min_bytes")))
    sink(ScanEvent(kind="status", status="Looking for large files…"))

    import time

    cutoff = time.time() - filters.max_age_days * 86400 if filters.max_age_days else None
    found: list[ScanItem] = []

    for root in paths.scan_user_dirs():
        if token.cancelled:
            return
        sink(ScanEvent(kind="status", status=f"Scanning {os.path.basename(root)}…"))
        for item in _walk(root, filters, cutoff, token):
            found.append(item)
            sink(ScanEvent(kind="item", item=item))

    sink(ScanEvent(kind="done"))


def _walk(root: str, filters: LargeFileFilter, cutoff: float | None, token: CancelToken):
    stack = [root]
    while stack:
        if token.cancelled:
            return
        current = stack.pop()
        if prefs.is_excluded(current):
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if token.cancelled:
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.startswith(".") or entry.name in SKIP_WALK_NAMES or entry.name in LARGE_FILE_SKIP:
                                continue
                            stack.append(entry.path)
                            continue
                        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    size = stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
                    if size < filters.min_bytes:
                        continue
                    if cutoff is not None and stat.st_mtime > cutoff:
                        continue
                    if prefs.is_excluded(entry.path):
                        continue
                    yield _make_row(entry.path, size, stat.st_mtime)
        except OSError:
            continue
