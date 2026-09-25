"""Loader for `dev_catalog.json`.

The catalog is data, not code: adding a tool is a JSON edit plus an
explanations entry. Nothing here knows about any specific tool.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

from ..models import SafetyLevel

_SEARCH_PATHS = (
    "/usr/share/purge-linux/dev_catalog.json",
    "/usr/local/share/purge-linux/dev_catalog.json",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "dev_catalog.json",
    ),
)


def _level(raw: str) -> SafetyLevel:
    return SafetyLevel.SAFE if raw == "safe" else SafetyLevel.MEDIUM


@dataclass(frozen=True)
class GlobalCacheRule:
    key: str
    label: str
    paths: tuple[str, ...]
    level: SafetyLevel
    reinstall: str = ""
    note: str = ""


@dataclass(frozen=True)
class ProjectRule:
    key: str
    label: str
    folder: str
    project: str
    root_markers: tuple[str, ...] = ()
    root_marker_extensions: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()
    refuse_when_present: tuple[str, ...] = ()
    level: SafetyLevel = SafetyLevel.SAFE
    reinstall: str = ""


@dataclass
class Catalog:
    global_rules: list[GlobalCacheRule] = field(default_factory=list)
    project_rules: list[ProjectRule] = field(default_factory=list)


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    raw = None
    for candidate in _SEARCH_PATHS:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                break
            except (OSError, ValueError):
                continue
    if raw is None:
        return Catalog()

    return Catalog(
        global_rules=[
            GlobalCacheRule(
                key=item["key"],
                label=item["label"],
                paths=tuple(item.get("paths", ())),
                level=_level(item.get("level", "safe")),
                reinstall=item.get("reinstall", ""),
                note=item.get("note", ""),
            )
            for item in raw.get("global", [])
        ],
        project_rules=[
            ProjectRule(
                key=item["key"],
                label=item["label"],
                folder=item["folder"],
                project=item.get("project", "generic"),
                root_markers=tuple(item.get("root_markers", ())),
                root_marker_extensions=tuple(item.get("root_marker_extensions", ())),
                lockfiles=tuple(item.get("lockfiles", ())),
                refuse_when_present=tuple(item.get("refuse_when_present", ())),
                level=_level(item.get("level", "safe")),
                reinstall=item.get("reinstall", ""),
            )
            for item in raw.get("projects", [])
        ],
    )
