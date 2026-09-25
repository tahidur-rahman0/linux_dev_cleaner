"""App Caches scanner.

Equivalent of Purge's CacheScanner + CacheDiscoveryPaths, rewritten for the
XDG layout.

The trap this module exists to get right: on Linux, Chromium-family browsers
follow the XDG split, so the HTTP cache lives under ~/.cache/<browser>/ while
the profile lives under ~/.config/<browser>/ — and a handful of cache dirs
(GPUCache, Service Worker/CacheStorage) sit on the config side anyway.
Electron apps do *not* follow the split: their caches stay in ~/.config/<App>.
Both roots have to be scanned, and the profile directory itself is never
touched.
"""

from __future__ import annotations

import os

from .. import paths
from ..models import DeletionRoute, ScanItem
from ..services import prefs
from .base import (
    CancelToken,
    EventSink,
    ScanEvent,
    build_item,
    merge_by_definition,
    resolve_sizes,
    subdirectories,
)

SOURCE = "app_caches"

# -- browser catalogs -------------------------------------------------------

#: (directory name under .cache/.config, explanation key, process names)
CHROMIUM_BROWSERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("google-chrome", "chrome-cache", ("chrome", "google-chrome")),
    ("google-chrome-beta", "chrome-cache", ("chrome",)),
    ("google-chrome-unstable", "chrome-cache", ("chrome",)),
    ("chromium", "chromium-cache", ("chromium",)),
    ("BraveSoftware/Brave-Browser", "brave-cache", ("brave",)),
    ("microsoft-edge", "edge-cache", ("msedge", "microsoft-edge")),
    ("vivaldi", "vivaldi-cache", ("vivaldi",)),
    ("opera", "opera-cache", ("opera",)),
    ("thorium", "chromium-cache", ("thorium",)),
)

#: Cache directories inside a Chromium profile, on the ~/.cache side.
CHROMIUM_PROFILE_CACHE_DIRS = (
    "Cache/Cache_Data",
    "Code Cache/js",
    "Code Cache/wasm",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
)

#: Cache directories inside a Chromium profile, on the ~/.config side.
CHROMIUM_PROFILE_CONFIG_DIRS = (
    "GPUCache",
    "Service Worker/CacheStorage",
    "Service Worker/ScriptCache",
    "component_crx_cache",
)

#: Browser-wide (not per-profile) cache directories.
CHROMIUM_ROOT_CACHE_DIRS = (
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "DawnWebGPUCache",
)

#: (relative path under a cache root, explanation key, process names)
FIREFOX_BROWSERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("mozilla/firefox", "firefox-cache", ("firefox",)),
    ("librewolf", "firefox-cache", ("librewolf",)),
    ("zen", "firefox-cache", ("zen",)),
    ("waterfox", "firefox-cache", ("waterfox",)),
    ("thunderbird", "thunderbird-cache", ("thunderbird",)),
)

FIREFOX_PROFILE_CACHE_DIRS = (
    ("cache2", None),
    ("startupCache", "firefox-startup-cache"),
    ("thumbnails", None),
    ("jumpListCache", None),
    ("safebrowsing", "browser-safebrowsing"),
)

#: Snap and Flatpak keep their own copies of the whole cache tree.
SNAP_BROWSER_ROOTS = (
    ("firefox", "mozilla/firefox", "firefox-cache", ("firefox",)),
    ("chromium", "chromium", "chromium-cache", ("chromium",)),
    ("thunderbird", "thunderbird", "thunderbird-cache", ("thunderbird",)),
)

FLATPAK_BROWSER_ROOTS = (
    ("org.mozilla.firefox", "mozilla/firefox", "firefox-cache", ("firefox",)),
    ("org.chromium.Chromium", "chromium", "chromium-cache", ("chromium",)),
    ("com.google.Chrome", "google-chrome", "chrome-cache", ("chrome",)),
    ("com.brave.Browser", "BraveSoftware/Brave-Browser", "brave-cache", ("brave",)),
    ("app.zen_browser.zen", "zen", "firefox-cache", ("zen",)),
    ("org.mozilla.Thunderbird", "thunderbird", "thunderbird-cache", ("thunderbird",)),
)

#: Electron desktop apps keep their cache under ~/.config/<App>/.
ELECTRON_CACHE_DIRS = (
    "Cache",
    "Cache/Cache_Data",
    "Code Cache/js",
    "Code Cache/wasm",
    "GPUCache",
    "CachedData",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "Crashpad/completed",
    "component_crx_cache",
    "Service Worker/CacheStorage",
    "Service Worker/ScriptCache",
)

#: Top-level ~/.cache entries handled elsewhere, so the generic sweep skips
#: them rather than producing a second, coarser row for the same bytes.
CACHE_CLAIMED_BY_OTHER_SCANNERS: frozenset[str] = frozenset(
    {
        "mozilla", "google-chrome", "google-chrome-beta", "google-chrome-unstable",
        "chromium", "BraveSoftware", "microsoft-edge", "vivaldi", "opera", "thorium",
        "librewolf", "zen", "waterfox", "thunderbird",
        "yarn", "pip", "uv", "poetry", "pipenv", "pre-commit", "go-build",
        "golangci-lint", "ms-playwright", "puppeteer", "Cypress", "node-gyp",
        "electron", "typescript", "deno", "bazel", "bazelisk", "zig", "ccache",
        "sccache", "composer", "JetBrains", "Google", "huggingface", "lm-studio",
        "coursier", "pnpm", "unity3d", "nvm", "electron-gyp",
    }
)


def _profile_dirs(root: str) -> list[str]:
    """Chromium profile directories, plus the root itself for flat layouts."""
    found: list[str] = []
    for entry in subdirectories(root):
        name = entry.name
        if name == "Default" or name.startswith("Profile ") or name in ("Guest Profile", "System Profile"):
            found.append(entry.path)
    return found or [root]


def _add(items: list[ScanItem], path: str, key: str, **kwargs) -> None:
    if not os.path.isdir(path):
        return
    item = build_item(path, source=SOURCE, definition_key=key, scope="app_caches", **kwargs)
    if item is not None:
        items.append(item)


def _running_note(process_names: tuple[str, ...], label: str) -> str:
    if not process_names:
        return ""
    if paths.is_running(process_names):
        return f"{label} is running — close it to reclaim the full amount."
    return ""


def _chromium_items(items: list[ScanItem]) -> None:
    cache_home = paths.cache_home()
    config_home = paths.config_home()

    for directory, key, processes in CHROMIUM_BROWSERS:
        cache_root = os.path.join(cache_home, directory)
        config_root = os.path.join(config_home, directory)
        if not os.path.isdir(cache_root) and not os.path.isdir(config_root):
            continue
        label = directory.split("/")[-1].replace("-", " ").title()
        note = _running_note(processes, label)

        for relative in CHROMIUM_ROOT_CACHE_DIRS:
            _add(items, os.path.join(cache_root, relative), key, note=note)

        for profile in _profile_dirs(cache_root):
            for relative in CHROMIUM_PROFILE_CACHE_DIRS:
                _add(items, os.path.join(profile, relative), key, note=note)

        for profile in _profile_dirs(config_root):
            for relative in CHROMIUM_PROFILE_CONFIG_DIRS:
                _add(items, os.path.join(profile, relative), key, note=note)


def _firefox_profiles(root: str) -> list[str]:
    return [entry.path for entry in subdirectories(root)]


def _firefox_items_under(items: list[ScanItem], cache_root: str, key: str, note: str) -> None:
    for profile in _firefox_profiles(cache_root):
        for relative, specific_key in FIREFOX_PROFILE_CACHE_DIRS:
            _add(items, os.path.join(profile, relative), specific_key or key, note=note)


def _firefox_items(items: list[ScanItem]) -> None:
    cache_home = paths.cache_home()
    for relative, key, processes in FIREFOX_BROWSERS:
        root = os.path.join(cache_home, relative)
        if not os.path.isdir(root):
            continue
        label = relative.split("/")[-1].title()
        _firefox_items_under(items, root, key, _running_note(processes, label))


def _snap_browser_items(items: list[ScanItem]) -> None:
    snap_root = paths.snap_root()
    for snap_name, relative, key, processes in SNAP_BROWSER_ROOTS:
        for variant in ("common", "current"):
            root = os.path.join(snap_root, snap_name, variant, ".cache", relative)
            if not os.path.isdir(root):
                continue
            note = _running_note(processes, snap_name.title())
            if "mozilla" in relative or relative in ("zen", "thunderbird", "librewolf"):
                _firefox_items_under(items, root, key, note)
            else:
                for profile in _profile_dirs(root):
                    for sub in CHROMIUM_PROFILE_CACHE_DIRS:
                        _add(items, os.path.join(profile, sub), key, note=note)


def _flatpak_browser_items(items: list[ScanItem]) -> None:
    flatpak_root = paths.flatpak_root()
    for app_id, relative, key, processes in FLATPAK_BROWSER_ROOTS:
        root = os.path.join(flatpak_root, app_id, "cache", relative)
        if not os.path.isdir(root):
            continue
        note = _running_note(processes, app_id.split(".")[-1].title())
        if "mozilla" in relative or relative in ("zen", "thunderbird", "librewolf"):
            _firefox_items_under(items, root, key, note)
        else:
            for profile in _profile_dirs(root):
                for sub in CHROMIUM_PROFILE_CACHE_DIRS:
                    _add(items, os.path.join(profile, sub), key, note=note)


def _electron_items(items: list[ScanItem]) -> None:
    """Electron app caches under ~/.config/<App>/.

    Only directories that already look like an Electron profile are inspected,
    so this does not wander through every app's configuration.
    """
    config_home = paths.config_home()
    browser_dirs = {d.split("/")[0] for d, _, _ in CHROMIUM_BROWSERS}

    for entry in subdirectories(config_home, skip_hidden=True):
        if entry.name in browser_dirs:
            continue
        marker = os.path.join(entry.path, "Preferences")
        looks_electron = os.path.exists(marker) or os.path.isdir(os.path.join(entry.path, "Cache"))
        if not looks_electron:
            continue
        for relative in ELECTRON_CACHE_DIRS:
            path = os.path.join(entry.path, relative)
            if not os.path.isdir(path):
                continue
            item = build_item(
                path,
                source=SOURCE,
                definition_key=f"electron:{entry.name}",
                name=None,
                scope="app_caches",
                fallback_key="electron-app-cache",
            )
            if item is not None:
                # One row per app, named after the app rather than the folder.
                item.name = _electron_display_name(entry.name)
                items.append(item)


def _electron_display_name(folder: str) -> str:
    from .base import prettify

    return prettify(folder)


def _generic_cache_items(items: list[ScanItem]) -> None:
    """Every other top-level folder in ~/.cache.

    Anything under ~/.cache is cache by definition, so a folder with no
    specific catalog entry still gets a row — it just carries the generic
    explanation instead of a tailored one.
    """
    cache_home = paths.cache_home()
    for entry in subdirectories(cache_home):
        if entry.name in CACHE_CLAIMED_BY_OTHER_SCANNERS:
            continue
        item = build_item(
            entry.path,
            source=SOURCE,
            definition_key=None,
            scope="app_caches",
            fallback_key="generic-cache",
        )
        if item is not None:
            items.append(item)


def _flatpak_cache_items(items: list[ScanItem]) -> None:
    for entry in subdirectories(paths.flatpak_root()):
        cache_root = os.path.join(entry.path, "cache")
        if not os.path.isdir(cache_root):
            continue
        if any(entry.name == app_id for app_id, _, _, _ in FLATPAK_BROWSER_ROOTS):
            continue
        item = build_item(
            cache_root,
            source=SOURCE,
            definition_key=f"flatpak:{entry.name}",
            scope="app_caches",
            fallback_key="generic-flatpak-cache",
        )
        if item is not None:
            item.name = _electron_display_name(entry.name)
            items.append(item)


def _snap_cache_items(items: list[ScanItem]) -> None:
    snap_names = {name for name, _, _, _ in SNAP_BROWSER_ROOTS}
    for entry in subdirectories(paths.snap_root()):
        if entry.name in snap_names:
            continue
        for variant in ("common", "current"):
            cache_root = os.path.join(entry.path, variant, ".cache")
            if not os.path.isdir(cache_root):
                continue
            item = build_item(
                cache_root,
                source=SOURCE,
                definition_key=f"snap:{entry.name}",
                scope="app_caches",
                fallback_key="generic-snap-cache",
            )
            if item is not None:
                item.name = _electron_display_name(entry.name)
                items.append(item)


def scan(sink: EventSink, token: CancelToken) -> None:
    """Discover every app cache row, then size them off the main thread."""
    sink(ScanEvent(kind="status", status="Looking for app caches…"))

    items: list[ScanItem] = []
    steps = (
        ("Checking browsers…", _chromium_items),
        (None, _firefox_items),
        (None, _snap_browser_items),
        (None, _flatpak_browser_items),
        ("Checking desktop apps…", _electron_items),
        (None, _flatpak_cache_items),
        (None, _snap_cache_items),
        ("Checking ~/.cache…", _generic_cache_items),
    )
    for status, step in steps:
        if token.cancelled:
            return
        if status:
            sink(ScanEvent(kind="status", status=status))
        step(items)

    items = merge_by_definition(items)
    for item in items:
        if token.cancelled:
            return
        sink(ScanEvent(kind="item", item=item))

    sink(ScanEvent(kind="status", status="Measuring…"))
    resolve_sizes(items, sink, token)
    sink(ScanEvent(kind="done"))
