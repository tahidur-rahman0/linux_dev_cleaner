"""The onboarding carousel must show one step at a time.

AdwCarousel allocates each page its *natural* width. An AdwStatusPage asks for
far less than the 560px dialog, so without hexpand all three steps are laid
out side by side and clipped.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

Adw.init()

from purgelinux.ui.onboarding import STEPS, OnboardingDialog  # noqa: E402


def test_every_step_expands_to_fill_the_carousel(fake_home):
    dialog = OnboardingDialog(lambda: None)
    for index in range(len(STEPS)):
        page = dialog.carousel.get_nth_page(index)
        assert page.get_hexpand(), f"step {index} does not expand — pages will tile"
