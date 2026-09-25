"""Disk usage for the sidebar summary."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .. import paths
from ..models import format_bytes


@dataclass(frozen=True)
class DiskUsage:
    total_bytes: int
    free_bytes: int

    @property
    def used_bytes(self) -> int:
        return max(0, self.total_bytes - self.free_bytes)

    @property
    def used_fraction(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self.used_bytes / self.total_bytes)

    @property
    def summary(self) -> str:
        return f"{format_bytes(self.used_bytes)} used · {format_bytes(self.free_bytes)} free"


def usage(path: str | None = None) -> DiskUsage:
    """Usage for the filesystem holding the home directory.

    statvfs rather than `df`: one syscall, and it does not depend on which
    coreutils implementation the distribution ships.
    """
    target = path or paths.home()
    try:
        stats = os.statvfs(target)
    except OSError:
        return DiskUsage(0, 0)
    block = stats.f_frsize or stats.f_bsize
    total = stats.f_blocks * block
    # f_bavail, not f_bfree: blocks reserved for root are not free to the user.
    free = stats.f_bavail * block
    return DiskUsage(total_bytes=total, free_bytes=free)
