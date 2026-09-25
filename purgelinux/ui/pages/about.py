"""About page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ... import __version__
from ...models import format_bytes
from ...services import prefs, privileged
from ...safety.explanations import database


class AboutPage(Adw.PreferencesPage):
    __gtype_name__ = "PurgeAboutPage"

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store

        header = Adw.PreferencesGroup()
        status = Adw.StatusPage(
            title="Purge for Ubuntu",
            description=f"Version {__version__}\nFree up disk space, safely.",
        )
        status.set_icon_name("user-trash")
        header.add(status)
        self.add(header)

        self.add(self._promise_group())
        self.add(self._status_group())

    def _promise_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="What this app will and will not do",
            description="The rules it follows, so you can check rather than trust.",
        )
        for title, subtitle in (
            ("Allowlist only",
             "A folder is only ever removable if it matches an explicit rule. Anything it does not recognise is never shown, let alone deleted."),
            ("Your files go to the Trash",
             "Personal files and duplicates are moved to the Trash and can be restored from Files. Caches are removed outright, because trashing them would not free any space."),
            ("Nothing leaves this machine",
             "There is no network access, no telemetry and no account. Scans, settings and history stay in ~/.config/purge-linux."),
            ("Root is asked for one thing only",
             "System cleanup runs a small helper through pkexec with a fixed list of actions. No file path from this app is ever handed to root."),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_subtitle_lines(0)
            group.add(row)
        return group

    def _status_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="On this system")

        lifetime = int(prefs.get("lifetime_freed_bytes", 0))
        group.add(Adw.ActionRow(title="Space freed so far", subtitle=format_bytes(lifetime)))
        group.add(Adw.ActionRow(title="Known cache definitions", subtitle=str(len(database()))))

        helper_state = "Installed" if privileged.available() else "Not installed — System cleanup is unavailable"
        group.add(Adw.ActionRow(title="Privileged helper", subtitle=helper_state))
        return group
