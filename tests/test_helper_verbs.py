"""The privileged helper's input handling.

This is the code that runs as root, so what it refuses matters more than what
it does. The helper takes verbs from a fixed set and never a path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

HELPER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "purgelinux", "helper", "purge-system-helper",
)


@pytest.fixture(scope="module")
def helper():
    spec = importlib.util.spec_from_loader(
        "purge_system_helper",
        importlib.machinery.SourceFileLoader("purge_system_helper", HELPER_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_verb_has_an_implementation(helper):
    from purgelinux.services.privileged import VERBS

    assert set(helper.VERBS) == set(VERBS), "client and helper disagree about the verb set"


def test_unknown_verbs_are_refused(helper):
    for bad in ("rm", "apt-clean-all", "../../bin/sh", "", "apt clean"):
        with pytest.raises(ValueError):
            helper.parse(bad)


def test_paths_are_never_accepted_as_verbs(helper):
    for path in ("/etc/passwd", "/home/user/.ssh", "--force", "-rf"):
        with pytest.raises(ValueError):
            helper.parse(path)


def test_only_journal_vacuum_takes_an_argument(helper):
    assert helper.parse("journal-vacuum=200M") == ("journal-vacuum", "200M")
    assert helper.parse("apt-clean") == ("apt-clean", None)
    with pytest.raises(ValueError):
        helper.parse("apt-clean=/etc")


@pytest.mark.parametrize("value", ["200M", "1G", "50M", "500K"])
def test_valid_journal_sizes_accepted(helper, value):
    assert helper.VACUUM_SIZE.match(value)


@pytest.mark.parametrize(
    "value",
    ["200", "M", "200MB", "-1M", "200M; rm -rf /", "$(whoami)", "../../etc", "999999999M"],
)
def test_invalid_journal_sizes_rejected(helper, value):
    result = helper.Result()
    helper.verb_journal_vacuum(result, value)
    assert result.errors, f"accepted {value!r}"
    assert result.freed == 0


def test_helper_refuses_to_run_unprivileged():
    completed = subprocess.run(
        [sys.executable, HELPER_PATH, "apt-clean"],
        capture_output=True, text=True, timeout=30,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert completed.returncode != 0
    assert "root" in payload["errors"][0].lower()
    assert payload["freed_bytes"] == 0


def test_protected_names_are_never_removed(helper, tmp_path):
    """The apt lock file must survive an archives clean."""
    result = helper.Result()
    lock = tmp_path / "lock"
    lock.write_text("x")
    helper.remove_entry(str(lock), result)
    assert lock.exists()
    assert result.skipped == [str(lock)]


def test_client_rejects_unknown_verbs_before_pkexec():
    from purgelinux.services import privileged

    result = privileged.run_verbs(["definitely-not-a-verb"])
    assert not result.ok
    assert "Refusing unknown verb" in result.errors[0]


def test_client_encode_rejects_bad_input():
    from purgelinux.services import privileged

    with pytest.raises(ValueError):
        privileged.encode_verb("rm -rf")
    with pytest.raises(ValueError):
        privileged.encode_verb("apt-clean", "/etc")
    assert privileged.encode_verb("journal-vacuum", "200M") == "journal-vacuum=200M"
