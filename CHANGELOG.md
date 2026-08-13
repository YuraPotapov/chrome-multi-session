# Changelog

All notable changes to this project are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**What the version is a promise about** — see *Compatibility* in the README. In short,
the public API is: the CLI flags, the `users.json` schema, the YAML flow format, the
report artifacts, and the on-disk profile layout `<env>-<login>`. That last one matters
more than it looks: changing it orphans real logged-in sessions on every user's machine,
so it is a MAJOR change even though no code signature moved.

While at `0.x`, breaking changes may land in a MINOR bump. `1.0.0` will be tagged once
the surface above is settled — deliberately *after* the planned work to make the engine
app-agnostic, since that will break things on purpose.

## [Unreleased]

### Added
- **A desktop GUI** in `gui/` (PySide6, its own virtualenv, `python3 gui/bootstrap.py`):
  environments, a `users.json` editor, a builder covering every launcher flag, and a
  live run view — step tree, log stream, session lifecycle and artifacts. It never
  imports the core; it spawns `session_launcher.py` through a configured interpreter,
  so the two environments stay independent and the launcher remains the single source
  of truth.
- `--events=-|FILE` — a structured JSONL event stream for programs driving the
  launcher: windows launched, CDP attached, every step, artifacts written, run summary,
  windows exited. `-` writes to stdout, which stays free because log records go to
  stderr. `engine/events.py` also provides the observer that feeds it, fanned out
  alongside the in-page HUD by `engine.events.Tee`.
- `--describe` — the whole inventory as JSON on stdout: environments, users (with
  `has_password`, never the password), scenarios with tags and whether
  `--run-tests=all` would run them, extensions, and the values each flag accepts. A
  broken config degrades to `warnings` inside the payload rather than a plain-text
  exit, so a caller always gets something it can render.
- `--extensions=NAME[,NAME...]` — install Chrome Web Store extensions into every
  profile. Entries may be a known name (`odoo_debug`), a raw 32-character store id,
  or `name=id`, so a fork can install anything without editing the code.
  `--extensions=list` prints what is known and how to install anything else.
- Unpacked extensions can be vendored in `extensions/<name>/` and installed by
  directory name with **no network access**, editable in place (the source is
  re-copied into each profile on every launch). A local directory takes precedence
  over the same name in the Web Store table, so a store extension can be pinned or
  patched without renaming the commands that install it.
- `--flows-dir=DIR` and `--reports-dir=DIR`, so the scenarios can live in their own
  repository, separate from the engine.
- `--version` / `-V`.
- `users.example.json` — the config shape, with placeholder credentials.
- `pyproject.toml`: installable, with a `chrome-multi-session` console entry point and
  pinned upper bounds on dependencies.
- The engine's own fixture flows under `tests/fixtures/flows/`, so the compiler and
  loader tests no longer assert on any particular app's scenarios. One integration test
  compiles the real tree when one is present, and skips when it is not.

### Changed
- The auto-login extension's source moved out of a Python string literal into
  `extensions/_autologin/` as ordinary editable JS. Credentials and the login-form
  selectors are generated per profile into a `config.js` that the source reads, so
  nothing secret is in the checked-in files and the selectors are no longer hardcoded
  in JavaScript.

### Fixed
- `--init-users-json` wrote a config that then refused to load: it emitted
  `"tests": []`, and an empty list is rejected. The template is now generated with
  `json.dumps` (it had also been hand-quoted into invalid JSON) and omits the key
  entirely. A test now scaffolds a config and loads it.

### Changed
- **BREAKING**: every usable extension vendored in `extensions/` is installed by
  default, and a broken one is skipped with a warning instead of stopping the launch.
  `--extensions` overrides the default entirely — `none` installs nothing, `all` is
  the default stated explicitly. Previously the Odoo Debug extension was downloaded
  from the Web Store on first run unless `--no-odoo-debug` was passed; nothing is
  downloaded now unless a Web Store name or id is named explicitly.
- `--odoo-debug` / `--no-odoo-debug` are deprecated. Both still work, with a one-time
  notice, and will be removed in the next MAJOR.

### Removed
- The empty `extensions/` directory.

## [0.4.0]

### Added
- `--jobs=N|all`: drive several windows at once instead of one after another. Extras
  queue and start as a slot frees. Scenarios inside a window still run in order.
- `--run-tests=config`: each user runs its **own** `run-tests` field from `users.json`,
  so one command covers a whole role matrix.
- `--env=NAME`: select an environment by short name, matched against the config's `env`
  field, and supply that environment's URL — making `--url` optional.
- `select` action, for native `<select>` elements. Clicking an `<option>` silently does
  nothing, so this needed its own action rather than a selector change.
- `highlight` overlay component: flashes a box over each clicked / typed / pressed
  element. A bare `press` marks the focused element, which is otherwise invisible.
- The overlay tree now shows **every** scenario the window will run, keeping finished and
  failed ones on screen; the running one is expanded, the rest collapsed.

### Changed
- **BREAKING**: `--filter-prefix` removed; use `--env`.
- **BREAKING**: ad-hoc `--user` runs are keyed by environment + login rather than by
  login alone, so they no longer share one profile across environments — and they now
  share the profile a config-driven run uses. Old bare-login profile folders are unused.
- The `users.json` field `prefix` is now `env`. The old name is still read, with a
  one-time notice: accept the old form, warn once, remove in the next MAJOR.

### Fixed
- An empty `--filter-users=` / `--filter-prefix=` silently meant "all", so an unset shell
  variable launched every user in every environment. Empty values are now an error.
- Any unrecognised `-`-prefixed argument was treated as a positional URL, so a bare
  `--run-tests` launched Chrome on the literal string `--run-tests` and ran nothing.
- `--run-tests=config` joined each user's scenario ids into one string, and a string is a
  single id — the run failed to compile.
- The overlay's log bridge sat on the process-wide logger, so with several windows every
  HUD showed every window's logs, and one thread could drive another thread's Playwright
  page.
- `DevToolsActivePort` was never cleared, so a killed window left a stale port that the
  engine would attach to — or, if the OS had reassigned it, another window's browser.
