"""Main window.

Layout mirrors the macOS app: a sidebar of tabs with the disk summary and
one-click clean at the bottom, and a content pane whose header carries the
title, the running total, Scan, and Clean Selected.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..models import Tab, format_bytes
from ..services import prefs
from ..services.store import AppStore
from .dialogs.confirm import build_confirm_dialog, build_result_dialog
from .pages.about import AboutPage
from .pages.large_files import LargeFilesPage
from .pages.scan_page import ScanPage
from .pages.settings import SettingsPage
from .onboarding import maybe_present
from .sidebar import Sidebar

EMPTY_STATES = {
    Tab.APP_CACHES: (
        "Nothing to clean",
        "No app caches were found. Run a scan, or check back after using your apps for a while.",
    ),
    Tab.DEV_TOOLS: (
        "No developer caches found",
        "Nothing from your package managers or projects is taking up space. "
        "Projects you have worked on recently are left alone — change that under Settings.",
    ),
    Tab.SYSTEM: (
        "System storage looks tidy",
        "No package archives, old snap revisions or oversized logs were found.",
    ),
}


class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "PurgeMainWindow"

    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Purge")
        self.set_default_size(1100, 720)

        self.store = AppStore(dispatch=lambda fn: GLib.idle_add(_once(fn)))
        self.current_tab = Tab.APP_CACHES
        self._pages: dict[Tab, Gtk.Widget] = {}

        self.sidebar = Sidebar()
        self.sidebar.connect("tab-selected", self._on_tab_selected)
        self.sidebar.connect("clean-safe-requested", lambda _s: self._clean_safe())

        split = Adw.NavigationSplitView()
        split.set_sidebar(Adw.NavigationPage(child=self._wrap_sidebar(), title="Purge"))
        split.set_content(Adw.NavigationPage(child=self._build_content(), title="App Caches"))
        self.content_page = split.get_content()
        self.set_content(split)

        self._connect_store()
        self.sidebar.refresh_disk()
        self._refresh_header()

        # On first run, explain what the app touches before it touches it.
        # Otherwise scan straight away, so the app is useful when it opens.
        GLib.timeout_add(250, self._start_first_run)

    # -- construction ------------------------------------------------------

    def _wrap_sidebar(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Purge"))
        view.add_top_bar(header)
        view.set_content(self.sidebar)
        return view

    def _build_content(self) -> Gtk.Widget:
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        for tab in (Tab.APP_CACHES, Tab.DEV_TOOLS, Tab.SYSTEM):
            title, body = EMPTY_STATES[tab]
            page = ScanPage(self.store, tab, title, body)
            self._pages[tab] = page
            self.stack.add_named(page, tab.value)

        large = LargeFilesPage(self.store)
        self._pages[Tab.LARGE_FILES] = large
        self.stack.add_named(large, Tab.LARGE_FILES.value)

        self._pages[Tab.SETTINGS] = SettingsPage(self.store)
        self.stack.add_named(self._pages[Tab.SETTINGS], Tab.SETTINGS.value)
        self._pages[Tab.ABOUT] = AboutPage(self.store)
        self.stack.add_named(self._pages[Tab.ABOUT], Tab.ABOUT.value)

        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.set_content(self.stack)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(view)
        return self.toast_overlay

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="App Caches", subtitle="")
        header.set_title_widget(self.window_title)

        self.scan_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Scan (Ctrl+R)")
        self.scan_button.connect("clicked", lambda _b: self._scan_current())
        header.pack_start(self.scan_button)

        self.clean_button = Gtk.Button(label="Clean Selected")
        self.clean_button.add_css_class("destructive-action")
        self.clean_button.set_sensitive(False)
        self.clean_button.connect("clicked", lambda _b: self._clean_selected())
        header.pack_end(self.clean_button)

        self.progress = Gtk.Spinner()
        header.pack_end(self.progress)
        return header

    # -- store wiring ------------------------------------------------------

    def _connect_store(self) -> None:
        self.store.connect("item-added", self._on_item_added)
        self.store.connect("item-updated", self._on_item_updated)
        self.store.connect("status-changed", self._on_status)
        self.store.connect("scan-started", self._on_scan_started)
        self.store.connect("scan-finished", self._on_scan_finished)
        self.store.connect("selection-changed", self._refresh_header)
        self.store.connect("clean-started", lambda _n: self._set_busy(True))
        self.store.connect("clean-finished", self._on_clean_finished)

    def _on_item_added(self, item) -> None:
        for tab, page in self._pages.items():
            if isinstance(page, ScanPage):
                page.add_item(item)
        self._refresh_header()

    def _on_item_updated(self, item) -> None:
        for page in self._pages.values():
            if isinstance(page, ScanPage):
                page.update_item(item)
        self._refresh_header()

    def _on_status(self, status: str) -> None:
        self.window_title.set_subtitle(status)

    def _on_scan_started(self) -> None:
        self._set_busy(True)

    def _on_scan_finished(self) -> None:
        self._set_busy(False)
        for page in self._pages.values():
            if isinstance(page, ScanPage):
                page.reload()
        self.sidebar.refresh_disk()
        self._refresh_header()

    def _on_clean_finished(self, report) -> None:
        self._set_busy(False)
        for page in self._pages.values():
            if isinstance(page, ScanPage):
                page.reload()
        self.sidebar.refresh_disk()
        self._refresh_header()

        if report.failed or report.skipped:
            build_result_dialog(report).present(self)
        else:
            self.toast_overlay.add_toast(
                Adw.Toast(title=f"Freed {report.formatted_freed} from {report.item_count} items")
            )

    # -- actions -----------------------------------------------------------

    def _start_first_run(self) -> bool:
        if not maybe_present(self, self._initial_scan):
            self._initial_scan()
        return False

    def _initial_scan(self) -> None:
        self.store.start_scan()

    def _scan_current(self) -> None:
        if self.store.scanning:
            self.store.cancel_scan()
            return
        self.store.start_scan()

    def _clean_selected(self) -> None:
        candidates = self.store.selected_candidates()
        if not candidates:
            return
        build_confirm_dialog(self, candidates, lambda: self._run_clean(candidates, "manual")).present(self)

    def _clean_safe(self) -> None:
        candidates = self.store.safe_candidates()
        if not candidates:
            return
        build_confirm_dialog(self, candidates, lambda: self._run_clean(candidates, "safe")).present(self)

    def _run_clean(self, candidates, trigger: str) -> None:
        self.store.clean(
            candidates,
            trigger=trigger,
            allow_permanent_fallback=bool(prefs.get("confirmed_permanent_fallback", False)),
        )

    def _on_tab_selected(self, _sidebar, tab_value: str) -> None:
        self.current_tab = Tab(tab_value)
        self.stack.set_visible_child_name(tab_value)
        self.content_page.set_title(self._tab_title())
        self._refresh_header()

    def _tab_title(self) -> str:
        from .sidebar import NAV_ITEMS

        for tab, label, _icon in NAV_ITEMS:
            if tab is self.current_tab:
                return label
        return "Purge"

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self.progress.start()
        else:
            self.progress.stop()
        self.scan_button.set_icon_name("process-stop-symbolic" if busy else "view-refresh-symbolic")
        self._refresh_header()

    def _refresh_header(self, *_args) -> None:
        self.window_title.set_title(self._tab_title())

        page = self._pages.get(self.current_tab)
        if isinstance(page, ScanPage):
            self.window_title.set_subtitle(self.store.status if self.store.scanning else page.subtitle())
        else:
            self.window_title.set_subtitle("")

        selected_bytes = self.store.selected_bytes()
        busy = self.store.scanning or self.store.deleting
        self.clean_button.set_sensitive(selected_bytes > 0 and not busy)
        self.clean_button.set_label(
            f"Clean Selected ({format_bytes(selected_bytes)})" if selected_bytes else "Clean Selected"
        )
        self.sidebar.set_safe_total(self.store.safe_bytes(), busy=busy)

    # -- shortcuts ---------------------------------------------------------

    def install_shortcuts(self, app: Adw.Application) -> None:
        from gi.repository import Gio

        def add(name: str, accel: str, callback) -> None:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.add_action(action)
            app.set_accels_for_action(f"win.{name}", [accel])

        add("scan", "<Control>r", self._scan_current)
        add("scan-all", "<Control><Shift>r", self._scan_current)
        add("filter-all", "<Control>1", lambda: self._set_filter("all"))
        add("filter-safe", "<Control>2", lambda: self._set_filter("safe"))
        add("filter-check", "<Control>3", lambda: self._set_filter("check"))

    def _set_filter(self, key: str) -> None:
        page = self._pages.get(self.current_tab)
        if isinstance(page, ScanPage):
            page.filter_bar.set_active(key)


def _once(fn):
    """Wrap a callable so GLib.idle_add runs it exactly once."""

    def run() -> bool:
        fn()
        return False

    return run
