"""An alias must not carry an explanation across scopes.

`explanations.json` reuses bare folder names as aliases — "cache" belongs to
`yarn-project-cache`, scope `project_artifact`. An Electron app's
~/.config/<App>/Cache is scope `app_caches` and must not inherit yarn's text:
the row would then describe something other than what it deletes.
"""

from __future__ import annotations

import pathlib

from purgelinux.safety.explanations import database


def test_cache_alias_does_not_leak_out_of_its_scope():
    db = database()
    yarn = db.by_key("yarn-project-cache")
    assert yarn is not None and "cache" in yarn.aliases and yarn.scope == "project_artifact"

    hit = db.resolve(key="electron:Claude", folder_name="Cache",
                     path="/home/u/.config/Claude/Cache", scope="app_caches")
    assert hit is None or hit.scope == "app_caches", (
        f"app_caches row resolved to {hit.key!r} (scope {hit.scope!r})"
    )


def test_the_alias_still_resolves_inside_its_own_scope():
    hit = database().resolve(folder_name="cache", scope="project_artifact")
    assert hit is not None and hit.key == "yarn-project-cache"


def test_electron_rows_fall_back_to_the_electron_explanation(make_tree):
    from purgelinux.scanners.base import build_item

    path = make_tree(".config/SomeElectronApp/Cache/blob.bin", "x" * 512)
    item = build_item(
        str(pathlib.Path(path).parent),
        source="app_caches",
        definition_key="electron:SomeElectronApp",
        scope="app_caches",
        fallback_key="electron-app-cache",
    )
    assert item is not None
    assert item.explanation == database().by_key("electron-app-cache").explanation
