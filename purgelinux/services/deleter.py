"""The only module in this app that removes anything.

Nothing else calls os.remove, os.unlink or shutil.rmtree. Every candidate is
put through DeletionSafetyPolicy.evaluate() before the filesystem is touched,
whether the clean was manual, one-click or scheduled.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Callable, Iterable, Sequence

from ..models import (
    DeletionCandidate,
    DeletionReport,
    DeletionRoute,
    SkippedItem,
)
from ..safety.policy import Decision, evaluate
from . import privileged, trash as trash_service

ProgressCallback = Callable[[int, int, str], None]


def dry_run_enabled() -> bool:
    """A global brake, on by default until the user has cleaned once.

    Set PURGE_DRY_RUN=1 to report what a clean would remove without touching
    the disk. Used heavily during bring-up and kept for support: "run it with
    PURGE_DRY_RUN=1 and send me the list".
    """
    return os.environ.get("PURGE_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


class Deleter:
    def __init__(self, *, dry_run: bool | None = None) -> None:
        self.dry_run = dry_run_enabled() if dry_run is None else dry_run

    # -- public API --------------------------------------------------------

    def delete(
        self,
        candidates: Sequence[DeletionCandidate],
        *,
        progress: ProgressCallback | None = None,
        allow_permanent_fallback: bool = False,
    ) -> DeletionReport:
        """Remove every eligible candidate, returning what happened.

        `allow_permanent_fallback` governs the cross-filesystem case: a file
        that cannot be trashed is skipped unless the user has explicitly
        agreed to delete it permanently.
        """
        report = DeletionReport()
        user_candidates = [c for c in candidates if c.route is not DeletionRoute.HELPER]
        helper_candidates = [c for c in candidates if c.route is DeletionRoute.HELPER]

        total = len(user_candidates) + (1 if helper_candidates else 0)
        done = 0

        for candidate in user_candidates:
            done += 1
            if progress:
                progress(done, total, candidate.name)
            self._delete_one(candidate, report, allow_permanent_fallback)

        if helper_candidates:
            done += 1
            if progress:
                progress(done, total, "System cleanup")
            self._run_helper(helper_candidates, report)

        report.finished_at = time.time()
        return report

    # -- internals ---------------------------------------------------------

    def _delete_one(
        self,
        candidate: DeletionCandidate,
        report: DeletionReport,
        allow_permanent_fallback: bool,
    ) -> None:
        path = candidate.path

        decision = evaluate(path, user_selected=candidate.user_selected)
        if decision is not Decision.ALLOW:
            report.skipped.append(SkippedItem(path, decision.skip_reason or "Skipped"))
            return

        if not os.path.lexists(path):
            # Already gone — a previous clean, or the app that owns it.
            return

        size = trash_service.size_of(path)

        if self.dry_run:
            report.freed_bytes += size
            if candidate.route is DeletionRoute.TRASH:
                report.trashed.append(path)
            else:
                report.removed.append(path)
            return

        if candidate.route is DeletionRoute.TRASH:
            self._trash_one(candidate, report, size, allow_permanent_fallback)
        else:
            self._remove_one(path, report, size)

    def _trash_one(
        self,
        candidate: DeletionCandidate,
        report: DeletionReport,
        size: int,
        allow_permanent_fallback: bool,
    ) -> None:
        try:
            trash_service.trash(candidate.path)
        except trash_service.CrossDeviceError:
            if not allow_permanent_fallback:
                report.skipped.append(
                    SkippedItem(
                        candidate.path,
                        "On another drive — cannot be moved to Trash.",
                    )
                )
                return
            self._remove_one(candidate.path, report, size)
            return
        except trash_service.TrashError as exc:
            report.failed.append(SkippedItem(candidate.path, str(exc)))
            return
        report.trashed.append(candidate.path)
        report.freed_bytes += size

    def _remove_one(self, path: str, report: DeletionReport, size: int) -> None:
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                os.unlink(path)
            else:
                shutil.rmtree(path, onerror=_collect_rmtree_error(report))
        except OSError as exc:
            report.failed.append(SkippedItem(path, exc.strerror or str(exc)))
            return
        if os.path.lexists(path):
            # rmtree reported per-entry errors and left the tree behind.
            return
        report.removed.append(path)
        report.freed_bytes += size

    def _run_helper(
        self, candidates: Sequence[DeletionCandidate], report: DeletionReport
    ) -> None:
        verbs: list[str] = []
        for candidate in candidates:
            if candidate.helper_verb and candidate.helper_verb not in verbs:
                verbs.append(candidate.helper_verb)

        if self.dry_run:
            for candidate in candidates:
                report.removed.append(f"[system] {candidate.name}")
                report.freed_bytes += candidate.size_bytes
            return

        result = privileged.run_verbs(verbs)
        if result.cancelled:
            for candidate in candidates:
                report.skipped.append(SkippedItem(candidate.name, "Cancelled at the password prompt."))
            return
        if not result.ok and not result.removed:
            message = result.errors[0] if result.errors else "System cleanup failed."
            for candidate in candidates:
                report.failed.append(SkippedItem(candidate.name, message))
            return

        report.freed_bytes += result.freed_bytes
        report.removed.extend(result.removed)
        report.skipped.extend(SkippedItem(entry, "Skipped by the system helper.") for entry in result.skipped)
        report.failed.extend(SkippedItem("System cleanup", err) for err in result.errors)


def _collect_rmtree_error(report: DeletionReport):
    def handler(_func, path, exc_info) -> None:
        error = exc_info[1]
        message = getattr(error, "strerror", None) or str(error)
        report.failed.append(SkippedItem(path, message))

    return handler


def retry(candidate: DeletionCandidate, *, dry_run: bool | None = None) -> DeletionReport:
    """Retry a single failed item, as upstream's per-row retry does."""
    return Deleter(dry_run=dry_run).delete([candidate])


def summarize(reports: Iterable[DeletionReport]) -> DeletionReport:
    combined = DeletionReport()
    for report in reports:
        combined.merge(report)
    combined.finished_at = time.time()
    return combined
