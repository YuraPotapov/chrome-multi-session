# Packaging

Builds an installable Chrome Multi Session for someone who has no Python, no pip,
no git and no interest in any of them. They get one file, install it, and find the
app in their launcher.

```
./packaging/build_deb.sh              # -> installers/linux/<version>/
./packaging/build_deb.sh --keep-venv  # reuse the build venv; much faster
```

Needed on the **build** machine: `python3` with `venv`, `dpkg-deb`, `fakeroot`.
Needed on the **target** machine: nothing but Ubuntu 22.04 and Google Chrome.

## What comes out

```
installers/linux/0.4.0/
  chrome-multi-session_0.4.0_amd64.deb   the canonical Debian name
  chrome_session_amd64.deb               the same file, under the short name
  SHA256SUMS
```

`installers/<platform>/<version>/` is versioned so several releases can sit side by
side. The `.deb` files themselves are git-ignored — they are ~96 MB — but the
directories and their `SHA256SUMS` are committed, so the history records what was
released and with which checksum.

## Installing

```bash
sudo apt install ./chrome_session_amd64.deb
```

`apt`, not `dpkg -i`: the package depends on the X, GL and xkb libraries that Qt
loads at runtime, and apt is what resolves them. `sudo dpkg -i chrome_session_amd64.deb`
works too on a normal desktop, where those libraries are already present.

Then: **Chrome Multi Session** in the applications menu, or `chrome-multi-session`
in a terminal for the CLI.

## What gets installed where

| Path | What |
|---|---|
| `/opt/chrome-multi-session/core/` | the launcher, frozen (Python, cryptography, pyyaml, playwright + its Node driver, `flows/`, `extensions/`, `hud.js`) |
| `/opt/chrome-multi-session/gui/` | the PySide6 front-end, frozen |
| `/usr/bin/chrome-multi-session{,-gui}` | wrappers onto the two bundles |
| `/usr/share/applications/…desktop` | the launcher entry |
| `~/ChromeMultiSession/` | **the user's**: `users.json`, `user_sessions/`, `reports/` |
| `~/.local/share/chrome-multi-session/gui/` | the GUI's run history and saved configurations |

The split is the point. Everything under `/opt` is replaced wholesale on upgrade;
nothing under `~` is ever touched by dpkg. `runtime_paths.py` is what draws the
line, and `tests/test_runtime_paths.py` is what keeps it drawn.

## Upgrading

```bash
sudo apt install ./chrome_session_amd64.deb    # the new one, over the old one
```

No uninstall, no reinstalling dependencies, no recreating configuration. Sessions,
credentials, reports and GUI history all survive, because dpkg never created them.

## Design notes

**Two executables, not one.** The GUI spawns the core as a subprocess and parses
its `--describe` / `--events` output; that boundary is the architecture, not an
accident, so packaging keeps it. `cms_gui.core.frozen_core()` finds the core
executable next to the GUI's, and the `.deb` wrapper also names it explicitly in
`CMS_CORE_EXE`.

**onedir, not onefile.** The GUI spawns the core for every `--describe` and every
run. A onefile build would unpack a 200 MB archive into `/tmp` each time.

**No bundled Chromium.** The adapter only ever `connect_over_cdp`s to the Chrome
already on the machine, so `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` is set for the
build and ~400 MB never gets downloaded. Playwright's **Node driver** is a
different matter and *is* bundled — `connect_over_cdp` goes through it — at
`_internal/playwright/driver/`, which is the one path
`playwright._impl._driver.compute_driver_executable()` will look in.

**`LD_LIBRARY_PATH`.** PyInstaller points it at the bundle, and children inherit
it. Chrome started that way loads our `libssl` and dies, so every subprocess that
runs a foreign binary goes through `runtime_paths.clean_subprocess_env()`.

**Chrome is a Suggests, not a Recommends.** apt installs Recommends by default,
and on Ubuntu 22.04 `chromium-browser` is a 2 KB shim that only redirects to a
snap — installing it would put something on `PATH` that looks like a browser and
launches nothing. So the package suggests `google-chrome-stable` and says nothing
else; `find_chrome()` prefers a binary that actually answers `--version`, and both
`postinst` and the GUI's first `--describe` tell the user how to install one.

**Sizes.** ~170 MB per bundle unpacked, ~96 MB compressed. Qt and the Node driver
are the whole of it; the exclusion list in `gui.spec` already drops WebEngine, QML,
3D and multimedia.

## Building for Windows

Not built here — PyInstaller does not cross-compile, and this is a Linux
workstation. The runtime work Windows needs is already done and tested:
`runtime_paths` resolves `%USERPROFILE%\ChromeMultiSession` and reads `$CMS_HOME`,
`find_chrome()` reads the `App Paths` registry key and probes Program Files,
`session_dir_for()` produces NTFS-legal profile names, `seed_password()` steps
aside where Chrome's password store cannot be written from outside, and
`cms_gui.core` already handles a `.exe` core.

What remains is packaging only:

1. `packaging/pyinstaller/{core,gui}-win.spec` — the same two specs plus
   `icon=` from `python -m cms_gui.icon` (it already writes a multi-size `.ico`).
2. `packaging/windows/chrome-multi-session.iss` — Inno Setup, with a constant
   `AppId` GUID and `UsePreviousAppDir=yes` so a new version installs over the old
   one; wizard step 1 a project-directory picker that writes `data_dir` into
   `<InstallDir>\cms.ini` (add that as the second source in
   `runtime_paths.user_data_root`, before the `%USERPROFILE%` default); step 2 the
   Desktop-shortcut task; and a `[Code]` check for the Chrome registry key.
3. `packaging/windows/build.ps1`, run on a machine with Python 3.11+ and Inno
   Setup 6 — or a `windows-latest` job that calls it.

Output belongs in `installers/windows/<version>/`.
