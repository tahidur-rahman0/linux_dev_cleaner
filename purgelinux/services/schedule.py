"""Scheduled cleaning via a systemd user timer.

Replaces macOS SMAppService/UNUserNotificationCenter. A user timer needs no
root, survives reboots, and is inspectable with `systemctl --user`, which
matters when someone asks why their disk got cleaned.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from .. import paths
from . import history, prefs

UNIT_NAME = "purge-linux-clean"

SERVICE_TEMPLATE = """[Unit]
Description=Purge for Ubuntu — scheduled safe clean

[Service]
Type=oneshot
ExecStart={command}
"""

TIMER_TEMPLATE = """[Unit]
Description=Purge for Ubuntu — scheduled safe clean

[Timer]
OnCalendar={calendar}
Persistent=true

[Install]
WantedBy=timers.target
"""

CALENDARS = {
    7: "weekly",
    30: "monthly",
    90: "quarterly",
}


def _unit_dir() -> str:
    return os.path.join(paths.config_home(), "systemd", "user")


def _systemctl(*args: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        completed = subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _executable() -> str:
    """The command the timer runs.

    Prefers the installed entry point; falls back to running the package in
    place so a source checkout schedules correctly too.
    """
    installed = shutil.which("purge-linux")
    if installed:
        return f"{installed} --scheduled-clean"
    import sys

    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"{sys.executable} -m purgelinux --scheduled-clean"


def apply() -> bool:
    """Write or remove the timer to match the current preferences."""
    enabled = bool(prefs.get("scheduled_cleaning_enabled", False))
    unit_dir = _unit_dir()

    if not enabled:
        _systemctl("disable", "--now", f"{UNIT_NAME}.timer")
        for suffix in (".service", ".timer"):
            try:
                os.unlink(os.path.join(unit_dir, UNIT_NAME + suffix))
            except OSError:
                pass
        _systemctl("daemon-reload")
        return True

    interval = int(prefs.get("scheduled_cleaning_interval_days", 30))
    calendar = CALENDARS.get(interval)
    if calendar is None:
        # A custom interval becomes a plain repeating date spec.
        calendar = f"*-*-1/{max(1, min(28, interval))} 10:00:00"

    try:
        os.makedirs(unit_dir, exist_ok=True)
        with open(os.path.join(unit_dir, f"{UNIT_NAME}.service"), "w", encoding="utf-8") as handle:
            handle.write(SERVICE_TEMPLATE.format(command=_executable()))
        with open(os.path.join(unit_dir, f"{UNIT_NAME}.timer"), "w", encoding="utf-8") as handle:
            handle.write(TIMER_TEMPLATE.format(calendar=calendar))
    except OSError:
        return False

    _systemctl("daemon-reload")
    return _systemctl("enable", "--now", f"{UNIT_NAME}.timer")


def describe_next_run() -> str:
    if not prefs.get("scheduled_cleaning_enabled", False):
        return "Automatic cleaning is off"

    if shutil.which("systemctl"):
        try:
            completed = subprocess.run(
                ["systemctl", "--user", "show", f"{UNIT_NAME}.timer", "--property=NextElapseUSecRealtime", "--value"],
                capture_output=True, text=True, timeout=10,
            )
            value = completed.stdout.strip()
            if value and value != "0":
                return value
        except (OSError, subprocess.SubprocessError):
            pass

    interval = int(prefs.get("scheduled_cleaning_interval_days", 30))
    entries = history.load()
    last = entries[0].timestamp if entries else time.time()
    return time.strftime("%-d %b %Y", time.localtime(last + interval * 86400))


def run_scheduled_clean() -> int:
    """Entry point for the timer: scan, clean safe items, notify.

    Runs headless. Only Safe to Clean rows are ever included, and personal
    files are excluded by construction — `safe_candidates()` filters them out.
    """
    from ..models import Tab, format_bytes
    from .store import AppStore

    store = AppStore()
    store.start_scan([Tab.APP_CACHES, Tab.DEV_TOOLS])
    if store._thread is not None:
        store._thread.join(timeout=900)

    candidates = store.safe_candidates()
    if not candidates:
        return 0

    from .deleter import Deleter

    report = Deleter().delete(candidates)
    history.record(report, "scheduled")
    prefs.set_value(
        "lifetime_freed_bytes",
        int(prefs.get("lifetime_freed_bytes", 0)) + report.freed_bytes,
    )
    notify("Purge freed space", f"{report.formatted_freed} cleaned from {report.item_count} items.")
    return 0


def notify(title: str, body: str) -> None:
    """Desktop notification, via notify-send so it works headless too."""
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(
            ["notify-send", "--app-name=Purge", "--icon=user-trash", title, body],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
