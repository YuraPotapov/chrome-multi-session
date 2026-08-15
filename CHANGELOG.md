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
- The recorder panel **edits its own steps**: delete one, move it up or down,
  or retarget it. A bad capture is obvious while the page is still on screen and
  much less so afterwards. Python owns the list, so the panel sends an intent and
  repaints from what comes back - it never edits its own copy, which is what
  keeps the two from disagreeing after a navigation.
- The panel **collapses to its header**, and fades to 35% while collapsed so it
  stops covering the app; hovering brings it back. The choice is remembered in
  `sessionStorage`, because a navigation replaces the whole renderer.

### Changed
- **The recorder shows itself.** A window launched with `--recorder` carries the
  panel from the moment it is attached - no right-click, no menu item, nothing to
  find. That is not capturing automatically: nothing becomes a step until Capture
  Step is pressed. It removes the bundled extension entirely, along with its
  `contextMenus` permission, its per-profile install and the DOM flag the two
  halves talked over.

### Removed
- `extensions/_recorder/`, which existed only to carry a right-click into the
  page.

## [0.7.0] - 2026-08-15

### Added
- **Checking which radio or checkbox is on.** None of the assertions answered
  "is this option selected?", which is most of what a permissions screen is. CSS
  already says it - `:checked` - so this needs no new step type, and the tree
  writes selectors that way by hand already
  (`flows/selectors.yaml`: `roles_wizard_agent_checked`). Picking a radio now
  offers *check it IS selected*, *check it is NOT selected* and *wait until it
  becomes selected*, and finds the input behind whatever you clicked: the label
  beside it, or the row around it - in Odoo the label is the input's sibling, not
  its parent. `data-value` joins the attributes synthesis looks for, because
  without it every radio in a group looks identical: they share their `name`.

### Fixed
- The recorder could produce `click: "a"` - every link on the page, recorded as a
  step. Synthesis gave up after four ancestors and returned whatever it had,
  which for a plain `<a>` in an unremarkable list is the tag on its own. It now
  walks further, skips ancestors that describe nothing, and falls back to
  `:nth-of-type()` rather than to something meaningless. When the result still
  matches more than one element the menu says so in warning colour, since a step
  like that acts on whichever element Playwright reaches first.

### Added
- **Continue a recording.** RUN ▾ → *With Recorder* works out for itself what to
  write to. With a scenario selected it asks - *Continue "x"* / *Start new* /
  *Cancel* - because appending to a scenario and replacing one look identical
  until it is too late. With nothing selected it asks nothing and records a new
  one. Continuing loads the steps the file already has, shows them in the panel,
  appends what you capture, and keeps the name, description and tags it carried,
  because those are edits somebody made on purpose. `--recorder=ID` does the same
  from a shell.
  What counts as selected: whatever is open in the Scenarios editor, or a single
  scenario chosen on Launch Sessions. Never one that ships with the application -
  it cannot be written back, so recording into it would collect steps and then
  throw them away.

## [0.6.1] - 2026-08-15

### Fixed
- **`fill` never saved a step.** It asked for the value with `window.prompt`, and
  the recorder is driven over CDP - where Playwright dismisses a page's dialogs
  by default. `prompt()` returned null, the step was dropped, and nothing said
  why. Values are asked for in the recorder's own panel now, so no dialog is
  involved; the same goes for `select` and `assert_text_contains`. The
  end-to-end test had missed it by stubbing `window.prompt`, which tested around
  the bug rather than through it - there is now a test that reads the source and
  refuses any dialog call.

### Added
- **Match by exact text** (`T` in the action menu), for picking one row out of a
  list by the data in it - a customer, a reference, a number. Still never the
  default and never for a UI label: those are translated and the step would
  break on the next environment. Data is not, which is why this is offered at
  all, and Playwright's `:text-is()` makes it exact.

## [0.6.0] - 2026-08-15

Scenarios stop being something you write in a text editor. There is a page for
them, a tree of your own to write them into, and a recorder that captures one
from a window you are already working in.

### Added
- **A Scenario Recorder.** RUN ▾ → *With Recorder* opens the windows as usual,
  with one addition: right-click any of them and there is **Start Scenarios**.
  From then on the page carries a recorder panel, and capture is explicit -
  moving the mouse, typing and every intermediate input event are ignored. Press
  **Capture Step**, hover until the element you want is outlined, click it, and
  choose from the actions that element can take. That is one step, and nothing
  else is. The action is performed as well as recorded, so the page advances
  exactly as the replayed flow will; assertions are recorded without acting.
  Finish writes an ordinary scenario, through the same validator everything else
  goes through, tagged `template` because a recording is a draft.
- The recorder works inside a dropdown, an autocomplete list or any other
  transient popup, which is where most of what is worth recording actually
  happens. Three things make that possible: **F2** arms it, so opening capture
  mode is not a click somewhere else; while armed, mousedown and pointerdown are
  taken away from the app as well as the click, so its own "close on outside
  press" never fires; and the action menu is chosen with **1-9 / ↑↓ + Enter**,
  because a click on the recorder's panel is an outside-click as far as the app
  is concerned. Clicking still works everywhere it is safe.
- The recorder prefers a name already in `selectors.yaml` over anything it could
  synthesize, so a recording reads like the flows beside it and follows the tree
  when that name is re-pointed. Failing that it uses a structural attribute -
  `data-menu-xmlid`, `name`, a stable id - and **never** a visible-text selector:
  the same app renders Ukrainian on one environment and English on another, so a
  selector keyed on a label is the one thing guaranteed to break.
- `--recorder[=ID]`, and a small bundled extension (`extensions/_recorder/`) that
  is what puts *Start Scenarios* in the context menu. It is installed only under
  `--recorder`, and so is the debug port the recorder attaches through - a plain
  launch is unchanged, and an unauthenticated port is not something to open by
  default.
- **A Scenarios page** in the GUI: every scenario and every block, a step editor
  and a YAML view of the same file, plus New, Duplicate, Import, Export, Delete
  and Revert. A step's target is an alias - another flow's id, or a name from
  selectors.yaml - so the page shows what each one resolves to and can follow it:
  `use: access.open_app` opens that block, a named target leads to the selector
  it stands for. `selectors.yaml` is editable too, with the shipped names listed
  read-only and "Add to my selectors" taking a copy to override.
- **A writable flows tree.** Flows now resolve against a search path: your own
  `~/ChromeMultiSession/flows` first, then the one that ships with the app. A
  scenario of yours shadows a bundled one with the same id and can still `use:`
  the shipped blocks without copying them; `selectors.yaml` is merged rather than
  replaced, so re-pointing one name after a UI change does not mean taking the
  whole file. `--flows-dir` still means exactly the directory it names - only the
  default is layered - and in a source checkout both trees are the same directory,
  so nothing about development changes.
- Core commands for scenario files, since the GUI depends on PySide6 and nothing
  else and cannot read or write YAML: `--flow-show`, `--flow-save --from=FILE`,
  `--flow-delete`, `--flow-import`, `--selectors-show`, `--selectors-save`. All
  answer with JSON and exit, like `--describe`, and nothing is written unless it
  compiles first.
- `--describe` now carries the blocks, the merged selector map, and per scenario
  which tree it came from and whether it can be edited - plus the step grammar
  itself, so an editor's action menu cannot drift from what the compiler accepts.

### Fixed
- `describe()` defaulted `flows_dir` to the bundled directory before the loader
  saw it, the same way the compiler and the runner did. That hid every scenario in
  the user's own tree and reported all 53 bundled ones as editable.

## [0.5.0] - 2026-08-14

The first release that installs. Everything below had been sitting in
`[Unreleased]` since 0.4.0; cutting it is what gives the `.deb` a number to carry,
and `cms_gui.version()` now reads that same number, so the GUI's About box, the
core's `--version` and the package all answer alike.

### Added
- **A standalone installer for Ubuntu 22.04** — `sudo apt install ./chrome_session_amd64.deb`
  and the app is in the applications menu, with no Python, pip, git or virtualenv on
  the machine. Both halves are frozen with PyInstaller (`packaging/pyinstaller/`) and
  wrapped in a `.deb` by `packaging/build_deb.sh`, which writes versioned output to
  `installers/linux/<version>/`. Playwright's Node driver is bundled — `connect_over_cdp`
  goes through it — but no Chromium is: the app still attaches to the Google Chrome
  already installed. See [packaging/README.md](packaging/README.md); Windows is
  planned there and the runtime work it needs is already in place.
- `runtime_paths.py` — one place that answers where things live, splitting the
  resources that ship with the app (`flows/`, `extensions/`, `hud.js`) from the data
  that belongs to the user (`users.json`, `user_sessions/`, `reports/`). Installed,
  the second half is `~/ChromeMultiSession`, so an upgrade cannot touch a session,
  a credential or a report. **In a source checkout both still resolve to the checkout,
  exactly as before** — nothing about working on the project changes.
- `--describe` now reports `chrome`: the browser the core found, its version, and a
  message saying how to install one when there is none. The GUI shows it once at
  startup, so a missing browser is a sentence rather than a failed launch.
- GUI: `cms_gui.version()`, resolving the release number the same three ways
  `session_launcher.version()` does — an installed distribution, the `VERSION` file a
  packaged build carries, then `pyproject.toml` in a checkout. About names both the
  GUI's version and the core's, and says which file the core one came from; the core
  is asked with `--version` when no `--describe` has succeeded yet.
- GUI: Launch Sessions marks unsaved work — both Save buttons turn red once the
  controls differ from the configuration they were opened from, and settle again if
  the edit is undone by hand.
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
- GUI: the tick boxes and radio dots are painted by a `QProxyStyle` rather than by the
  stylesheet, which can fill an indicator but cannot put a mark inside one — a ticked
  box used to be an empty accent square. This also reaches the check rows in the
  Accounts, Extensions and Scenarios lists, which the stylesheet never touched.
- GUI: the Sessions counter is a stepper built from real widgets, replacing Fusion's
  two stacked 7px arrows.
- `find_chrome()` prefers a candidate that answers `--version` over one that merely
  exists on `PATH`. Ubuntu 22.04's `chromium-browser` is a 2 KB shim that redirects to
  a snap: findable, executable, and not a browser. It also now looks in the Windows
  registry and Program Files, where Chrome is never on `PATH`.
- Chrome is started with a scrubbed `LD_LIBRARY_PATH` so that a packaged build's own
  bundled libraries are not forced on it.
- The auto-login extension's source moved out of a Python string literal into
  `extensions/_autologin/` as ordinary editable JS. Credentials and the login-form
  selectors are generated per profile into a `config.js` that the source reads, so
  nothing secret is in the checked-in files and the selectors are no longer hardcoded
  in JavaScript.

### Fixed
- Windows: profile folder names now drop the characters NTFS rejects, so an env like
  `localhost:8069` produces a usable directory. Linux and macOS names are unchanged —
  renaming them would orphan every existing logged-in session.
- Windows: `seed_password()` no longer writes a credential blob Chrome cannot decrypt
  there (its password store is AES-256-GCM under a DPAPI-wrapped key). It skips the
  step with a note; auto-login is unaffected, since that is the extension's job.
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
