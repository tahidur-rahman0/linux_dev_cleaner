"""First-run onboarding.

Three steps, not five: on Linux there is no Full Disk Access permission to
request, so the permissions step upstream needs becomes a plain statement of
what the app touches and what it will never touch.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..services import prefs, privileged

STEPS = [
    (
        "user-trash",
        "Purge for Ubuntu",
        "Your machine fills up with cache and build output you never asked for. "
        "Purge finds it, labels what is safe, and clears it in one click.",
    ),
    (
        "security-high-symbolic",
        "It only touches what it recognises",
        "Every item matches an explicit rule and carries a plain-English explanation. "
        "Anything it cannot identify is never shown, let alone deleted.\n\n"
        "Your SSH keys, password store, keyrings, browser logins, cookies and history "
        "are on a permanent never-touch list.",
    ),
    (
        "folder-documents-symbolic",
        "Your files go to the Trash",
        "Personal files and duplicates are moved to the Trash, so you can restore them from Files.\n\n"
        "Caches and build folders are removed outright — moving those to the Trash would not "
        "free any space. Every confirmation screen says which is which.",
    ),
]


class OnboardingDialog(Adw.Dialog):
    __gtype_name__ = "PurgeOnboardingDialog"

    def __init__(self, on_finish) -> None:
        super().__init__()
        self.set_content_width(560)
        self.set_content_height(520)
        self._on_finish = on_finish

        self.carousel = Adw.Carousel(hexpand=True, vexpand=True)
        self.carousel.set_allow_scroll_wheel(False)
        for icon, title, body in STEPS:
            self.carousel.append(self._build_step(icon, title, body))
        self.carousel.connect("page-changed", lambda *_: self._sync_buttons())

        indicator = Adw.CarouselIndicatorDots(carousel=self.carousel)

        self.back_button = Gtk.Button(label="Back")
        self.back_button.connect("clicked", lambda _b: self._go(-1))
        self.next_button = Gtk.Button(label="Continue")
        self.next_button.add_css_class("suggested-action")
        self.next_button.connect("clicked", lambda _b: self._advance())

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_margin_start(18)
        buttons.set_margin_end(18)
        buttons.set_margin_bottom(18)
        buttons.append(self.back_button)
        buttons.append(Gtk.Box(hexpand=True))
        buttons.append(self.next_button)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.carousel)
        box.append(indicator)
        box.append(buttons)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_title=False))
        view.set_content(box)
        self.set_child(view)
        self._sync_buttons()

    def _build_step(self, icon: str, title: str, body: str) -> Gtk.Widget:
        page = Adw.StatusPage(title=title, description=body)
        page.set_icon_name(icon)
        page.set_vexpand(True)
        # AdwCarousel allocates each page its *natural* width, and a
        # StatusPage asks for far less than the dialog is wide. Without this
        # all three steps are laid out side by side and clipped.
        page.set_hexpand(True)

        if icon.startswith("folder") and not privileged.available():
            note = Gtk.Label(
                label="The privileged helper is not installed, so the System tab will be read-only.",
                wrap=True,
            )
            note.add_css_class("dim-label-small")
            page.set_child(note)
        return page

    def _position(self) -> int:
        return int(self.carousel.get_position())

    def _go(self, delta: int) -> None:
        target = max(0, min(len(STEPS) - 1, self._position() + delta))
        self.carousel.scroll_to(self.carousel.get_nth_page(target), True)

    def _advance(self) -> None:
        if self._position() >= len(STEPS) - 1:
            prefs.set_value("onboarding_complete", True)
            self.close()
            self._on_finish()
            return
        self._go(1)

    def _sync_buttons(self) -> None:
        position = self._position()
        self.back_button.set_sensitive(position > 0)
        self.next_button.set_label("Scan my machine" if position >= len(STEPS) - 1 else "Continue")


def maybe_present(parent, on_finish) -> bool:
    """Show onboarding on first run only. Returns True if it was shown."""
    if prefs.get("onboarding_complete", False):
        return False
    OnboardingDialog(on_finish).present(parent)
    return True
