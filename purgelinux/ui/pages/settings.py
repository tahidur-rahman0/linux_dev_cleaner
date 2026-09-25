"""Settings page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ...models import format_bytes
from ...services import history, prefs, schedule

STALE_CHOICES = [
    ("Show all", 0),
    ("1 month", 30),
    ("3 months", 90),
    ("6 months", 180),
    ("1 year", 365),
    ("2 years", 730),
]

SIZE_CHOICES = [
    ("5 MB", 5_000_000),
    ("50 MB", 50_000_000),
    ("100 MB", 100_000_000),
    ("500 MB", 500_000_000),
    ("1 GB", 1_000_000_000),
]

INTERVAL_CHOICES = [
    ("Weekly", 7),
    ("Monthly", 30),
    ("Every 3 months", 90),
]

JOURNAL_CHOICES = ["50M", "100M", "200M", "500M", "1G"]


class SettingsPage(Adw.PreferencesPage):
    __gtype_name__ = "PurgeSettingsPage"

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store

        self.add(self._appearance_group())
        self.add(self._scanning_group())
        self.add(self._schedule_group())
        self.add(self._excluded_group())
        self.add(self._history_group())

    # -- groups ------------------------------------------------------------

    def _appearance_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Appearance")
        row = Adw.ComboRow(title="Theme", model=Gtk.StringList.new(["System", "Light", "Dark"]))
        current = prefs.get("appearance", "system")
        row.set_selected({"system": 0, "light": 1, "dark": 2}.get(current, 0))
        row.connect("notify::selected", self._on_appearance)
        group.add(row)
        return group

    def _on_appearance(self, row: Adw.ComboRow, _param) -> None:
        value = ["system", "light", "dark"][row.get_selected()]
        prefs.set_value("appearance", value)
        apply_appearance(value)

    def _scanning_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Scanning",
            description="Controls which developer projects and personal files are offered.",
        )

        stale = Adw.ComboRow(
            title="Consider projects stale after",
            subtitle="Projects you have touched more recently are left alone",
            model=Gtk.StringList.new([label for label, _ in STALE_CHOICES]),
        )
        current = int(prefs.get("stale_project_days", 90))
        stale.set_selected(next((i for i, (_, v) in enumerate(STALE_CHOICES) if v == current), 2))
        stale.connect("notify::selected", lambda r, _p: prefs.set_value("stale_project_days", STALE_CHOICES[r.get_selected()][1]))
        group.add(stale)

        size = Adw.ComboRow(
            title="Large file threshold",
            model=Gtk.StringList.new([label for label, _ in SIZE_CHOICES]),
        )
        current_size = int(prefs.get("large_file_min_bytes", 100_000_000))
        size.set_selected(next((i for i, (_, v) in enumerate(SIZE_CHOICES) if v == current_size), 2))
        size.connect("notify::selected", lambda r, _p: prefs.set_value("large_file_min_bytes", SIZE_CHOICES[r.get_selected()][1]))
        group.add(size)

        journal = Adw.ComboRow(
            title="Keep system logs up to",
            subtitle="Used when trimming the systemd journal",
            model=Gtk.StringList.new(JOURNAL_CHOICES),
        )
        current_journal = str(prefs.get("journal_keep_size", "200M"))
        journal.set_selected(JOURNAL_CHOICES.index(current_journal) if current_journal in JOURNAL_CHOICES else 2)
        journal.connect("notify::selected", lambda r, _p: prefs.set_value("journal_keep_size", JOURNAL_CHOICES[r.get_selected()]))
        group.add(journal)
        return group

    def _schedule_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Cleaning Schedule",
            description="Runs a safe clean on a timer. Only Safe to Clean items are ever included.",
        )

        toggle = Adw.SwitchRow(title="Run automatic cleaning")
        toggle.set_active(bool(prefs.get("scheduled_cleaning_enabled", False)))
        toggle.connect("notify::active", self._on_schedule_toggle)
        group.add(toggle)

        interval = Adw.ComboRow(
            title="How often",
            model=Gtk.StringList.new([label for label, _ in INTERVAL_CHOICES]),
        )
        current = int(prefs.get("scheduled_cleaning_interval_days", 30))
        interval.set_selected(next((i for i, (_, v) in enumerate(INTERVAL_CHOICES) if v == current), 1))
        interval.connect("notify::selected", self._on_interval)
        group.add(interval)

        self.next_run_row = Adw.ActionRow(title="Next clean", subtitle=schedule.describe_next_run())
        group.add(self.next_run_row)
        return group

    def _on_schedule_toggle(self, row: Adw.SwitchRow, _param) -> None:
        enabled = row.get_active()
        prefs.set_value("scheduled_cleaning_enabled", enabled)
        schedule.apply()
        self.next_run_row.set_subtitle(schedule.describe_next_run())

    def _on_interval(self, row: Adw.ComboRow, _param) -> None:
        prefs.set_value("scheduled_cleaning_interval_days", INTERVAL_CHOICES[row.get_selected()][1])
        schedule.apply()
        self.next_run_row.set_subtitle(schedule.describe_next_run())

    def _excluded_group(self) -> Adw.PreferencesGroup:
        self.excluded_group = Adw.PreferencesGroup(
            title="Excluded from scans",
            description="Folders this app leaves alone. Right-click any result to add one.",
        )
        self._reload_excluded()
        return self.excluded_group

    def _reload_excluded(self) -> None:
        for row in getattr(self, "_excluded_rows", []):
            self.excluded_group.remove(row)
        self._excluded_rows = []

        excluded = sorted(prefs.excluded_paths())
        if not excluded:
            row = Adw.ActionRow(title="Nothing excluded", subtitle="Everything in scope is scanned")
            row.set_sensitive(False)
            self.excluded_group.add(row)
            self._excluded_rows.append(row)
            return

        for path in excluded:
            row = Adw.ActionRow(title=path)
            button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.connect("clicked", self._on_unexclude, path)
            row.add_suffix(button)
            self.excluded_group.add(row)
            self._excluded_rows.append(row)

    def _on_unexclude(self, _button, path: str) -> None:
        prefs.unexclude_path(path)
        self._reload_excluded()

    def _history_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Cleaning History")
        entries = history.load()
        if not entries:
            row = Adw.ActionRow(title="Nothing cleaned yet")
            row.set_sensitive(False)
            group.add(row)
            return group

        lifetime = int(prefs.get("lifetime_freed_bytes", 0))
        summary = Adw.ActionRow(
            title="Freed since you installed Purge",
            subtitle=format_bytes(lifetime),
        )
        group.add(summary)

        for entry in entries[:10]:
            trigger = {"safe": "Safe clean", "scheduled": "Scheduled", "manual": "Manual"}.get(entry.trigger, entry.trigger)
            row = Adw.ActionRow(
                title=f"{entry.formatted_freed} · {entry.item_count} items",
                subtitle=f"{trigger} · {entry.formatted_date}",
            )
            group.add(row)
        return group


def apply_appearance(value: str) -> None:
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(
        {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(value, Adw.ColorScheme.DEFAULT)
    )
