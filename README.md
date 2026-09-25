# Purge for Ubuntu

**Free up disk space, safely.**

A Linux [Purge] the macOS disk cleaner.
-find the cache and build output your machine collects on its own, label what
is safe, clear it in one click — rebuilt for Ubuntu's paths, packaging and tooling.

Written in Python with GTK4 and libadwaita. Ships as a single `.deb`.

---

## What it finds

**App Caches** — `~/.cache`, Flatpak (`~/.var/app/*/cache`) and Snap (`~/snap/*/common/.cache`)
app caches, thumbnails, font and shader caches, and browser caches for Chrome, Chromium,
Brave, Edge, Vivaldi, Opera, Firefox and Thunderbird — in **deb, snap and flatpak**
packagings. Ubuntu ships Firefox as a snap by default, so that path is the one that
usually has the data.

**Dev Tools** — global caches for npm, pnpm, Yarn, Bun, Deno, pip, uv, Poetry, conda,
cargo, Go, Gradle, Maven, Coursier, Composer, NuGet, Bazel, ccache, Playwright, Docker
(read-only), VS Code, Cursor and JetBrains — plus the ones that actually take the space on
a mobile dev machine: the **Flutter SDK cache**, `~/.pub-cache`, **Gradle** caches and
distributions, Android **system images**, **NDK** versions, old platforms and build-tools,
`~/.konan` and downloaded JDKs.

**Projects** — per-project build output, found only next to the file that identifies the
project: `node_modules` beside a `package.json`, `target` beside a `Cargo.toml`, Flutter's
`build`/`.dart_tool`/`android/.gradle` beside a `pubspec.yaml`, and so on. A lone folder
called `build` somewhere in Documents is never touched.

**Large Files** — your Documents, Desktop, Downloads, Videos, Music and Pictures, with
size and category filters, search, byte-identical duplicate detection, and local AI models
from Ollama, LM Studio, GPT4All and Hugging Face.

**System** — APT package archives and lists, unused packages, old kernels, leftover
package configs, the systemd journal, old snap revisions, crash reports and rotated logs.

## How it decides what is safe

Two tiers, and nothing else is ever shown:

| Label | Meaning |
|---|---|
| **Safe to Clean** | A download cache or build artifact the next build re-fetches or regenerates transparently |
| **Check First** | SDK payloads, toolchains and IDE indexes — rebuildable, but the cost is a long re-download or a re-index you will notice |

Anything the app cannot identify is left out of the list entirely.

**Deletion is allowlist-only.** Every removal — manual, one-click or scheduled — goes
through `DeletionSafetyPolicy.evaluate()` first. Your SSH keys, GnuPG, password store,
keyrings, browser cookies, logins, history, local storage and Firefox profiles are on a
permanent never-delete list, and no scanner or catalog entry can override it.

**Your files go to the Trash.** Personal files and duplicates move to the XDG trash and
can be restored from Files. Caches are removed outright, because trashing them would not
free any space — every confirmation screen says which is which.

**Root is asked for exactly one thing.** System cleanup runs a small helper through
`pkexec`, which accepts a fixed set of verbs and no file paths at all. One password prompt
covers a whole clean.

Nothing leaves your machine. No network access, no telemetry, no account.

---

## Install

### From the built package

```bash
sudo apt install ./purge-linux_1.0.0_all.deb
```

### Build it yourself

```bash
git clone https://github.com/tahidur-rahman0/ubuntu_cleaner.git && cd ubuntu_cleaner
make install-deps      # build + runtime dependencies from the Ubuntu archive
make deb               # produces ../purge-linux_1.0.0_all.deb
sudo apt install ../purge-linux_1.0.0_all.deb
```

Then launch **Purge** from the app grid, or run `purge-linux`.

**Supported:** Ubuntu 24.04 LTS, 25.10 and 26.04 LTS. The floor is 24.04 because the UI
uses `Adw.NavigationSplitView` and `Adw.Dialog`, which need libadwaita 1.5.

## Try it without deleting anything

```bash
purge-linux --dry-run
```

Scans everything and prints what a safe clean *would* remove, touching nothing. The same
brake works on the GUI:

```bash
PURGE_DRY_RUN=1 purge-linux
```

## Development

```bash
make run     # launch from the checkout
make test    # 166 tests, none of which touch your real home directory
make dry-run
```

### Layout

```
purgelinux/
  safety/      policy.py — the deletion gate; tiers.py; explanations.py
  scanners/    app_caches, dev_tools, projects, large_files, duplicates, ai_models, system
  services/    deleter.py (the only code that removes anything), trash.py, store.py, …
  ui/          GTK4 / libadwaita
  helper/      purge-system-helper — the only code that runs as root
data/
  explanations.json   what every row means, in plain English
  dev_catalog.json    the dev tool and project artifact catalog
```

### Adding a tool or a cache

Both catalogs are data. Add the paths to `data/dev_catalog.json` and an entry to
`data/explanations.json` with the same `key`. No code changes, and `make test` fails if a
catalog key has no explanation.

### Ubuntu 26.04 notes

Three rules this codebase follows deliberately, because 26.04 changed the ground:

- **Never parse apt CLI output.** apt 3.x reworked it. Package state comes from the
  `python3-apt` bindings.
- **Never shell out to `du`, `df`, `stat` or `find`.** Ubuntu ships uutils coreutils now.
  Sizes come from `os.scandir` and `os.statvfs`.
- **Depend on `polkitd | policykit-1` and `pkexec | policykit-1`**, since `policykit-1` is
  now a transitional package.

## Differences from macOS Purge

| Upstream | Here |
|---|---|
| Everything moves to Trash | Personal files to Trash; caches removed outright, labelled per row |
| App Uninstaller | Covered by apt/snap — the app finds the *leftovers* instead |
| iOS Simulators, Xcode, Homebrew | No Linux counterpart; dropped rather than faked |
| Sparkle in-app updates | `apt` handles updates |
| Full Disk Access gate | No such permission on Linux |
| Menu-bar companion | Not in 1.0 |
| — | System tab: APT, journald, snap, kernels, crash reports |

## License

MIT.
