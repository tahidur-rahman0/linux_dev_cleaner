"""Every row the app can produce must carry an explanation."""

from __future__ import annotations

from purgelinux.safety.explanations import database
from purgelinux.scanners.catalog import catalog


def test_database_loads():
    assert len(database()) > 100


def test_every_catalog_key_resolves():
    db = database()
    missing = sorted(
        {rule.key for rule in catalog().global_rules}
        | {rule.key for rule in catalog().project_rules}
        - db.keys
    )
    missing = [key for key in missing if db.by_key(key) is None]
    assert missing == [], f"catalog keys with no explanation: {missing}"


def test_every_entry_has_text_and_a_valid_tag():
    for key in database().keys:
        entry = database().by_key(key)
        assert entry.explanation.strip(), f"{key} has no explanation"
        assert entry.tag in ("safe", "medium"), f"{key} has tag {entry.tag}"
        assert entry.display_name.strip(), f"{key} has no display name"


def test_system_verbs_all_have_explanations():
    from purgelinux.services.privileged import VERBS
    from purgelinux.scanners import system

    db = database()
    for key in ("apt-archives", "apt-lists", "apt-autoremove", "old-kernels",
                "residual-configs", "journal-logs", "snap-old-revisions",
                "snapd-cache", "crash-reports", "rotated-logs"):
        assert db.by_key(key) is not None, f"system row {key} has no explanation"
    assert VERBS and system.SOURCE == "system"


def test_resolution_prefers_the_specific_entry():
    db = database()
    assert db.resolve(key="chrome-cache").display_name == "Google Chrome Cache"
    assert db.resolve(folder_name="node_modules").key == "node-modules"
