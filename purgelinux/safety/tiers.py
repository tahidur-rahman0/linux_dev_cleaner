"""Safety tiers for a folder name or path.

Linux rewrite of Purge's `SafetyTierList`. The tiering rule, applied
consistently across every catalog in the app:

  SAFE   — a package-manager download cache or build artifact that the next
           build re-fetches or regenerates transparently.
  MEDIUM — SDK payloads, toolchains, IDE indexes: technically rebuildable,
           but the cost is a long re-download or a re-index you will notice.
  None   — unknown. Never shown, never deleted.
"""

from __future__ import annotations

import os

from ..models import SafetyLevel

#: Regenerated automatically, with no visible cost to the user.
DEFINITELY_SAFE: frozenset[str] = frozenset(
    {
        # Build output and dependency trees
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "htmlcov",
        ".nyc_output",
        "coverage",
        "target",
        "build",
        "dist",
        "out",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".angular",
        ".astro",
        ".turbo",
        ".parcel-cache",
        ".vite",
        ".webpack",
        "storybook-static",
        ".dart_tool",
        ".gradle",
        ".kotlin",
        "vendor",
        "_build",
        "deps",
        "obj",
        # Cache folder names
        ".cache",
        "cache",
        "caches",
        "_cacache",
        "_npx",
        "_logs",
        "cache_data",
        "code cache",
        "gpucache",
        "shadercache",
        "grshadercache",
        "graphitedawncache",
        "dawnwebgpucache",
        "cacheddata",
        "cachedextensionvsixs",
        "component_crx_cache",
        "cachestorage",
        "scriptcache",
        "cache2",
        "startupcache",
        "thumbnails",
        "jumplistcache",
        "safebrowsing",
        "mesa_shader_cache",
        "glcache",
        "radv_builtin_shaders",
        "fontconfig",
        "daemon",
        "snapshots",
        "crashpad",
        "completed",
        "workspacestorage",
    }
)

#: Rebuildable, but with a cost the user will feel — a long re-download, a
#: re-index, or a re-sync. Never part of one-click or scheduled cleaning.
CHECK_FIRST: frozenset[str] = frozenset(
    {
        "tracker3",
        "tracker",
        "gnome-software",
        "dists",
        "system-images",
        "ndk",
        "platforms",
        "sources",
        "build-tools",
        "toolchains",
        ".konan",
        ".jdks",
        "jdks",
        "index",
        "indexes",
        "pkgs",
        "lists",
        "modules",
    }
)

#: Directories whose *contents* are user data even though the name says cache.
#: Matched on the full path, not the folder name.
CHECK_FIRST_PATH_FRAGMENTS: tuple[str, ...] = (
    "/dropbox",
    "/nextcloud",
    "/owncloud",
    "/insync",
    "/megasync",
    "/onedrive",
    "/keybase",
    "/syncthing",
    "/.local/share/gnome-shell",
)

#: Path fragments that settle the tier regardless of folder name.
SAFE_PATH_FRAGMENTS: tuple[str, ...] = (
    "/service worker/cachestorage",
    "/service worker/scriptcache",
    "/gpucache",
    "/shadercache",
    "/grshadercache",
    "/code cache",
    "/cache_data",
    "/cacheddata",
    "/component_crx_cache",
    "/crashpad/completed",
    "/.cache/thumbnails",
    "/_cacache",
)


def evaluate_level(folder_name: str, path: str | None = None) -> SafetyLevel | None:
    """Tier for a folder name, optionally refined by its full path.

    Returns None when nothing recognizes it — the caller must then leave the
    row out of the list entirely rather than guessing.
    """
    lower = folder_name.lower()

    if path:
        path_lower = os.path.normpath(path).lower()
        for fragment in SAFE_PATH_FRAGMENTS:
            if fragment in path_lower:
                return SafetyLevel.SAFE
        for fragment in CHECK_FIRST_PATH_FRAGMENTS:
            if fragment in path_lower:
                return SafetyLevel.MEDIUM
        # A downloaded SDK payload costs real bandwidth to restore.
        if "/android/sdk/" in path_lower or "/bin/cache" in path_lower:
            return SafetyLevel.MEDIUM
        if "/.gradle/wrapper/dists" in path_lower:
            return SafetyLevel.MEDIUM

    if lower in DEFINITELY_SAFE:
        return SafetyLevel.SAFE
    if lower in CHECK_FIRST:
        return SafetyLevel.MEDIUM
    if lower.endswith(".log") or lower.endswith(".old"):
        return SafetyLevel.SAFE
    return None
