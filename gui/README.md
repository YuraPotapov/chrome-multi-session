# chrome-multi-session — GUI

A desktop front-end for `session_launcher.py`, at two levels: **Launch Sessions**
describes a run in the words of the job, **Command** exposes every flag the
launcher accepts. Both feed the same live view of what a run is doing, and both
land in the same reusable history.

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
| what has already been run | its own `history.json`, the one thing the core does not track |

Two consequences worth knowing:

* **The environments stay separate.** The GUI's venv needs PySide6 and nothing
  else; the core's needs playwright and cryptography. On Windows they are often
  not even the same Python. Settings → *Interpreter* is the join.
* **The CLI and the GUI can never disagree.** There is no second copy of the
  config format, the scenario list or the flag rules. A flag added to the core
  shows up here after one line in `cms_gui/commands.py` - and a test fails if
  you forget it. Launch Sessions does not get a command builder of its own
  either: `cms_gui/launch.py` turns a configuration into the same state dict the
  Command form produces, and `commands.build_argv` does the rest.

## Two levels, one toggle

**Developer mode**, top right next to Settings, switches between them without a
restart:

|  | regular | developer |
| --- | --- | --- |
| Launch Sessions | yes | yes |
| Command | hidden | yes |
| *Copy command* in the toolbar | hidden | yes |
| the generated command line under the Launch summary | hidden | yes |

Launch Sessions is never hidden: it is the primary interface in one mode and
still the quicker one in the other. `Ctrl+Shift+D` toggles; the toolbar's RUN
acts on whichever of the two launching pages you were last in.

## The pages

**Environments** — the environments `--env` can select, derived from the
distinct `env` values in `users.json`, with their origin and user count. URL
overrides and default `--flows-dir` / `--reports-dir` / `--sessions-dir` are the
GUI's own (the core has no place to keep them).

**Credentials** — a table editor over `users.json`. Passwords are masked with a
per-row reveal. Validation mirrors the launcher exactly: `class`/`login`/
`password` required, `env`+`login` unique (that pair names the profile folder),
`tag:` not `tags:`. Saving is atomic and keeps one `.bak`.

**Log sources** — a table editor over `logsources.json`: the servers the backend
logs live on, and which log belongs to which environment. Two tables rather than
one list, because the file has two levels - a *connection* is where to run a
reader, a *log* is what to read there - and one connection serves every log on a
machine. Validation mirrors `engine.serverlog` exactly, so the editor cannot write
a file the next launch would refuse; a file that could not be *read* disables Save
entirely, because an empty editor over a broken file validates perfectly and one
click would replace its contents. **Open Tail** and **Open Full** read the log through the core
(`--server-log-show`) and put it on screen - the GUI has no ssh of its own - which
is how an unknown host key, a stopped container or simply the wrong path gets found
before a run depends on it. The viewer filters, saves, and is not modal, so two
logs can be compared side by side.

**Command** — every flag, grouped the way `--help` groups them. The
flow-execution and report flags stay disabled until `--run-tests` is set,
because the launcher rejects them without it, so an invalid line cannot be
built. There is a live, copyable preview of the exact command. Developer mode
only.

**Launch Sessions** — the same launcher without the launcher: which environment,
whose accounts, how many at once, which scenarios, how much to record. It reads
back as a sentence, refuses to run a configuration with a problem in it, and
saves named configurations you can duplicate, rename and delete. Rules a user
could not guess are applied for them - asking for screenshots adds the `screen`
artifact the core needs before it will take `--report-screen` - and the flow and
report options simply do not apply when nothing is scripted, which the page says
out loud rather than dropping them quietly. Anything genuinely technical (start
address, profile prefix, log level, the three folders, the execution overlay)
is folded away under *Advanced*.

**Run** — one panel per window: state (launching → attached → running → pass /
fail), the step tree (the very tree the in-page HUD draws - it arrives in the
`flow.start` event), progress and the run summary. With `--server-log` on, each
panel also folds out that window's **Server log**: the backend lines written since
*that* window opened, level-coloured, filterable when several logs are streaming,
and saveable - which is the only way to keep them from a plain launch, where there
is no report directory to write into.

**Separate Window** moves that log into a full-size window of its own, with a
search, a level filter that means "this level and worse", and a Follow
switch for when you want to read something that has already scrolled past. Several
sessions can be open at once, which is the point of a per-session log when ten
windows are running as ten roles. While a window has the lines, the strip stops
drawing them and says so with a pulse instead - one copy of a few hundred lines is
enough, and a blank space there would read as "the log stopped".

Lines are coloured by severity - debug recedes into grey, info is green, warnings
amber, errors red, and a critical is the same red with weight and a wash behind it,
because a critical is not a different *kind* of thing from an error but the same
thing gone further. The palette is read from the theme as each line is painted, so
it follows dark mode instead of freezing into whichever theme was on at startup.

**Log** — the launcher's stderr, level-coloured, filterable by level, by session
and by text. The `[session]` prefixes the core adds during parallel runs are what
the session filter uses. *Showing* picks between the live run and any run's
archived log, newest first; an archive is parsed back into the same records a live
line produces, so the filters and the colouring work on it identically. Live lines
keep arriving while you read an old log, and starting a run brings the page back to
the live one.

**Artifacts** — a run's `reports/<timestamp>/` tree, with inline previews for JSON,
text and screenshots, and each file's write time. During a run it fills in as the
files land; between runs *Run* picks any recorded run whose reports still exist,
newest first, or any folder you point it at. It reopens on whichever run you were
last looking at.

**History** — every run either page started, newest first. An entry is not a log
line: it carries the configuration that caused the run, so the useful verb is
*run again* - straight from the table, or after opening it back up in the page it
came from and changing one thing. A Launch Sessions entry restores its
configuration; a Command entry restores its form (and turns developer mode on, so
there is somewhere to put it). Each entry also links the reports the run wrote and
a copy of its log, archived at the end of the run because the next run clears the
Log page.

## Where the GUI keeps its own things

| | |
| --- | --- |
| paths, the last form state, the current mode, what Artifacts and Log were last showing | `QSettings` (`~/.config/chrome-multi-session/gui.conf` on Linux) |
| history, saved configurations, archived logs | `~/.local/share/chrome-multi-session/gui/` |

Both are plain files, and the history is readable JSON. Nothing the launcher owns
(users, environments, scenarios) is copied into either.

The run record is what makes the observing pages persistent: Artifacts and Log
have no store of their own, they read it for the list of runs worth offering. So
deleting a history entry takes its log with it, and a report folder deleted on
disk simply stops being offered.

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
`tests/test_launch.py` pins the translation the other way round: what each
user-facing answer becomes on the command line, and that the argv still comes out
of `commands.build_argv` rather than a second builder. The suite redirects
`XDG_CONFIG_HOME` and `XDG_DATA_HOME` to a temporary directory, so running it
never touches your own settings or history.

## Look

The interface follows the *Industry* design system exported from the design
prototype: a light blueprint surface, square corners, hairline dividers, one
slate-blue accent, condensed headings and monospace for machine text. All of it
lives in `cms_gui/theme.py` - retune there, not in the pages.

The design asks for Barlow, Barlow Condensed and JetBrains Mono. They are web
fonts, so each is declared as a stack that falls through to whatever is
installed. To get the intended type exactly, drop the `.ttf` files into
`cms_gui/assets/fonts/` - they are picked up at startup.

Because the type is whatever the machine actually has, nothing in the chrome
assumes a text width: the navigation rail measures its own labels and sizes
itself to the widest one, with 206px as a floor.

The icon is painted too, in `cms_gui/icon.py` - three offset windows in ascending
lightness on dark slate, which is the tool's one idea. Every size is rendered at
its own size rather than scaled from one bitmap, and below 24px the title bars and
hairlines drop out so the three lightness steps are left to do the work alone.
No binary asset, and the accent only has to change in `theme.py`.

## Packaging

Not yet: this ships as source plus a bootstrap. PyInstaller specs per OS are the
planned next step, and want the UI to settle first. The icon files those specs
will need can be produced on demand:

```bash
.venv/bin/python -m cms_gui.icon build/icons   # icon-16..256.png + icon.ico
```
