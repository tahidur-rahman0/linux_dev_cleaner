"""The deletion engine: routing, safety gating, and dry run."""

from __future__ import annotations

import os

from purgelinux.models import DeletionCandidate, DeletionRoute, SafetyLevel
from purgelinux.services.deleter import Deleter


def _candidate(path: str, route=DeletionRoute.REMOVE, user_selected=False, size=1000):
    return DeletionCandidate(
        item_id="id", name=os.path.basename(path), path=path, size_bytes=size,
        route=route, safety=SafetyLevel.SAFE, user_selected=user_selected,
    )


def test_caches_are_removed_outright(make_tree, fake_home):
    path = make_tree(".cache/some-app/blob.bin", "x" * 4000)
    target = os.path.dirname(path)

    report = Deleter(dry_run=False).delete([_candidate(target)])
    assert not os.path.exists(target)
    assert report.removed == [target]
    assert report.trashed == []
    assert report.freed_bytes > 0


def test_personal_files_go_to_the_trash(make_tree, fake_home):
    path = make_tree("Documents/movie.mp4", "x" * 9000)

    report = Deleter(dry_run=False).delete(
        [_candidate(path, route=DeletionRoute.TRASH, user_selected=True)]
    )
    assert not os.path.exists(path)
    assert report.trashed == [path]
    assert (fake_home / ".local/share/Trash/files/movie.mp4").exists()


def test_protected_paths_are_skipped_not_deleted(make_tree, fake_home):
    path = make_tree(".ssh/id_rsa", "secret")

    report = Deleter(dry_run=False).delete([_candidate(path, user_selected=True)])
    assert os.path.exists(path)
    assert report.removed == [] and report.trashed == []
    assert "Protected" in report.skipped[0].reason


def test_dry_run_touches_nothing(make_tree, fake_home):
    path = make_tree(".cache/some-app/blob.bin", "x" * 4000)
    target = os.path.dirname(path)

    report = Deleter(dry_run=True).delete([_candidate(target)])
    assert os.path.exists(target), "dry run deleted a real directory"
    assert report.freed_bytes > 0
    assert report.removed == [target]


def test_dry_run_flag_reads_the_environment(monkeypatch):
    monkeypatch.setenv("PURGE_DRY_RUN", "1")
    assert Deleter().dry_run is True
    monkeypatch.setenv("PURGE_DRY_RUN", "0")
    assert Deleter().dry_run is False


def test_already_missing_paths_are_not_errors(fake_home):
    report = Deleter(dry_run=False).delete([_candidate(str(fake_home / ".cache/gone"))])
    assert report.failed == [] and report.removed == []


def test_helper_route_is_batched_into_verbs(make_tree, monkeypatch):
    from purgelinux.services import deleter as deleter_module

    captured = {}

    def fake_run(verbs, **kwargs):
        captured["verbs"] = verbs
        from purgelinux.services.privileged import HelperResult

        return HelperResult(ok=True, freed_bytes=5000, removed=["cleaned"])

    monkeypatch.setattr(deleter_module.privileged, "run_verbs", fake_run)

    candidates = [
        DeletionCandidate("a", "APT", "apt://x", 1000, DeletionRoute.HELPER, SafetyLevel.SAFE, helper_verb="apt-clean"),
        DeletionCandidate("b", "Logs", "log://x", 2000, DeletionRoute.HELPER, SafetyLevel.SAFE, helper_verb="journal-vacuum=200M"),
        DeletionCandidate("c", "APT again", "apt://y", 500, DeletionRoute.HELPER, SafetyLevel.SAFE, helper_verb="apt-clean"),
    ]
    report = Deleter(dry_run=False).delete(candidates)

    # One pkexec call, deduplicated verbs: one password prompt for the batch.
    assert captured["verbs"] == ["apt-clean", "journal-vacuum=200M"]
    assert report.freed_bytes == 5000


def test_cancelled_authentication_is_reported_as_skipped(monkeypatch):
    from purgelinux.services import deleter as deleter_module
    from purgelinux.services.privileged import HelperResult

    monkeypatch.setattr(
        deleter_module.privileged, "run_verbs",
        lambda verbs, **kwargs: HelperResult(ok=False, cancelled=True),
    )
    report = Deleter(dry_run=False).delete(
        [DeletionCandidate("a", "APT", "apt://x", 1000, DeletionRoute.HELPER, SafetyLevel.SAFE, helper_verb="apt-clean")]
    )
    assert report.failed == []
    assert "Cancelled" in report.skipped[0].reason
