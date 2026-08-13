# chrome-multi-session — GUI

A desktop front-end for `session_launcher.py`: environments, credentials, every
command the launcher accepts, and a live view of what a run is doing.

```bash
python3 bootstrap.py          # creates gui/.venv, installs PySide6, starts the app
```

That is the whole install. Linux, macOS and Windows all take the same line.

---

## What it is (and deliberately is not)

The GUI **never imports the core**. It spawns `session_launcher.py` through an
interpreter you configure, and learns everything else from the launcher itself:

| It needs to know | Where it gets it |
| --- | --- |
| what environments, users, scenarios and extensions exist | `--describe` (JSON) |
| what is happening right now | `--events=-` (JSONL on stdout) |
| what the run is saying | the launcher's stderr, the normal log |
| what to change about users | `users.json`, edited in place |

Two consequences worth knowing:

* **The environments stay separate.** The GUI's venv needs PySide6 and nothing
  else; the core's needs playwright and cryptography. On Windows they are often
  not even the same Python. Settings → *Interpreter* is the join.
* **The CLI and the GUI can never disagree.** There is no second copy of the
  config format, the scenario list or the flag rules. A flag added to the core
  shows up here after one line in `cms_gui/commands.py` - and a test fails if
  you forget it.

## The pages

**Environments** — the environments `--env` can select, derived from the
distinct `env` values in `users.json`, with their origin and user count. URL
overrides and default `--flows-dir` / `--reports-dir` / `--sessions-dir` are the
GUI's own (the core has no place to keep them).

**Credentials** — a table editor over `users.json`. Passwords are masked with a
per-row reveal. Validation mirrors the launcher exactly: `class`/`login`/
`password` required, `env`+`login` unique (that pair names the profile folder),
`tag:` not `tags:`. Saving is atomic and keeps one `.bak`.

**Command** — every flag, grouped the way `--help` groups them. The
flow-execution and report flags stay disabled until `--run-tests` is set,
because the launcher rejects them without it, so an invalid line cannot be
built. There is a live, copyable preview of the exact command.

**Run** — one panel per window: state (launching → attached → running → pass /
fail), the step tree (the very tree the in-page HUD draws - it arrives in the
`flow.start` event), progress and the run summary.

**Log** — the launcher's stderr, level-coloured, filterable by level, by session
and by text. The `[session]` prefixes the core adds during parallel runs are
what the session filter uses.

**Artifacts** — the run's `reports/<timestamp>/` tree as it fills in, with
inline previews for JSON, text and screenshots.

## Requirements

* Python 3.9+ with `venv` (Debian/Ubuntu: `sudo apt install python3-venv`).
* A working core checkout. `gui/` living inside it is detected automatically,
  including its `.venv`; otherwise point Settings at `session_launcher.py`.

## Development

```bash
python3 bootstrap.py --setup      # environment only, no launch
python3 bootstrap.py --upgrade    # re-install dependencies after editing requirements.txt
.venv/bin/python -m cms_gui       # run from source
.venv/bin/python -m pytest -q     # tests (headless, no display needed)
```

`tests/test_commands.py` runs the real launcher's `--help` and fails if the core
has a flag the catalogue does not offer, or the catalogue offers one the core
does not accept. It skips when no core checkout is present.

## Look

The interface follows the *Industry* design system exported from the design
prototype: a light blueprint surface, square corners, hairline dividers, one
slate-blue accent, condensed headings and monospace for machine text. All of it
lives in `cms_gui/theme.py` - retune there, not in the pages.

The design asks for Barlow, Barlow Condensed and JetBrains Mono. They are web
fonts, so each is declared as a stack that falls through to whatever is
installed. To get the intended type exactly, drop the `.ttf` files into
`cms_gui/assets/fonts/` - they are picked up at startup.

## Packaging

Not yet: this ships as source plus a bootstrap. PyInstaller specs per OS are the
planned next step, and want the UI to settle first.
