"""Loads and resolves `explanations.json`.

Port of Purge's ExplanationDatabase / ExplanationResolver. Every row the user
sees carries a plain-English explanation and a tier, and both come from this
file rather than from code, so the catalog grows without touching logic.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

from ..models import SafetyLevel

#: Searched in order. The first is the installed location, the second the
#: source checkout so `make run` works without installing.
_SEARCH_PATHS = (
    "/usr/share/purge-linux/explanations.json",
    "/usr/local/share/purge-linux/explanations.json",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "explanations.json"),
)


@dataclass(frozen=True)
class Explanation:
    key: str
    display_name: str
    tag: str
    explanation: str
    aliases: tuple[str, ...]
    scope: str

    @property
    def level(self) -> SafetyLevel:
        return SafetyLevel.SAFE if self.tag == "safe" else SafetyLevel.MEDIUM


class ExplanationDatabase:
    def __init__(self, entries: list[Explanation]) -> None:
        self._entries = entries
        self._by_key: dict[str, Explanation] = {}
        self._by_alias: dict[str, Explanation] = {}
        self._by_scoped_alias: dict[tuple[str, str], Explanation] = {}
        for entry in entries:
            self._by_key[entry.key.lower()] = entry
            for alias in (*entry.aliases, entry.display_name):
                # First alias wins: later catalogs reuse generic names like
                # "cache", and the specific entry should not be overwritten.
                self._by_alias.setdefault(alias.lower(), entry)
                if entry.scope:
                    self._by_scoped_alias.setdefault((alias.lower(), entry.scope), entry)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def keys(self) -> set[str]:
        return set(self._by_key)

    def by_key(self, key: str | None) -> Explanation | None:
        if not key:
            return None
        return self._by_key.get(key.lower())

    def _by_alias_in_scope(self, name: str, scope: str | None) -> Explanation | None:
        """Alias lookup that will not hand a row another scope's entry.

        Generic aliases like "cache" are claimed by whichever catalog entry
        loads first. Without the scope guard an Electron app's
        ~/.config/<App>/Cache picks up `yarn-project-cache` and the row is
        captioned "Yarn's in-project package cache" — a wrong explanation on a
        row the user is about to approve for deletion. Refusing the match lets
        the caller's `fallback_key` supply the correct generic text instead.
        """
        lowered = name.lower()
        if scope:
            scoped = self._by_scoped_alias.get((lowered, scope))
            if scoped is not None:
                return scoped
        hit = self._by_alias.get(lowered)
        if hit is None:
            return None
        if scope and hit.scope and hit.scope != scope:
            return None
        return hit

    def resolve(
        self,
        key: str | None = None,
        folder_name: str | None = None,
        path: str | None = None,
        scope: str | None = None,
    ) -> Explanation | None:
        """Best match for a row, trying key, then folder name, then path parts."""
        hit = self.by_key(key)
        if hit:
            return hit
        if key:
            hit = self._by_alias_in_scope(key, scope)
            if hit:
                return hit
        if folder_name:
            hit = self._by_alias_in_scope(folder_name, scope)
            if hit:
                return hit
        if path:
            # Walk the path from the most specific component outwards, so
            # ".../Code/CachedData" prefers the CachedData entry over "Code".
            parts = [p for p in os.path.normpath(path).split(os.sep) if p]
            for part in reversed(parts):
                hit = self._by_alias.get(part.lower())
                if hit and (scope is None or hit.scope == scope):
                    return hit
            for part in reversed(parts):
                hit = self._by_alias_in_scope(part, scope)
                if hit:
                    return hit
        return None


def _load_entries() -> list[Explanation]:
    for candidate in _SEARCH_PATHS:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            continue
        return [
            Explanation(
                key=item["key"],
                display_name=item.get("display_name", item["key"]),
                tag=item.get("tag", "medium"),
                explanation=item.get("explanation", ""),
                aliases=tuple(item.get("aliases", ())),
                scope=item.get("scope", ""),
            )
            for item in raw
        ]
    return []


@lru_cache(maxsize=1)
def database() -> ExplanationDatabase:
    return ExplanationDatabase(_load_entries())
