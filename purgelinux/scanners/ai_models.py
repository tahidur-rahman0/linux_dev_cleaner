"""Local AI model discovery.

Ollama stores models content-addressed: several models can share a blob, so
counting every blob per model would wildly overstate what deleting one would
free. Each blob is therefore counted once, against the first model that
references it — the same correction upstream makes.
"""

from __future__ import annotations

import json
import os

from .. import paths
from ..models import DeletionRoute, Location, SafetyLevel, ScanItem
from .base import CancelToken, EventSink, ScanEvent

SOURCE = "large_files"
MODEL_EXTENSIONS = (".gguf", ".ggml", ".safetensors", ".bin", ".pt", ".pth")


def _row(name: str, path: str, size: int, mtime: float, key: str, detail: str) -> ScanItem:
    return ScanItem(
        item_id=ScanItem.make_id(None, path),
        name=name,
        locations=[Location(path=path, size_bytes=size, last_modified=mtime, key="ai_model")],
        safety=SafetyLevel.MEDIUM,
        source=SOURCE,
        definition_key=key,
        explanation=detail,
        icon_name="applications-science-symbolic",
        route=DeletionRoute.TRASH,
        size_resolved=True,
        user_selected=True,
        extra={"category": "ai_model", "source_label": "AI Models"},
    )


def _ollama(items: list[ScanItem], token: CancelToken) -> None:
    root = os.path.join(paths.home(), ".ollama", "models")
    manifests = os.path.join(root, "manifests")
    blobs = os.path.join(root, "blobs")
    if not os.path.isdir(manifests) or not os.path.isdir(blobs):
        return

    claimed: set[str] = set()

    for current, _dirs, files in os.walk(manifests):
        if token.cancelled:
            return
        for filename in files:
            manifest_path = os.path.join(current, filename)
            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except (OSError, ValueError):
                continue

            digests = []
            for layer in manifest.get("layers", []):
                digest = layer.get("digest")
                if digest:
                    digests.append(digest)
            config_digest = (manifest.get("config") or {}).get("digest")
            if config_digest:
                digests.append(config_digest)

            size = 0
            newest = 0.0
            for digest in digests:
                blob = os.path.join(blobs, digest.replace(":", "-"))
                if digest in claimed or not os.path.isfile(blob):
                    continue
                # Shared blobs count once: deleting this model only reclaims
                # the bytes no other model still references.
                claimed.add(digest)
                try:
                    stat = os.stat(blob)
                except OSError:
                    continue
                size += stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
                newest = max(newest, stat.st_mtime)

            if size <= 0:
                continue

            relative = os.path.relpath(current, manifests).replace(os.sep, "/")
            tag = f"{relative.split('/', 2)[-1]}:{filename}" if "/" in relative else filename
            items.append(
                _row(
                    name=tag,
                    path=manifest_path,
                    size=size,
                    mtime=newest,
                    key="ollama-model",
                    detail="Ollama model. Deleting it frees the blobs no other model uses; pulling it again is a large download.",
                )
            )


def _flat_model_dir(items: list[ScanItem], root: str, key: str, detail: str, token: CancelToken) -> None:
    if not os.path.isdir(root):
        return
    for current, dirs, files in os.walk(root):
        if token.cancelled:
            return
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for filename in files:
            if not filename.lower().endswith(MODEL_EXTENSIONS):
                continue
            path = os.path.join(current, filename)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            size = stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
            if size < 50 * 1000 * 1000:
                continue
            items.append(_row(filename, path, size, stat.st_mtime, key, detail))


def _huggingface(items: list[ScanItem], token: CancelToken) -> None:
    hub = os.path.join(paths.cache_home(), "huggingface", "hub")
    if not os.path.isdir(hub):
        return
    try:
        entries = list(os.scandir(hub))
    except OSError:
        return
    for entry in entries:
        if token.cancelled:
            return
        if not entry.is_dir(follow_symlinks=False) or not entry.name.startswith("models--"):
            continue
        size = 0
        newest = 0.0
        for current, _dirs, files in os.walk(entry.path):
            for filename in files:
                try:
                    stat = os.stat(os.path.join(current, filename))
                except OSError:
                    continue
                size += stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
                newest = max(newest, stat.st_mtime)
        if size < 50 * 1000 * 1000:
            continue
        repo = entry.name[len("models--"):].replace("--", "/")
        items.append(
            _row(repo, entry.path, size, newest, "huggingface-cache",
                 "Model downloaded from Hugging Face. Deleting it is safe but it is a large download to restore.")
        )


def scan(sink: EventSink, token: CancelToken) -> None:
    items: list[ScanItem] = []
    _ollama(items, token)
    _flat_model_dir(
        items, os.path.join(paths.home(), ".lmstudio", "models"), "lmstudio-model",
        "LM Studio model. Deleting it frees a lot of space; downloading it again takes a while.", token,
    )
    _flat_model_dir(
        items, os.path.join(paths.data_home(), "nomic.ai"), "gpt4all-model",
        "GPT4All model. Deleting it is safe but it is a large download to restore.", token,
    )
    _huggingface(items, token)

    for item in items:
        sink(ScanEvent(kind="item", item=item))
