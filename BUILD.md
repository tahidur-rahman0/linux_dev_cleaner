# Building and running Purge on Ubuntu

Everything you need on the Ubuntu laptop, start to finish. Nothing here assumes
you remember the Mac session.

**Status.** Verified on a Debian-family machine with GTK 4 and libadwaita 1.9:
166 tests pass, every module imports, `--dry-run` runs end to end against a real
home directory, and the GTK window opens, scans and renders its rows. Five bugs
that only a real run could surface were found and fixed that way, each with a
regression test: `tests/test_cache_grouping.py`, `tests/test_onboarding_layout.py`,
`tests/test_list_callbacks.py`, `tests/test_explanation_scope.py` and
`tests/test_selection_sync.py`.

`make deb` builds cleanly and the package was verified from an extracted copy
running at its installed paths: it picks up `/usr/share/purge-linux/*.json`, finds
the helper under `/usr/libexec`, and `--dry-run` and the window both work from the
packaged code. `apt-get install --simulate` resolves every dependency with nothing
else pulled in, and `lintian` reports only `no-manual-page` plus the deliberate
`policykit-1` alternation.

**Still unverified — section 3 onwards.** The package has not actually been
installed to `/`, so the app-grid entry, the privileged System tab (the only part
that asks for a password) and `apt remove` are untested. The
[Troubleshooting](#troubleshooting) section covers the likely failures.

---

## 1. Prerequisites

```bash
git clone https://github.com/tahidur-rahman0/ubuntu_cleaner.git
cd ubuntu_cleaner
make install-deps
```

That runs:

```bash
sudo apt install -y devscripts debhelper dh-python pybuild-plugin-pyproject \
    python3-all python3-setuptools python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil python3-apt python3-pytest
```

Needs Ubuntu **24.04 LTS or newer** (24.04, 25.10, 26.04). The floor is 24.04
because the UI uses `Adw.NavigationSplitView` and `Adw.Dialog`, which need
libadwaita 1.5. Check with `apt policy libadwaita-1-0`.

---

## 2. Build

```bash
make deb
```

which is just `dpkg-buildpackage -us -uc -b` — unsigned, binary-only.

**The package lands in the parent directory**, not in the repo:

```
../purge-linux_1.0.0_all.deb
```

`Architecture: all` because it is pure Python — the same file installs on 24.04,
25.10 and 26.04.

---

## 3. Install

```bash
sudo apt install ../purge-linux_1.0.0_all.deb
```

Use `apt`, not `dpkg -i`. apt resolves the runtime dependencies; dpkg just fails
if one is missing.

What lands where:

| Path | What |
|---|---|
| `/usr/bin/purge-linux` | the launcher command |
| `/usr/lib/python3/dist-packages/purgelinux/` | the application |
| `/usr/libexec/purge-linux/purge-system-helper` | the root helper (root:root 0755) |
| `/usr/share/polkit-1/actions/io.github.purgelinux.policy` | the pkexec rule |
| `/usr/share/purge-linux/` | `explanations.json`, `dev_catalog.json` |
| `/usr/share/applications/io.github.purgelinux.desktop` | app grid entry |
| `/usr/share/icons/hicolor/scalable/apps/` | the icon |

---

## 4. First run — dry run first

**Do this before letting it near real files:**

```bash
purge-linux --dry-run
```

Scans everything and prints what a clean *would* remove, touching nothing. Sanity
check a couple of the numbers by hand:

```bash
du -sh ~/.cache ~/.npm ~/.gradle ~/.pub-cache 2>/dev/null
```

The GUI has the same brake:

```bash
PURGE_DRY_RUN=1 purge-linux
```

Then launch **Purge** from the app grid, or just `purge-linux`.

---

## 5. Verification checklist

Work through this the first time. It is ordered so a failure stops you before
anything is deleted.

- [ ] `purge-linux --version` prints `purge-linux 1.0.0`
- [ ] `purge-linux --dry-run` completes and the sizes look plausible
- [ ] **Purge** appears in the app grid with its own icon (not a generic cog)
- [ ] The window opens and the sidebar shows six tabs
- [ ] A scan streams rows in and the window stays responsive
- [ ] Rows carry an explanation, a size, and a green or amber pill
- [ ] Chrome or Firefox appears as **one** row, not one per cache folder
- [ ] `Ctrl+R` scans; `Ctrl+1` / `Ctrl+2` / `Ctrl+3` switch filters
- [ ] Dev Tools finds your real caches — npm, pip, cargo, Gradle, pub-cache
- [ ] Large Files finds something in Videos or Downloads

Then a **real** clean, on a scratch tree rather than your actual files:

```bash
mkdir -p ~/purge-test/proj && cd ~/purge-test/proj
echo '{}' > package.json && echo '{}' > package-lock.json
mkdir -p node_modules && head -c 50M /dev/urandom > node_modules/blob.bin
mkdir -p ~/.cache/purge-test-junk && head -c 20M /dev/urandom > ~/.cache/purge-test-junk/blob.bin
head -c 30M /dev/urandom > ~/Videos/purge-test-clip.mp4
```

In Settings set **Consider projects stale after → Show all** and **Large file
threshold → 5 MB**, then scan. Check:

- [ ] `node_modules` and the junk cache are removed outright
- [ ] `purge-test-clip.mp4` goes to the **Trash** and is restorable in Files
- [ ] The confirmation sheet said which of those two would happen, per row
- [ ] `df -h ~` reflects the freed space

And the System tab, which is the only part that asks for a password:

- [ ] Select APT archives + System logs → **one** password prompt for the batch
- [ ] `journalctl --disk-usage` shrank
- [ ] `sudo tail /var/log/purge-linux.log` shows what root actually did

Finally:

- [ ] `sudo apt remove purge-linux` removes cleanly

---

## 6. Troubleshooting

### `dpkg-checkbuilddeps: Unmet build dependencies`
Run `make install-deps`. The error names exactly what is missing.

### `Unable to determine the build system` / pybuild errors
`pybuild-plugin-pyproject` is missing. The project has no `setup.py`, so pybuild
needs the PEP 517 plugin. It is in `Build-Depends`; `make install-deps` installs it.

### The window does not open, `ValueError: Namespace Adw not available`
`gir1.2-adw-1` is missing, or libadwaita is older than 1.5:
```bash
apt policy libadwaita-1-0 gir1.2-adw-1
```

### The app grid shows a generic icon
```bash
sudo gtk-update-icon-cache -f /usr/share/icons/hicolor
sudo update-desktop-database /usr/share/applications
```
Then log out and back in. GNOME caches aggressively.

### A GTK error on launch — `TypeError`, `AttributeError`, an unknown property
Get the real traceback:
```bash
purge-linux 2>&1 | tail -40
```
The UI is in `purgelinux/ui/`. Paste the traceback and it is usually a one-line fix.

### The System tab is empty or says the helper is not installed
```bash
ls -l /usr/libexec/purge-linux/purge-system-helper   # must be root:root, 0755
pkexec --version
```
Check the **About** tab — it states whether the helper was found.

### `apt.Cache()` errors, or apt rows missing
`python3-apt` is missing. It is a hard dependency, so this should not happen
from a proper `apt install`.

### Something got deleted that should not have
Please open an issue with the path. The safety policy is
`purgelinux/safety/policy.py` and is covered by `tests/test_safety_policy.py`;
a gap there is the most serious kind of bug this app can have.

---

## 7. Development

```bash
make run       # launch from the checkout, no install needed
make test      # 166 tests — none touch your real home directory
make dry-run
make lint      # desktop-file-validate + appstreamcli
make clean
```

### Layout

```
purgelinux/
  safety/      policy.py — the deletion gate; tiers.py; explanations.py
  scanners/    app_caches, dev_tools, projects, large_files, duplicates,
               ai_models, system
  services/    deleter.py (the only code that removes anything), trash.py,
               store.py, privileged.py, schedule.py, prefs.py, history.py
  ui/          GTK4 / libadwaita
  helper/      purge-system-helper — the only code that runs as root
data/
  explanations.json   what every row means, in plain English
  dev_catalog.json    dev tool and project artifact catalog
```

### Two rules the codebase keeps

1. Every deletion goes through `DeletionSafetyPolicy.evaluate()`. Nothing outside
   `services/deleter.py` calls `os.remove`, `os.unlink` or `shutil.rmtree`.
2. The root helper never receives a path from the GUI — only verbs from a fixed set.

### Adding a tool or a cache

Both catalogs are data. Add the paths to `data/dev_catalog.json` and an entry with
the same `key` to `data/explanations.json`. No code changes. `make test` fails if a
catalog key has no explanation.

Installed copies live in `/usr/share/purge-linux/`, so you can edit those directly
to try something without rebuilding.

### Ubuntu 26.04 rules this code follows deliberately

- **Never parse apt CLI output** — apt 3.x reworked it. Use the `python3-apt`
  bindings (`apt.Cache()`).
- **Never shell out to `du`, `df`, `stat`, `find`** — Ubuntu ships uutils
  coreutils. Use `os.scandir` and `os.statvfs`.
- **Depend on `polkitd | policykit-1` and `pkexec | policykit-1`** — `policykit-1`
  is now a transitional package.

---

## 8. Uninstall

```bash
sudo apt remove purge-linux            # keeps your settings and history
sudo apt purge purge-linux             # also removes /var/log/purge-linux.log
rm -rf ~/.config/purge-linux           # settings, history, excluded paths
```

If you enabled scheduled cleaning, turn it off in Settings *before* uninstalling,
or clear it by hand:

```bash
systemctl --user disable --now purge-linux-clean.timer
rm -f ~/.config/systemd/user/purge-linux-clean.{service,timer}
systemctl --user daemon-reload
```
