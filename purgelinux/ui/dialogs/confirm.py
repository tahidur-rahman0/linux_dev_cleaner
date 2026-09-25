"""Confirmation sheets.

Every row states its fate plainly — moved to Trash or deleted permanently.
Upstream can promise "nothing is ever deleted permanently"; on Linux that
promise would be false for caches, so the sheet says which is which instead
of implying a reversibility the app cannot deliver.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from ...models import DeletionCandidate, DeletionRoute, SafetyLevel, format_bytes

MAX_LISTED = 40


def _group(candidates: list[DeletionCandidate]) -> tuple[list, list, list]:
    trashed = [c for c in candidates if c.route is DeletionRoute.TRASH]
    system = [c for c in candidates if c.route is DeletionRoute.HELPER]
    removed = [c for c in candidates if c.route is DeletionRoute.REMOVE]
    return trashed, removed, system


def build_confirm_dialog(parent, candidates: list[DeletionCandidate], on_confirm) -> Adw.AlertDialog:
    total = sum(c.size_bytes for c in candidates)
    trashed, removed, system = _group(candidates)

    dialog = Adw.AlertDialog(
        heading=f"Clean {format_bytes(total)}?",
        body=f"{len(candidates)} items selected.",
    )

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

    if trashed:
        content.append(_section(
            "Moved to Trash",
            "Restorable from Files until you empty the Trash.",
            trashed,
        ))
    if removed:
        content.append(_section(
            "Deleted permanently",
            "Caches and build output. Moving these to the Trash would not free any space, "
            "so they are removed outright. Everything here rebuilds itself.",
            removed,
        ))
    if system:
        content.append(_section(
            "System cleanup",
            "Runs as administrator. You will be asked for your password once.",
            system,
        ))

    risky = [c for c in candidates if c.safety is SafetyLevel.MEDIUM]
    if risky:
        warning = Gtk.Label(
            label=f"{len(risky)} of these are marked Check First — they rebuild, but you will notice the cost.",
            wrap=True, xalign=0.0,
        )
        warning.add_css_class("scan-row-note")
        content.append(warning)

    scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=380)
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_child(content)
    dialog.set_extra_child(scroller)

    dialog.add_response("cancel", "Cancel")
    dialog.add_response("clean", f"Clean {format_bytes(total)}")
    dialog.set_response_appearance("clean", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, response: on_confirm() if response == "clean" else None)
    return dialog


def _section(title: str, explanation: str, candidates: list[DeletionCandidate]) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    total = sum(c.size_bytes for c in candidates)
    header = Gtk.Label(label=f"{title} — {len(candidates)} items, {format_bytes(total)}", xalign=0.0)
    header.add_css_class("heading")
    box.append(header)

    note = Gtk.Label(label=explanation, xalign=0.0, wrap=True)
    note.add_css_class("dim-label-small")
    box.append(note)

    listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    for candidate in sorted(candidates, key=lambda c: -c.size_bytes)[:MAX_LISTED]:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=candidate.name, xalign=0.0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        size = Gtk.Label(label=candidate.formatted_size, xalign=1.0)
        size.add_css_class("scan-row-size")
        row.append(name)
        row.append(size)
        listing.append(row)

    if len(candidates) > MAX_LISTED:
        more = Gtk.Label(label=f"…and {len(candidates) - MAX_LISTED} more", xalign=0.0)
        more.add_css_class("dim-label-small")
        listing.append(more)

    box.append(listing)
    return box


def build_result_dialog(report) -> Adw.AlertDialog:
    heading = f"Freed {report.formatted_freed}"
    parts = [f"{report.item_count} items cleaned."]
    if report.skipped:
        parts.append(f"{len(report.skipped)} skipped for safety.")
    if report.failed:
        parts.append(f"{len(report.failed)} could not be removed.")

    dialog = Adw.AlertDialog(heading=heading, body=" ".join(parts))

    if report.skipped or report.failed:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for entry in (report.skipped + report.failed)[:20]:
            label = Gtk.Label(label=f"{entry.path} — {entry.reason}", xalign=0.0, wrap=True)
            label.add_css_class("dim-label-small")
            box.append(label)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=240)
        scroller.set_child(box)
        dialog.set_extra_child(scroller)

    dialog.add_response("ok", "Done")
    dialog.set_default_response("ok")
    return dialog
