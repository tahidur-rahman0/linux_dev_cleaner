"""Dev Tools scanner — global caches.

Reads `dev_catalog.json` and turns each entry into a row. The only logic that
is not data-driven is version-keeping: for SDK payloads that ship one
directory per version (NDK, Android platforms, build-tools), the newest is
always kept, because offering to delete the version you are building against
would be a trap.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess

from .. import paths
from ..models import ScanItem
from .base import (
    CancelToken,
    EventSink,
    ScanEvent,
    build_item,
    merge_by_definition,
    resolve_sizes,
)
from .catalog import GlobalCacheRule, catalog

SOURCE = "dev_tools"

#: Rules whose paths expand to one directory per version, and how many of the
#: newest to keep out of the list.
VERSION_KEEP: dict[str, int] = {
    "android-ndk": 1,
    "android-platforms": 2,
    "android-build-tools": 1,
    "gradle-dists": 0,
    "vscode-server": 1,
}

_VERSION_PART = re.compile(r"(\d+)")


def _version_key(name: str) -> tuple:
    """Sort key that orders `android-34` after `android-9`, not before it."""
    return tuple(int(part) if part.isdigit() else part.lower() for part in _VERSION_PART.split(name))


def _expand(pattern: str) -> list[str]:
    """Expand ~, {flutter_sdk} and shell globs into existing directories."""
    if "{flutter_sdk}" in pattern:
        sdk = paths.resolve_flutter_sdk()
        if sdk is None:
            return []
        pattern = pattern.replace("{flutter_sdk}", sdk)
    expanded = os.path.expanduser(pattern)
    if any(ch in expanded for ch in "*?["):
        return sorted(p for p in glob.glob(expanded) if os.path.isdir(p))
    return [expanded] if os.path.isdir(expanded) else []


def _active_rust_toolchain() -> str | None:
    """The toolchain rustup would use, so it is never offered for deletion."""
    try:
        completed = subprocess.run(
            ["rustup", "show", "active-toolchain"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    return line.split(" ")[0] or None


def _paths_for(rule: GlobalCacheRule) -> list[str]:
    found: list[str] = []
    for pattern in rule.paths:
        found.extend(_expand(pattern))

    keep = VERSION_KEEP.get(rule.key)
    if keep:
        # Sort by version and drop the newest `keep` entries from the offer.
        ordered = sorted(found, key=lambda p: _version_key(os.path.basename(p)))
        found = ordered[:-keep] if len(ordered) > keep else []

    if rule.key == "rustup-toolchains":
        active = _active_rust_toolchain()
        if active:
            found = [p for p in found if os.path.basename(p) != active]

    return found


def _docker_row() -> ScanItem | None:
    """Read-only Docker row.

    Deleting the daemon's storage underneath it corrupts state, so this row
    reports what Docker says it could reclaim and hands the action back to the
    user. It is never selectable.
    """
    from ..models import DeletionRoute, Location, SafetyLevel
    from ..safety.explanations import database

    if not paths.which("docker"):
        return None
    try:
        completed = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}}\t{{.Reclaimable}}"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None

    total = 0
    for line in completed.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        total += _parse_docker_size(parts[1])
    if total <= 0:
        return None

    entry = database().by_key("docker-reclaimable")
    if entry is None:
        return None
    return ScanItem(
        item_id="docker:reclaimable",
        name="Docker",
        locations=[Location(path="/var/lib/docker", size_bytes=total, key="docker")],
        safety=SafetyLevel.MEDIUM,
        source=SOURCE,
        definition_key="docker-reclaimable",
        explanation=entry.explanation,
        icon_name="package-x-generic-symbolic",
        route=DeletionRoute.REMOVE,
        reinstall_hint="docker system prune",
        note="Read-only — run docker system prune yourself.",
        size_resolved=True,
        extra={"read_only": True},
    )


def _parse_docker_size(text: str) -> int:
    """Parse "1.23GB (45%)" as bytes."""
    match = re.match(r"\s*([\d.]+)\s*([KMGT]?B)", text.strip(), re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).upper()
    factor = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}.get(unit, 1)
    return int(value * factor)


def scan(sink: EventSink, token: CancelToken) -> None:
    sink(ScanEvent(kind="status", status="Looking for developer caches…"))

    items: list[ScanItem] = []
    for rule in catalog().global_rules:
        if token.cancelled:
            return
        for path in _paths_for(rule):
            item = build_item(
                path,
                source=SOURCE,
                definition_key=rule.key,
                name=rule.label,
                level=rule.level,
                scope="dev_tool",
                icon_name="applications-engineering-symbolic",
                reinstall_hint=rule.reinstall,
                note=rule.note,
                fallback_key="generic-dev-cache",
            )
            if item is not None:
                items.append(item)

    items = merge_by_definition(items)

    docker = _docker_row()
    if docker is not None:
        items.append(docker)

    for item in items:
        if token.cancelled:
            return
        sink(ScanEvent(kind="item", item=item))

    sink(ScanEvent(kind="status", status="Measuring developer caches…"))
    resolve_sizes([i for i in items if not i.extra.get("read_only")], sink, token)
    sink(ScanEvent(kind="done"))
