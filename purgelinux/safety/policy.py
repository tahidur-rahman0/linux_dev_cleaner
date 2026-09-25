"""Strict allow/deny gate for every filesystem removal this app performs.

Linux rewrite of Purge's `DeletionSafetyPolicy`. Manual cleaning, one-click
cleaning and scheduled cleaning all run every candidate through `evaluate()`
before anything is touched. A path that is not explicitly allowed is refused.

Two independent lists:

  NEVER_DELETE     hard refusal, even when a scanner proposes the path. A bug
                   in a scanner cannot make these eligible.
  allowlist        a path is removable only when it sits under a known cache
                   root, or its folder name is on the whitelist.

AUDIT: generic names like `build`, `dist`, `out` and `target` are on the
whitelist and could in principle match a user-authored folder. The whitelist
only *authorizes* a removal; it never discovers one. Discovery lives in the
scanners, which surface these only next to a matching project manifest, so a
lone folder named `out` in Documents is never proposed. Kept deliberately,
flagged here so the breadth stays visible.
"""

from __future__ import annotations

import enum
import os
import re
from functools import lru_cache

from .. import paths


class Decision(enum.Enum):
    ALLOW = "allow"
    BLOCKED_NEVER_DELETE = "never_delete"
    BLOCKED_NOT_ALLOWLISTED = "not_allowlisted"

    @property
    def skip_reason(self) -> str | None:
        if self is Decision.ALLOW:
            return None
        if self is Decision.BLOCKED_NEVER_DELETE:
            return "Protected location — not eligible for deletion."
        return "This file was skipped for safety"

    @property
    def is_user_visible_skip(self) -> bool:
        return self is Decision.BLOCKED_NOT_ALLOWLISTED


# --------------------------------------------------------------------------
# Never delete
# --------------------------------------------------------------------------

#: Home-relative trees that are refused outright, with everything beneath them.
NEVER_DELETE_HOME_PREFIXES: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".password-store",
    ".pki",
    ".cert",
    ".aws",
    ".kube",
    ".netrc",
    ".bash_history",
    ".zsh_history",
    ".local/share/keyrings",
    ".config/systemd",
    ".config/autostart",
    ".config/gcloud",
    ".config/purge-linux",
    ".gnome2/keyrings",
    # Browser profiles. Their caches are reached through the cache roots in
    # ~/.cache, ~/snap/<b>/common/.cache and ~/.var/app/<id>/cache instead, so
    # nothing legitimate is lost by refusing the profile tree wholesale.
    ".mozilla",
    ".thunderbird",
    "snap/firefox/common/.mozilla",
    "snap/thunderbird/common/.thunderbird",
    ".var/app/org.mozilla.firefox/.mozilla",
    ".var/app/org.mozilla.Thunderbird/.thunderbird",
)

#: Names that mean identity, session or user data wherever they appear inside
#: an application-data root. Deleting any of these would log the user out or
#: lose local state, which is exactly what cache cleaning must never do.
BROWSER_SENSITIVE_NAMES: frozenset[str] = frozenset(
    {
        "cookies",
        "cookies-journal",
        "login data",
        "login data for account",
        "web data",
        "history",
        "history-journal",
        "bookmarks",
        "favicons",
        "preferences",
        "secure preferences",
        "local state",
        "local storage",
        "session storage",
        "sessions",
        "session data",
        "indexeddb",
        "databases",
        "database",
        "leveldb",
        "extension state",
        "extension rules",
        "extension scripts",
        "local extension settings",
        "managed extension settings",
        "sync data",
        "affiliation database",
        "storage",
        "key4.db",
        "key3.db",
        "logins.json",
        "places.sqlite",
        "cert9.db",
        "sessionstore-backups",
        "sessionstore.jsonlz4",
        "prefs.js",
        "keyrings",
        "trust",
        # The Service Worker root holds registrations and their Database.
        # Only CacheStorage and ScriptCache beneath it are ever eligible.
        "service worker",
    }
)

#: A browser profile directory itself is never removed, only caches inside it.
PROFILE_DIR_RE = re.compile(r"^(default|default-release|profile \d+|guest profile|system profile)$")

#: Absolute system paths the privileged helper is allowed to work under.
#: Nothing outside these is ever eligible, even for root.
SYSTEM_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/var/cache/apt/archives",
    "/var/lib/apt/lists",
    "/var/lib/snapd/cache",
    "/var/crash",
    "/var/log",
    "/var/tmp",
)

#: Never removed even by the helper.
SYSTEM_NEVER_DELETE: tuple[str, ...] = (
    "/var/log/journal",  # vacuumed through journalctl, never rm -rf'd
    "/var/lib/apt/lists/lock",
    "/var/cache/apt/archives/lock",
    "/var/cache/apt/archives/partial",
)


# --------------------------------------------------------------------------
# Allowlist
# --------------------------------------------------------------------------

ALLOWLISTED_FOLDER_NAMES: frozenset[str] = frozenset(
    {
        # Dependency trees and build output
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
        "obj",
        "bin",
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
        ".venv",
        "venv",
        ".terraform",
        ".yarn",
        "bundle",
        "builddir",
        "_build",
        # Cache folder names
        "cache",
        "caches",
        ".cache",
        "cache_data",
        "cache2",
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
        "startupcache",
        "thumbnails",
        "jumplistcache",
        "safebrowsing",
        "workspacestorage",
        "_cacache",
        "_npx",
        "_logs",
        "daemon",
        "snapshots",
        "completed",
        "mesa_shader_cache",
        "glcache",
        "radv_builtin_shaders",
        "fontconfig",
        "modules-2",
        "build-cache-1",
        "registry",
        "repository",
        "packages",
        "hosted",
        "store",
        "dists",
        "downloads",
        "toolchains",
        "system-images",
        "ndk",
        "index",
        "pkgs",
        "temp",
        ".temp",
    }
)

#: Path fragments that authorize removal irrespective of folder name.
ALLOWLISTED_PATH_FRAGMENTS: tuple[str, ...] = (
    "/service worker/cachestorage",
    "/service worker/scriptcache",
    "/crashpad/completed",
    "/bin/cache",
    "/.android/avd/",
)


@lru_cache(maxsize=1)
def _catalog_dirs() -> tuple[str, ...]:
    """Directories the shipped dev catalog declares.

    The catalog names specific paths (`~/.konan`, `~/.gradle/wrapper/dists`),
    not folder-name patterns, so it can authorize exactly those trees and
    nothing else. It is checked *after* every never-delete rule, so a catalog
    entry can never unlock a protected location — it can only narrow what is
    already outside the generic allowlist.
    """
    try:
        from ..scanners.catalog import catalog
        from ..scanners.dev_tools import _expand
    except Exception:
        return ()
    found: list[str] = []
    for rule in catalog().global_rules:
        for pattern in rule.paths:
            try:
                found.extend(os.path.normpath(p) for p in _expand(pattern))
            except Exception:
                continue
    return tuple(sorted(set(found)))


def reset_catalog_cache() -> None:
    """Drop the cached catalog paths. Used by tests and after a rescan."""
    _catalog_dirs.cache_clear()


def _home_roots() -> tuple[str, ...]:
    """Application-data roots. Browser-sensitive name checks apply here."""
    return (
        paths.cache_home(),
        paths.config_home(),
        paths.data_home(),
        paths.state_home(),
        paths.snap_root(),
        paths.flatpak_root(),
    )


def _cache_roots() -> tuple[str, ...]:
    """Roots whose entire contents are, by definition, cache."""
    roots = [paths.cache_home()]
    for base, pattern in ((paths.flatpak_root(), "cache"), (paths.snap_root(), None)):
        try:
            for entry in os.scandir(base):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if pattern:
                    roots.append(os.path.join(entry.path, pattern))
                else:
                    roots.append(os.path.join(entry.path, "common", ".cache"))
                    roots.append(os.path.join(entry.path, "current", ".cache"))
        except OSError:
            continue
    return tuple(roots)


def _is_within(path: str, root: str) -> bool:
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    return path == root or path.startswith(root + os.sep)


def _escapes_home(path: str) -> bool:
    """Whether following symlinks would take this path outside the home tree."""
    home = os.path.normpath(paths.home())
    try:
        real = os.path.realpath(path)
    except OSError:
        return True
    return not _is_within(real, home)


def user_selectable_roots() -> tuple[str, ...]:
    """Where a hand-picked personal file is allowed to live.

    Large Files and Duplicates let the user choose their own files, which by
    definition are not on any cache allowlist. Rather than bypass the policy
    for those rows, they are bounded to the directories the Large Files
    scanner actually walks, plus the local model stores.
    """
    home = paths.home()
    roots = list(paths.scan_user_dirs())
    roots.extend(
        [
            os.path.join(home, ".ollama", "models"),
            os.path.join(home, ".lmstudio", "models"),
            os.path.join(paths.cache_home(), "lm-studio"),
            os.path.join(paths.cache_home(), "huggingface"),
            os.path.join(paths.data_home(), "nomic.ai"),
        ]
    )
    return tuple(roots)


def evaluate(path: str, *, allow_system: bool = False, user_selected: bool = False) -> Decision:
    """Decide whether `path` may be removed.

    `allow_system` is set only by the privileged helper route, and even then
    the path must sit under SYSTEM_ALLOWED_PREFIXES. `user_selected` is set
    only for personal files the user picked in Large Files; it relaxes the
    allowlist but every never-delete rule still applies.
    """
    if not path or not os.path.isabs(path):
        return Decision.BLOCKED_NEVER_DELETE

    norm = os.path.normpath(path)
    home = os.path.normpath(paths.home())

    # Root, home itself, and any top-level home directory are never removable.
    if norm in ("/", home) or len(norm.strip("/").split("/")) == 0:
        return Decision.BLOCKED_NEVER_DELETE

    if os.path.ismount(norm):
        return Decision.BLOCKED_NEVER_DELETE

    lower = norm.lower()
    base = os.path.basename(norm)
    base_lower = base.lower()

    # ---- outside the home tree -------------------------------------------
    if not _is_within(norm, home):
        if not allow_system:
            return Decision.BLOCKED_NEVER_DELETE
        for blocked in SYSTEM_NEVER_DELETE:
            if _is_within(norm, blocked):
                return Decision.BLOCKED_NEVER_DELETE
        for prefix in SYSTEM_ALLOWED_PREFIXES:
            if _is_within(norm, prefix) and norm != os.path.normpath(prefix):
                return Decision.ALLOW
        return Decision.BLOCKED_NOT_ALLOWLISTED

    # ---- never-delete trees ----------------------------------------------
    for rel in NEVER_DELETE_HOME_PREFIXES:
        if _is_within(norm, os.path.join(home, rel)):
            return Decision.BLOCKED_NEVER_DELETE

    # A symlink that would resolve outside the home tree is refused: removing
    # it is fine, but nothing downstream should ever follow it.
    if os.path.islink(norm) and _escapes_home(norm):
        return Decision.BLOCKED_NEVER_DELETE

    in_app_data = any(_is_within(norm, root) for root in _home_roots())

    if in_app_data:
        # Identity, session and site-storage names, wherever they appear in an
        # application-data root. Scoped to these roots so a user's own file
        # named "History" in Documents is unaffected.
        parent_lower = os.path.basename(os.path.dirname(norm)).lower()
        if base_lower in BROWSER_SENSITIVE_NAMES:
            # CacheStorage / ScriptCache sit *under* Service Worker and remain
            # eligible; the Service Worker root itself does not.
            if not (parent_lower == "service worker" and base_lower in ("cachestorage", "scriptcache")):
                return Decision.BLOCKED_NEVER_DELETE
        if PROFILE_DIR_RE.match(base_lower):
            return Decision.BLOCKED_NEVER_DELETE
        # Any ancestor that is a sensitive container (Local Storage/leveldb…).
        for part in norm[len(home):].lower().split(os.sep):
            if part in BROWSER_SENSITIVE_NAMES and part != "service worker":
                return Decision.BLOCKED_NEVER_DELETE

    # ---- user-selected personal files -------------------------------------
    if user_selected:
        for root in user_selectable_roots():
            if _is_within(norm, root) and norm != os.path.normpath(root):
                return Decision.ALLOW
        return Decision.BLOCKED_NOT_ALLOWLISTED

    # ---- allowlist --------------------------------------------------------
    for fragment in ALLOWLISTED_PATH_FRAGMENTS:
        if fragment in lower:
            return Decision.ALLOW

    for root in _cache_roots():
        if _is_within(norm, root) and norm != os.path.normpath(root):
            return Decision.ALLOW

    if base_lower in ALLOWLISTED_FOLDER_NAMES:
        return Decision.ALLOW

    for declared in _catalog_dirs():
        if norm == declared or norm.startswith(declared + os.sep):
            return Decision.ALLOW

    # Versioned SDK payloads and extension folders (androidstudio-2024.1,
    # ms-python.python-2024.2.0) carry a version suffix rather than a fixed
    # name; allow them only inside a directory that is itself allowlisted.
    parent_base = os.path.basename(os.path.dirname(norm)).lower()
    if parent_base in ALLOWLISTED_FOLDER_NAMES or parent_base in ("extensions", "ndk", "system-images", "build-tools", "platforms"):
        return Decision.ALLOW

    return Decision.BLOCKED_NOT_ALLOWLISTED


def is_offered_for_cleanup(
    path: str, *, allow_system: bool = False, user_selected: bool = False
) -> bool:
    """Whether a scanner may surface this path at all."""
    return evaluate(path, allow_system=allow_system, user_selected=user_selected) is Decision.ALLOW
