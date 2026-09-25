"""Per-project artifact discovery.

Port of Purge's DevScanner.discoverProjects + ProjectArtifactCatalog. The
important property: a removable folder is only ever proposed when the marker
that identifies its project sits beside it. A lone folder named `build` or
`target` somewhere in Documents is never surfaced, which is what keeps the
generic names on the deletion allowlist safe.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from .. import paths
from ..models import ProjectGroup, ScanItem
from ..services import git_check, prefs
from .base import (
    MAX_DIRECTORY_ENTRIES,
    SKIP_WALK_NAMES,
    CancelToken,
    EventSink,
    ScanEvent,
    build_item,
    path_size,
    resolve_sizes,
)
from .catalog import ProjectRule, catalog

SOURCE = "projects"

#: How deep below the home directory to look for project roots.
MAX_DEPTH = 6

#: Hidden directories worth descending into anyway — people keep projects in
#: dotfile repos and in ~/.local/src.
ALLOWED_HIDDEN = frozenset({".local", ".config"})


def _has_marker(root: str, rule: ProjectRule, names: set[str]) -> bool:
    if rule.root_markers and any(marker in names for marker in rule.root_markers):
        return True
    if rule.root_marker_extensions:
        return any(
            name.endswith(ext) for name in names for ext in rule.root_marker_extensions
        )
    return False


def _refused(artifact: str, rule: ProjectRule) -> bool:
    """Whether something inside the artifact disqualifies it.

    For folders that are usually disposable but sometimes hold real state —
    a `.venv` someone dropped a database into, for instance.
    """
    if not rule.refuse_when_present:
        return False
    try:
        with os.scandir(artifact) as entries:
            present = {entry.name for entry in entries}
    except OSError:
        return True
    for pattern in rule.refuse_when_present:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if any(name.endswith(suffix) for name in present):
                return True
        elif pattern in present:
            return True
    return False


def _has_lockfile(root: str, rule: ProjectRule, names: set[str]) -> bool:
    """Whether this project can be rebuilt reproducibly.

    Without a lockfile a reinstall may resolve to different versions, so the
    row is demoted and needs explicit confirmation — upstream's
    `missingLockfile` friction.
    """
    if not rule.lockfiles:
        return True
    return any(lock in names for lock in rule.lockfiles)


def _walk_project_roots(token: CancelToken):
    """Yield (directory, entry names) for every directory worth inspecting."""
    home = paths.home()
    stack: list[tuple[str, int]] = [(home, 0)]

    while stack:
        if token.cancelled:
            return
        current, depth = stack.pop()
        try:
            with os.scandir(current) as scan:
                entries = list(scan)
        except OSError:
            continue
        if len(entries) > MAX_DIRECTORY_ENTRIES:
            # A directory this size is a data dump, not a project tree.
            continue

        names = {entry.name for entry in entries}
        yield current, names

        if depth >= MAX_DEPTH:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            name = entry.name
            if name in SKIP_WALK_NAMES:
                continue
            if name.startswith(".") and name not in ALLOWED_HIDDEN:
                continue
            if prefs.is_excluded(entry.path):
                continue
            stack.append((entry.path, depth + 1))


def _collect(token: CancelToken) -> tuple[list[ScanItem], dict[str, list[ScanItem]]]:
    rules = catalog().project_rules
    items: list[ScanItem] = []
    by_root: dict[str, list[ScanItem]] = {}
    seen_paths: set[str] = set()

    for directory, names in _walk_project_roots(token):
        for rule in rules:
            if not _has_marker(directory, rule, names):
                continue
            artifact = os.path.join(directory, rule.folder)
            if not os.path.isdir(artifact):
                continue
            normalized = os.path.normpath(artifact)
            if normalized in seen_paths:
                continue
            if _refused(artifact, rule):
                continue

            has_lock = _has_lockfile(directory, rule, names)
            item = build_item(
                artifact,
                source=SOURCE,
                definition_key=None,
                name=rule.label,
                level=rule.level,
                scope="project_artifact",
                icon_name="folder-code-symbolic",
                reinstall_hint=rule.reinstall,
            )
            if item is None:
                continue
            # The definition key names the explanation, but each artifact is
            # its own row: two projects' node_modules must not merge.
            item.definition_key = rule.key
            item.extra = {
                "project_root": directory,
                "project_type": rule.project,
                "has_lockfile": has_lock,
            }
            if not has_lock:
                item.note = "No lockfile — a reinstall may not reproduce the same versions."

            seen_paths.add(normalized)
            items.append(item)
            by_root.setdefault(directory, []).append(item)

    return items, by_root


def _annotate_git(by_root: dict[str, list[ScanItem]], token: CancelToken) -> None:
    """Flag artifacts whose project has uncommitted work."""
    roots = list(by_root)
    if not roots:
        return

    def check(root: str) -> tuple[str, git_check.GitStatus]:
        return root, git_check.status(root)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for root, status in pool.map(check, roots):
            if token.cancelled:
                return
            dirty = status is git_check.GitStatus.DIRTY
            for item in by_root[root]:
                item.extra["git_dirty"] = dirty
                if dirty:
                    item.note = "Uncommitted changes in this repository."


def _build_groups(by_root: dict[str, list[ScanItem]]) -> list[ProjectGroup]:
    stale_days = int(prefs.get("stale_project_days", 90) or 0)
    groups: list[ProjectGroup] = []

    for root, artifacts in by_root.items():
        _, newest = path_size(root) if False else (0, _root_mtime(root))
        group = ProjectGroup(
            root=root,
            name=os.path.basename(root.rstrip("/")) or root,
            types=sorted({a.extra.get("project_type", "generic") for a in artifacts}),
            artifacts=artifacts,
            last_modified=newest,
            git_dirty=any(a.extra.get("git_dirty") for a in artifacts),
        )
        if stale_days and group.stale_days < stale_days:
            # Still actively worked on — not offered under the current
            # "consider stale after" setting.
            continue
        groups.append(group)

    groups.sort(key=lambda g: g.name.lower())
    return groups


@lru_cache(maxsize=1)
def _artifact_folder_names() -> frozenset[str]:
    """Every folder name the catalog treats as build output.

    Needed by `_root_mtime`: a project's age must reflect its *source*. If the
    artifacts counted, every project would look freshly used the moment it was
    built, and the stale threshold would hide exactly the projects it exists
    to surface.
    """
    names = set()
    for rule in catalog().project_rules:
        names.add(rule.folder.split("/")[0])
    return frozenset(names)


def _root_mtime(root: str) -> float:
    """Newest mtime among a project's own files, ignoring its artifacts.

    Sizing the whole tree would be far slower and would report the artifact's
    age rather than the project's.
    """
    newest = 0.0
    ignored = SKIP_WALK_NAMES | _artifact_folder_names()
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name in ignored:
                    continue
                try:
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
    except OSError:
        return 0.0
    return newest


def scan(sink: EventSink, token: CancelToken) -> list[ProjectGroup]:
    sink(ScanEvent(kind="status", status="Looking through your projects…"))

    items, by_root = _collect(token)
    if token.cancelled:
        return []

    _annotate_git(by_root, token)
    groups = _build_groups(by_root)
    kept = {id(a) for g in groups for a in g.artifacts}
    items = [i for i in items if id(i) in kept]

    for item in items:
        sink(ScanEvent(kind="item", item=item))

    sink(ScanEvent(kind="status", status="Measuring project folders…"))
    resolve_sizes(items, sink, token)
    sink(ScanEvent(kind="done"))
    return groups
