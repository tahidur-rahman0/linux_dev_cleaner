"""XDG path helpers.

Everything that needs to know where the user's data lives goes through here,
so tests can point the whole app at a temporary tree by setting HOME.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache


def home() -> str:
    return os.path.expanduser("~")


def _xdg(var: str, default_rel: str) -> str:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return value
    return os.path.join(home(), default_rel)


def cache_home() -> str:
    return _xdg("XDG_CACHE_HOME", ".cache")


def config_home() -> str:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_home() -> str:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_home() -> str:
    return _xdg("XDG_STATE_HOME", ".local/state")


def flatpak_root() -> str:
    return os.path.join(home(), ".var", "app")


def snap_root() -> str:
    return os.path.join(home(), "snap")


def trash_dir() -> str:
    return os.path.join(data_home(), "Trash")


def app_config_dir() -> str:
    path = os.path.join(config_home(), "purge-linux")
    os.makedirs(path, exist_ok=True)
    return path


#: XDG user-dir keys we scan for large files.
USER_DIR_KEYS = ("DOCUMENTS", "DESKTOP", "DOWNLOAD", "VIDEOS", "MUSIC", "PICTURES")

_FALLBACK_USER_DIRS = {
    "DOCUMENTS": "Documents",
    "DESKTOP": "Desktop",
    "DOWNLOAD": "Downloads",
    "VIDEOS": "Videos",
    "MUSIC": "Music",
    "PICTURES": "Pictures",
}


@lru_cache(maxsize=1)
def _user_dirs_from_config() -> dict[str, str]:
    """Parse ~/.config/user-dirs.dirs.

    Read directly rather than shelling out to `xdg-user-dir`: one less process
    per key, and it keeps working on a system without xdg-utils installed.
    Localized installs name these folders in the user's own language, so
    hardcoding "Downloads" would silently scan nothing.
    """
    result: dict[str, str] = {}
    config = os.path.join(config_home(), "user-dirs.dirs")
    try:
        with open(config, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, raw = line.partition("=")
                name = name.strip()
                if not name.startswith("XDG_") or not name.endswith("_DIR"):
                    continue
                key = name[len("XDG_"):-len("_DIR")]
                value = raw.strip().strip('"').strip("'")
                value = value.replace("$HOME", home())
                if value:
                    result[key] = os.path.normpath(value)
    except OSError:
        pass
    return result


def user_dir(key: str) -> str:
    """Resolve one XDG user directory, falling back to the English name."""
    configured = _user_dirs_from_config().get(key)
    if configured:
        return configured
    return os.path.join(home(), _FALLBACK_USER_DIRS.get(key, key.title()))


def scan_user_dirs() -> list[str]:
    """Existing user directories the Large Files scanner walks."""
    seen: list[str] = []
    for key in USER_DIR_KEYS:
        path = user_dir(key)
        if path not in seen and os.path.isdir(path) and os.path.normpath(path) != os.path.normpath(home()):
            seen.append(path)
    return seen


def which(program: str) -> str | None:
    from shutil import which as _which

    return _which(program)


def resolve_flutter_sdk() -> str | None:
    """Locate the Flutter SDK root.

    `which flutter` points at <sdk>/bin/flutter, often through a symlink, so
    resolve it before walking up. Falls back to the usual install locations,
    including the snap.
    """
    binary = which("flutter")
    if binary:
        real = os.path.realpath(binary)
        sdk = os.path.dirname(os.path.dirname(real))
        if os.path.isdir(os.path.join(sdk, "bin", "cache")):
            return sdk
    for candidate in (
        os.path.join(home(), "flutter"),
        os.path.join(home(), "development", "flutter"),
        os.path.join(home(), "snap", "flutter", "common", "flutter"),
        "/opt/flutter",
        "/usr/lib/flutter",
    ):
        if os.path.isdir(os.path.join(candidate, "bin", "cache")):
            return candidate
    return None


def is_running(process_names: tuple[str, ...]) -> bool:
    """Whether any of the given process names is currently running."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return _is_running_pgrep(process_names)
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if any(target in name for target in process_names):
                return True
    except Exception:
        return False
    return False


def _is_running_pgrep(process_names: tuple[str, ...]) -> bool:
    for target in process_names:
        try:
            completed = subprocess.run(
                ["pgrep", "-x", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode == 0:
            return True
    return False
