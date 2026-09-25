"""Application entry point."""

from __future__ import annotations

import sys


def _run_scheduled_clean() -> int:
    """Headless path used by the systemd user timer."""
    from .services.schedule import run_scheduled_clean

    return run_scheduled_clean()


def _run_dry_run_report() -> int:
    """Print what a safe clean would remove, touching nothing.

    The support path: "run it with --dry-run and send me the output".
    """
    import os

    os.environ["PURGE_DRY_RUN"] = "1"

    from .models import Tab, format_bytes
    from .services.deleter import Deleter
    from .services.store import AppStore

    store = AppStore()
    print("Scanning…", flush=True)
    store.start_scan()
    if store._thread is not None:
        store._thread.join(timeout=1800)

    for tab in (Tab.APP_CACHES, Tab.DEV_TOOLS, Tab.LARGE_FILES, Tab.SYSTEM):
        rows = store.items_for(tab)
        if not rows:
            continue
        print(f"\n{tab.value.replace('_', ' ').title()} — {store.summary_line(tab)}")
        for item in rows[:40]:
            print(f"  {item.safety.label:14s} {item.formatted_size:>10s}  {item.name}")
            for path in item.paths[:4]:
                print(f"{'':27s}{path}")

    candidates = store.safe_candidates()
    report = Deleter(dry_run=True).delete(candidates)
    print(f"\nA safe clean would free {report.formatted_freed} across {report.item_count} items.")
    print("Nothing was removed — this was a dry run.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if "--scheduled-clean" in argv:
        return _run_scheduled_clean()
    if "--dry-run" in argv:
        return _run_dry_run_report()
    if "--version" in argv:
        from . import __version__

        print(f"purge-linux {__version__}")
        return 0

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, Gtk

    from . import APP_ID
    from .services import prefs
    from .ui.pages.settings import apply_appearance
    from .ui.window import MainWindow

    class PurgeApplication(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.window: MainWindow | None = None

        def do_command_line(self, command_line):  # noqa: N802 (GObject naming)
            self.activate()
            return 0

        def do_activate(self) -> None:  # noqa: N802
            if self.window is None:
                _load_styles()
                apply_appearance(prefs.get("appearance", "system"))
                self.window = MainWindow(self)
                self.window.install_shortcuts(self)
                self._install_app_actions()
            self.window.present()

        def _install_app_actions(self) -> None:
            quit_action = Gio.SimpleAction.new("quit", None)
            quit_action.connect("activate", lambda *_: self.quit())
            self.add_action(quit_action)
            self.set_accels_for_action("app.quit", ["<Control>q"])

    def _load_styles() -> None:
        import os

        provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "style.css")
        try:
            provider.load_from_path(css_path)
        except Exception:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    return PurgeApplication().run(argv)


if __name__ == "__main__":
    sys.exit(main())
