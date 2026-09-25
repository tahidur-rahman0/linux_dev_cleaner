"""Client for the root helper.

The GUI never runs as root. Everything that needs privilege is expressed as a
*verb* from a fixed set, and the helper hardcodes the paths each verb touches.
No path from the GUI is ever passed to root — that is the whole point of the
design, so do not add a path argument here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

HELPER_PATHS = (
    "/usr/libexec/purge-linux/purge-system-helper",
    "/usr/lib/purge-linux/purge-system-helper",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "helper",
        "purge-system-helper",
    ),
)

#: The complete set of things root is ever asked to do.
VERBS: frozenset[str] = frozenset(
    {
        "apt-clean",
        "apt-lists-clean",
        "apt-autoremove",
        "dpkg-purge-rc",
        "journal-vacuum",
        "snap-prune",
        "snapd-cache-clean",
        "crash-clean",
        "rotated-log-clean",
    }
)

#: Only verb taking an argument, and it must look like a journald size limit.
PARAMETERIZED = {"journal-vacuum"}


@dataclass
class HelperResult:
    ok: bool
    freed_bytes: int = 0
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def helper_path() -> str | None:
    for candidate in HELPER_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def available() -> bool:
    return helper_path() is not None and shutil.which("pkexec") is not None


def encode_verb(verb: str, argument: str | None = None) -> str:
    if verb not in VERBS:
        raise ValueError(f"unknown verb: {verb}")
    if argument is None:
        return verb
    if verb not in PARAMETERIZED:
        raise ValueError(f"verb {verb} takes no argument")
    return f"{verb}={argument}"


def run_verbs(verbs: list[str], *, timeout: int = 900) -> HelperResult:
    """Run a batch of verbs under a single pkexec authentication.

    Batching matters for the experience: polkit's auth_admin_keep means the
    user types their password once for the whole clean rather than once per
    row.
    """
    if not verbs:
        return HelperResult(ok=True)

    # Validate the request before looking at the environment: bad input is
    # refused on every machine, not only where pkexec happens to be missing.
    for encoded in verbs:
        name = encoded.split("=", 1)[0]
        if name not in VERBS:
            return HelperResult(ok=False, errors=[f"Refusing unknown verb: {name}"])

    binary = helper_path()
    if binary is None:
        return HelperResult(ok=False, errors=["Privileged helper is not installed."])
    if shutil.which("pkexec") is None:
        return HelperResult(ok=False, errors=["pkexec is not available on this system."])

    try:
        completed = subprocess.run(
            ["pkexec", binary, *verbs],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return HelperResult(ok=False, errors=["The system cleanup timed out."])
    except OSError as exc:
        return HelperResult(ok=False, errors=[str(exc)])

    # 126 = authorization dialog dismissed, 127 = pkexec could not run it.
    if completed.returncode == 126:
        return HelperResult(ok=False, cancelled=True)
    if completed.returncode == 127:
        return HelperResult(ok=False, errors=["Could not start the privileged helper."])

    payload = _parse(completed.stdout)
    if payload is None:
        message = completed.stderr.strip() or "The privileged helper returned no result."
        return HelperResult(ok=False, errors=[message])

    return HelperResult(
        ok=completed.returncode == 0,
        freed_bytes=int(payload.get("freed_bytes", 0)),
        removed=list(payload.get("removed", [])),
        skipped=list(payload.get("skipped", [])),
        errors=list(payload.get("errors", [])),
    )


def _parse(stdout: str) -> dict | None:
    """Last JSON object on stdout, ignoring anything the tools printed."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None
