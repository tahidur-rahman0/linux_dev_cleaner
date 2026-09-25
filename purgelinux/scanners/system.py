"""System Cleanup scanner — the Ubuntu-only tab.

Everything here *reads* state unprivileged and hands the *writing* to the
root helper as a verb. No path from this module is ever passed to root.

Two hard rules about how state is read, both of which are about surviving
Ubuntu 26.04:

  * Never parse apt's CLI output. apt 3.x reworked it (columns, colour,
    ordering) and any regex over it is a silent zero waiting to happen. Use
    the python3-apt bindings.
  * Never shell out to du/df/stat/find. Ubuntu ships uutils coreutils now;
    sizes come from os.scandir and statvfs.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess

from ..models import DeletionRoute, Location, SafetyLevel, ScanItem
from ..safety.explanations import database
from ..services import prefs
from .base import CancelToken, EventSink, ScanEvent

SOURCE = "system"

APT_ARCHIVES = "/var/cache/apt/archives"
APT_LISTS = "/var/lib/apt/lists"
SNAPD_CACHE = "/var/lib/snapd/cache"
CRASH_DIR = "/var/crash"
LOG_DIR = "/var/log"
JOURNAL_DIR = "/var/log/journal"

ROTATED_SUFFIXES = (".gz", ".xz", ".bz2", ".zst", ".old")
ROTATED_NUMBERED = re.compile(r"\.\d+$")


def _dir_size(path: str, *, predicate=None, max_depth: int = 8) -> tuple[int, int]:
    """Allocated bytes and file count under `path`, without shelling out."""
    total = 0
    count = 0
    stack = [(path, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                stack.append((entry.path, depth + 1))
                            continue
                        if predicate is not None and not predicate(entry.name):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
                    count += 1
        except OSError:
            continue
    return total, count


def _row(
    key: str,
    name: str,
    path: str,
    size: int,
    verb: str,
    level: SafetyLevel,
    detail: str = "",
    note: str = "",
) -> ScanItem | None:
    if size <= 0:
        return None
    entry = database().by_key(key)
    explanation = detail or (entry.explanation if entry else "")
    if not explanation:
        return None
    return ScanItem(
        item_id=f"system:{key}",
        name=name,
        locations=[Location(path=path, size_bytes=size, key=key)],
        safety=level,
        source=SOURCE,
        definition_key=key,
        explanation=explanation,
        icon_name="applications-system-symbolic",
        route=DeletionRoute.HELPER,
        helper_verb=verb,
        size_resolved=True,
        note=note,
    )


# -- apt (via python3-apt, never the CLI) -----------------------------------

def _apt_cache():
    try:
        import apt  # type: ignore
    except ImportError:
        return None
    try:
        return apt.Cache()
    except Exception:
        return None


def _apt_rows(items: list[ScanItem]) -> None:
    size, count = _dir_size(APT_ARCHIVES, predicate=lambda n: n.endswith(".deb"), max_depth=1)
    row = _row("apt-archives", "APT Package Archives", APT_ARCHIVES, size, "apt-clean", SafetyLevel.SAFE)
    if row:
        row.note = f"{count} downloaded package files"
        items.append(row)

    size, _ = _dir_size(APT_LISTS, max_depth=2)
    row = _row("apt-lists", "APT Package Lists", APT_LISTS, size, "apt-lists-clean", SafetyLevel.MEDIUM)
    if row:
        items.append(row)

    cache = _apt_cache()
    if cache is None:
        return

    autoremovable = []
    residual = []
    kernels = []
    running_kernel = os.uname().release

    for package in cache:
        try:
            if package.is_auto_removable:
                autoremovable.append(package)
            if package.installed is None and package.has_config_files:
                residual.append(package.name)
            if package.is_installed and package.name.startswith("linux-image-") and running_kernel not in package.name:
                kernels.append(package)
        except Exception:
            continue

    if autoremovable:
        total = sum(_installed_size(p) for p in autoremovable)
        row = _row(
            "apt-autoremove", "Unused Packages", "apt://autoremove", total,
            "apt-autoremove", SafetyLevel.MEDIUM,
        )
        if row:
            names = ", ".join(sorted(p.name for p in autoremovable)[:6])
            row.note = f"{len(autoremovable)} packages: {names}" + ("…" if len(autoremovable) > 6 else "")
            items.append(row)

    if len(kernels) > 1:
        # Keep the running kernel and the newest installed one as a fallback.
        removable = sorted(kernels, key=lambda p: p.name)[:-1]
        total = sum(_installed_size(p) for p in removable)
        row = _row("old-kernels", "Old Kernels", "apt://kernels", total, "apt-autoremove", SafetyLevel.MEDIUM)
        if row:
            row.note = f"Running {running_kernel} — it and the newest installed kernel are kept."
            items.append(row)

    if residual:
        row = _row(
            "residual-configs", "Leftover Package Configs", "dpkg://residual",
            len(residual) * 32 * 1024, "dpkg-purge-rc", SafetyLevel.MEDIUM,
        )
        if row:
            row.note = f"{len(residual)} removed packages still have config files"
            items.append(row)


def _installed_size(package) -> int:
    try:
        return int(package.installed.installed_size or 0)
    except Exception:
        return 0


# -- journald ---------------------------------------------------------------

def _journal_rows(items: list[ScanItem]) -> None:
    size, _ = _dir_size(JOURNAL_DIR, max_depth=3)
    if size <= 0:
        return
    keep = str(prefs.get("journal_keep_size", "200M"))
    row = _row(
        "journal-logs", "System Logs", JOURNAL_DIR, size,
        f"journal-vacuum={keep}", SafetyLevel.SAFE,
    )
    if row:
        row.note = f"Trimmed to {keep}; recent entries are kept."
        items.append(row)


# -- snap -------------------------------------------------------------------

class _SnapdConnection(http.client.HTTPConnection):
    """HTTP over snapd's unix socket.

    The REST API returns JSON, which survives snap CLI output changes.
    """

    def __init__(self) -> None:
        super().__init__("localhost")

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect("/run/snapd.socket")


def _snap_list() -> list[dict] | None:
    if not os.path.exists("/run/snapd.socket"):
        return None
    try:
        connection = _SnapdConnection()
        connection.request("GET", "/v2/snaps?select=all")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
    except (OSError, ValueError, http.client.HTTPException):
        return _snap_list_cli()
    result = payload.get("result")
    return result if isinstance(result, list) else None


def _snap_list_cli() -> list[dict] | None:
    """Fallback when the socket is unavailable."""
    if not os.path.exists("/snap/bin") and not os.path.exists("/usr/bin/snap"):
        return None
    try:
        completed = subprocess.run(
            ["snap", "list", "--all"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    rows: list[dict] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        rows.append({
            "name": parts[0],
            "revision": parts[2],
            "status": "disabled" if "disabled" in line else "active",
            "installed-size": 0,
        })
    return rows


def _snap_rows(items: list[ScanItem]) -> None:
    snaps = _snap_list()
    if snaps:
        disabled = [s for s in snaps if s.get("status") == "disabled"]
        total = sum(int(s.get("installed-size") or 0) for s in disabled)
        if disabled:
            # Older snapd builds omit installed-size; estimate rather than
            # show a zero row that cannot be selected.
            if total <= 0:
                total = len(disabled) * 100 * 1000 * 1000
            row = _row(
                "snap-old-revisions", "Old Snap Revisions", "snap://disabled", total,
                "snap-prune", SafetyLevel.SAFE,
            )
            if row:
                names = ", ".join(sorted({s.get("name", "?") for s in disabled})[:6])
                row.note = f"{len(disabled)} disabled revisions: {names}"
                items.append(row)

    size, _ = _dir_size(SNAPD_CACHE, max_depth=1)
    row = _row("snapd-cache", "Snap Download Cache", SNAPD_CACHE, size, "snapd-cache-clean", SafetyLevel.SAFE)
    if row:
        items.append(row)


# -- logs and crashes -------------------------------------------------------

def _is_rotated(name: str) -> bool:
    if name.endswith(ROTATED_SUFFIXES):
        return True
    return bool(ROTATED_NUMBERED.search(name))


def _log_rows(items: list[ScanItem]) -> None:
    size, count = _dir_size(CRASH_DIR, max_depth=1)
    row = _row("crash-reports", "Crash Reports", CRASH_DIR, size, "crash-clean", SafetyLevel.SAFE)
    if row:
        row.note = f"{count} reports"
        items.append(row)

    size, count = _dir_size(LOG_DIR, predicate=_is_rotated, max_depth=3)
    row = _row("rotated-logs", "Archived Logs", LOG_DIR, size, "rotated-log-clean", SafetyLevel.SAFE)
    if row:
        row.note = f"{count} rotated log files; current logs are untouched"
        items.append(row)


# -- flatpak (no root needed) -----------------------------------------------

def _flatpak_rows(items: list[ScanItem]) -> None:
    from .. import paths as _paths

    if not _paths.which("flatpak"):
        return
    try:
        completed = subprocess.run(
            ["flatpak", "uninstall", "--unused", "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if completed.returncode != 0:
        return
    lines = [l.strip() for l in completed.stdout.splitlines() if l.strip().startswith(("org.", "com.", "io."))]
    if not lines:
        return
    row = _row(
        "flatpak-unused", "Unused Flatpak Runtimes", "flatpak://unused",
        len(lines) * 200 * 1000 * 1000, "", SafetyLevel.SAFE,
    )
    if row:
        row.route = DeletionRoute.REMOVE
        row.helper_verb = ""
        row.extra = {"flatpak_unused": True, "read_only": True}
        row.note = f"{len(lines)} runtimes — run flatpak uninstall --unused"
        items.append(row)


def scan(sink: EventSink, token: CancelToken) -> None:
    sink(ScanEvent(kind="status", status="Checking system storage…"))
    items: list[ScanItem] = []

    for step in (_apt_rows, _journal_rows, _snap_rows, _log_rows, _flatpak_rows):
        if token.cancelled:
            return
        try:
            step(items)
        except Exception:
            # A missing tool must never take the whole tab down.
            continue

    for item in items:
        sink(ScanEvent(kind="item", item=item))
    sink(ScanEvent(kind="done"))
