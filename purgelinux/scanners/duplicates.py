"""Byte-identical file detection.

Port of Purge's DuplicateFileDetector. Three stages, cheapest first: group by
size, then by a hash of the head of the file, then confirm with a full-file
hash. The final confirmation is a real byte comparison for the surviving
pair, so a hash collision can never cause a wrong deletion.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict

from ..models import DuplicateGroup, ScanItem
from .base import CancelToken

HEAD_BYTES = 4096
CHUNK = 1024 * 1024


def _hash_head(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.blake2b(handle.read(HEAD_BYTES), digest_size=16).hexdigest()
    except OSError:
        return None


def _hash_full(path: str) -> str | None:
    digest = hashlib.blake2b(digest_size=32)
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _identical(left: str, right: str) -> bool:
    """Final byte-for-byte confirmation before anything is offered."""
    try:
        with open(left, "rb") as a, open(right, "rb") as b:
            while True:
                chunk_a = a.read(CHUNK)
                chunk_b = b.read(CHUNK)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except OSError:
        return False


def find(items: list[ScanItem], token: CancelToken) -> list[DuplicateGroup]:
    """Group byte-identical files among the rows already found."""
    by_size: dict[int, list[ScanItem]] = defaultdict(list)
    for item in items:
        size = item.size_bytes
        if size > 0:
            by_size[size].append(item)

    groups: list[DuplicateGroup] = []

    for size, candidates in by_size.items():
        if token.cancelled:
            return groups
        if len(candidates) < 2:
            continue

        by_head: dict[str, list[ScanItem]] = defaultdict(list)
        for item in candidates:
            head = _hash_head(item.path)
            if head:
                by_head[head].append(item)

        for head_group in by_head.values():
            if token.cancelled:
                return groups
            if len(head_group) < 2:
                continue

            by_full: dict[str, list[ScanItem]] = defaultdict(list)
            for item in head_group:
                full = _hash_full(item.path)
                if full:
                    by_full[full].append(item)

            for digest, matched in by_full.items():
                if len(matched) < 2:
                    continue
                confirmed = [matched[0]]
                for candidate in matched[1:]:
                    if _identical(matched[0].path, candidate.path):
                        confirmed.append(candidate)
                if len(confirmed) < 2:
                    continue
                groups.append(
                    DuplicateGroup(group_id=f"dup:{digest[:16]}:{size}", size_each=size, items=confirmed)
                )

    groups.sort(key=lambda g: -g.reclaimable_bytes)
    return groups


def cleanup_items(group: DuplicateGroup) -> list[ScanItem]:
    """Everything in the group except the chosen keeper."""
    return [item for item in group.items if item.item_id != group.keeper_id]
