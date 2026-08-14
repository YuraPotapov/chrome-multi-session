# chrome-multi-session

Open one isolated Chrome window per test user, each auto-logged in, each with its
own persisted session and a single saved credential in Chrome's password manager.
Reusable for any multi-user login setup.

## How it works

- The user list lives in a JSON config file (`users.json` by default). Each entry
  has an `env` (which environment it belongs to), a `class` (window class / profile
  label), a `login` and its own `password`.
- Every user gets a separate `--user-data-dir` Chrome profile, so all users can be
  logged in at once without sharing cookies.
- A tiny generated extension fills and submits the login form per profile. It
  submits once per tab: if the credentials are rejected and the login page comes
  back, it stops instead of resubmitting in a loop (it logs a warning to the tab's
  console).
- Each profile's password manager is seeded with that user's single login/password
  (Chrome runs with `--password-store=basic` so the value is decryptable).
- Sessions persist between runs; each launch opens a single clean tab.

Profiles and generated extensions live under `user_sessions/` next to the
script by default (git-ignored — they hold cookies and passwords); override the
location with `--sessions-dir=DIR`.

## Config file

`users.json` (next to the script; override with `--config=FILE`) is a JSON array,
one object per user. Each has a `login`, a `class` (window class / profile label),
a `password`, and an optional `env`:

```json
[
  {"env": "localhost:8069", "class": "Admin", "login": "admin", "password": "admin"},
  {"env": "localhost:8069", "class": "User",  "login": "user",  "password": "user"}
]
```

Each user's profile folder is `<env>-<login>` (e.g. `localhost:8069-admin`),
so the **`env`+`login` pair must be unique**. `--user-session=PREFIX` overrides
the folder prefix for the whole run. Path separators in an `env` (e.g. a URL) are
flattened for the folder name.

> `env` used to be called `prefix`. The old name is still read, so an existing
> config keeps working; rename it when convenient.

### Predefined tests per user

An entry can carry the scenarios that user should run, so a role never has to be
paired with its test by hand:

```json
{"env": "localhost:8069", "class": "Agent", "login": "role_agent",
 "password": "CHANGE-ME", "run-tests": ["access_agent"]}
```

`--run-tests=config` then gives **each window its own list**, instead of replaying
every scenario against every window — so one command covers the whole matrix:

```bash
python3 session_launcher.py --env=dev --run-tests=config --execution-overlay=all
```

The value takes a JSON list or a comma-separated string, and entries may be
scenario ids or `tag:NAME` selectors (singular `tag:`, one tag per entry — `tags:`
is rejected with a hint). `tests` is accepted as a shorthand for `run-tests`.

Because the field lives per entry, each environment can name its own variant — the
`localhost` rows use `access_division`, while the `app-dev`/`app-stg` rows use
the `access_division_dev` twin. A user with no `run-tests` still launches, but runs
nothing (logged as skipped); if *nobody* selected has one, the run stops before any
window opens.

Note `tag:` is a **union**: `tag:manual` selects every manual scenario, not the
intersection with another tag. Naming a scenario by id runs it regardless of its
tags — `manual` only keeps it out of `--run-tests=all`.

### Multiple environments

Give the same user set a different `env` per environment (the same login can
repeat across environments — the folder stays unique):

```json
[
  {"env": "localhost:8069",              "class": "Agent", "login": "role_agent", "password": "CHANGE-ME"},
  {"env": "https://app-dev.example.com/", "class": "Agent", "login": "role_agent", "password": "CHANGE-ME"},
  {"env": "https://app-stg.example.com/", "class": "Agent", "login": "role_agent", "password": "CHANGE-ME"}
]
```

Then launch one environment at a time with `--env`, which matches by short name
and **also supplies that environment's URL**:

```bash
# just the dev environment's users
python3 session_launcher.py --env=app-dev
```

The name is the host's first label (`app-dev`, `app-stg`, `localhost`), and
any unambiguous shortening works — `--env=dev`, `--env=stg`, `--env=local`. A name
that matches nothing, or several environments, exits with the full list rather
than launching something unintended.

Omitting `--env` launches every environment at once, which is rarely what you
want against a single URL — the script warns when a run spans more than one.

Only pass `--user-session` *without* `--env` at your peril: it forces one folder
prefix onto every entry and collapses the environments together (the script errors
if that collides).

It holds logins and passwords, so it's **git-ignored** — it isn't committed. Scaffold
a starter copy with:

```bash
python3 session_launcher.py --init-users-json
```

This writes a one-line example `users.json` (only if one doesn't already exist);
edit it to add your users.

## Install (Ubuntu 22.04)

For using the app rather than working on it. Nothing needs to be installed first —
no Python, no pip, no git — because the Python runtime, PySide6 and Playwright all
ship inside the package. Google Chrome is the one thing that does not, and the app
says so on startup if it cannot find one.

```bash
sudo apt install ./chrome_session_amd64.deb
```

Then launch **Chrome Multi Session** from the applications menu, or run
`chrome-multi-session` for the CLI.

Your sessions, credentials and reports live in `~/ChromeMultiSession`, separate
from the installed program, so installing a newer version over the top keeps all
of them. Building the package, what lands where, and the Windows plan:
[packaging/README.md](packaging/README.md).

## Getting started (from source)

```bash
# 1. Chrome or Chromium must be on PATH
google-chrome --version

# 2. Environment + dependencies (Python 3.9+)
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt

# 3. Your user list. Copy the example, or scaffold a one-user starter:
cp users.example.json users.json          # then edit it
#   ...or: python3 session_launcher.py --init-users-json

# 4. Launch
python3 session_launcher.py --env=localhost
```

That opens one Chrome window per configured user, each signed in. `users.json` holds
passwords and is git-ignored.

To run flows as well, install the extras and point `--run-tests` at a scenario:

```bash
python3 -m pip install -r requirements-dev.txt      # playwright, pyyaml, pytest
python3 session_launcher.py --env=localhost --run-tests=my_scenario --execution-overlay=all
```

**Writing your own flows: [docs/flows.md](docs/flows.md).** The GUI's Scenarios
page does the same thing without a text editor - it lists every scenario and
block, shows what each named target resolves to, and writes into
`~/ChromeMultiSession/flows`, which is searched before the tree that ships with
the app.

## Running the tests

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/ -q
```

No browser is launched: the suite drives the engine against fake adapters and its own
fixture flows in `tests/fixtures/flows/`. One integration test compiles the real `flows/`
tree if there is one, and skips if there is not.

## The GUI

Everything below can also be driven from a desktop front-end that lives in
[gui/](gui/) — environments, a `users.json` editor, a builder for every flag on this
page, and a live view of a run (step tree, log, artifacts).

```bash
python3 gui/bootstrap.py     # creates gui/.venv, installs PySide6, starts the app
```

It is a *client* of this launcher, not a second implementation: it spawns
`session_launcher.py` through an interpreter you configure, asks `--describe` what
exists and follows `--events=-` for what happens. The two environments stay separate —
the GUI needs PySide6, the launcher needs playwright and cryptography. See
[gui/README.md](gui/README.md).

### `--describe` and `--events`

Both flags exist for programs rather than people, and are useful on their own:

```bash
# the whole inventory as JSON: environments, users (never passwords), scenarios
# with their tags, extensions, and the values each flag accepts
python3 session_launcher.py --describe

# a structured JSONL event per transition, on stdout - log records stay on stderr,
# so the two are clean, separate streams
python3 session_launcher.py --env=localhost --run-tests=smoke_odoo --events=- \
  1>events.jsonl 2>run.log
```

Event kinds: `launcher.start`, `window.launched`, `windows.ready`,
`session.attached` / `session.attach_failed`, `session.start`, `flow.start` (carrying
the same step tree the HUD draws), `step.start` / `step.end` / `step.retry`,
`flow.end`, `artifacts.written`, `run.dir`, `run.summary`, `window.exited`. Each line
has `ts`, a monotonic `seq`, `kind`, and — for anything inside a window — `session`.
`--events=PATH` appends to a file instead.

## Compatibility

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The
public API the version is a promise about:

1. **CLI flags** and their accepted values
2. **The `users.json` schema** — `env`, `class`, `login`, `password`, `run-tests`
3. **The YAML flow format** — see [docs/flows.md](docs/flows.md)
4. **The report artifacts** — `result.json` fields and the
   `reports/<run>/<session>/<scenario>/` layout
5. **The on-disk profile layout** — `<env>-<login>`. Changing it orphans real logged-in
   sessions on every user's machine, so it is a breaking change even though no signature
   moved.

Deprecations keep working for one MAJOR cycle with a one-time notice — as `prefix` →
`env` does today. While at `0.x`, breaking changes may land in a MINOR bump; see
[CHANGELOG.md](CHANGELOG.md).

## Requirements

- Google Chrome / Chromium on `PATH`
- Python 3.9+, `pip install -r requirements.txt` (the `cryptography` package)
- For `--run-tests` only: `playwright` and `pyyaml` (in `requirements-dev.txt`)

## Usage

```bash
python3 session_launcher.py --env=localhost
```

Runs in the foreground and holds every window — press **CTRL+C to close them all**
(logins persist for next time).

Options:

- `--env=NAME` — launch one environment, matched against the config's `env` field
  by short name (`--env=dev`). It also supplies that environment's URL, so `--url`
  is optional. Default: every environment.
- `--url=URL` — login URL (or pass it as a positional argument). Overrides the URL
  `--env` would supply.
- `--user-session=PREFIX` — prefix for this run's profile dir names, so separate
  sessions stay isolated (the per-user suffix is appended automatically).
- `--sessions-dir=DIR` — where to store profiles + generated extensions
  (default: `user_sessions/` next to the script).
- `--config=FILE` — path to the users JSON config (default: `users.json`).
- `--init-users-json` — write a starter `users.json` (at `--config`'s path if
  given) and exit; never overwrites an existing file.
- `--filter-users=LIST` — launch only a subset of the config, by login:
  `--filter-users=role_agent,role_manager`. Default is `--filter-users=all`
  (every user). Errors if a login isn't in the config, or if the value is empty.
- `--user=LOGIN` — launch a single user instead of the config list. Its password
  and window label come from the config; if the login isn't there, the
  environment's own shared password is used.
- `--password=PASS` — override the stored password. With `--user` it applies to
  that user (and needs no config entry at all); on its own it applies to every
  selected user.

**Everything `--env` provides is a default — any flag you pass explicitly wins.**

```bash
# one user, dev; password comes from users.json
python3 session_launcher.py --env=dev --filter-users=role_division

# a login that isn't in the config yet — borrows dev's shared password
python3 session_launcher.py --env=dev --user=role_agent3

# stale password in the config? override it for this run
python3 session_launcher.py --env=dev --user=role_agent3 --password=newpass

# a deep link, still using dev's users and profiles
python3 session_launcher.py --env=dev --url=https://dev.example.com/orders/12
```
- `--run-tests=LIST` — attach to each window over CDP, run scenarios, write reports.
  `LIST` is `all`, `config`, or a comma-separated list of scenario ids and
  `tag:NAME` selectors. `config` runs each user's **own** `run-tests` field from
  `users.json` (see below).
- `--detach` — fire-and-forget: leave windows running after the script exits.
- `--log-level=LEVEL` — `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `INFO`; also via
  the `OPEN_USERS_LOG_LEVEL` env var).
- `--execution-overlay=LIST` — show the in-page execution HUD while running flows
  (`--run-tests`). `LIST` is a comma-separated set of components or `all`:
  `tree`, `progress`, `status`, `logs`, `highlight` (`notifications` is accepted
  but not drawn yet). Only valid together with `--run-tests`.

  The tree shows **every scenario the window will run**, not just the current one:
  finished ones keep their result (✔/✖) and stay on screen, the running one is
  expanded, the rest sit collapsed on a single line. So when scenario 1 fails and
  scenario 2 starts, what happened before is still there to look at.

  `highlight` flashes a box over the element each acting step touches — `click`,
  `fill`, `select` and `press` — captioned with the action (`click`,
  `fill 07-2026-0454`, `press Enter`). A bare `press` has no selector, so it marks
  whatever currently has **focus**, which is otherwise invisible in a screenshot.
  Assertions are not marked, or the page would strobe. The box is drawn over the
  element rather than styling it, so the page's own CSS is never modified, and it
  fades after ~1.4 s.
- `--extensions=LIST` — which extensions to install into every profile.

  **By default — with no flag — every usable extension in `extensions/` is installed.**
  One that is broken, or that this Chrome will not accept, is skipped with a warning
  naming the reason; the launch is never stopped by it.

  ```bash
  python3 session_launcher.py --env=localhost                    # everything in extensions/
  python3 session_launcher.py --env=localhost --extensions=none  # nothing
  python3 session_launcher.py --env=localhost --extensions=all   # the default, explicitly
  python3 session_launcher.py --env=localhost --extensions=odoo_debug   # exactly this one
  ```

  `--extensions` always wins over the default, `none` included. `LIST` entries may be
  local directory names, Chrome Web Store names or ids, or `name=id`.

  **See what is available:**

  ```bash
  python3 session_launcher.py --extensions=list
  ```

  **Ship your own, with no network at all** — drop an unpacked extension in
  `extensions/<name>/` (it needs a `manifest.json`) and use its directory name:

  ```bash
  python3 session_launcher.py --env=localhost --extensions=my_helper
  ```

  Nothing is downloaded, the source is re-copied into each profile on every launch so
  edits take effect immediately, and a local directory **wins over the same name** in
  the Web Store table — which is how you pin or patch a store extension without
  renaming the commands that use it. See [extensions/README.md](extensions/README.md).

  **Install a store extension that isn't named yet** — no code change needed. The id is the last
  path segment of the Chrome Web Store URL
  (`https://chromewebstore.google.com/detail/<slug>/`**`hmdmhilocobgohohpdpolmibjklfgkbi`**):

  ```bash
  python3 session_launcher.py --env=localhost \
    --extensions=fmkadmapgofadopljbjfkapdkoienihi

  # or name it inline - the name is used for the cache file and the log lines
  python3 session_launcher.py --env=localhost \
    --extensions=react_devtools=fmkadmapgofadopljbjfkapdkoienihi
  ```

  To make a name permanent, add one line to `KNOWN_EXTENSIONS` in
  `session_launcher.py`. Unknown names fail fast and print the list above rather than
  silently installing nothing.

  Each CRX is fetched once from the Web Store and cached under the sessions dir, then
  planted into the profile directly (Chrome 137+ blocks `--load-extension`). A fetch
  failure warns and the launch continues without that extension.

  Replaces `--odoo-debug` / `--no-odoo-debug`, which still work with a deprecation
  notice. Note the default changed: the Odoo Debug extension used to be installed
  unless you opted out, and now nothing is installed unless you ask.

- `--jobs=N|all` — drive **N windows at once** instead of one after another
  (default `1`). With more windows than jobs the extras queue and start the moment
  a slot frees. Scenarios *inside* a window always run in order — one page cannot
  be driven twice — so this only removes the wait between windows, and no flow
  changes meaning. Console lines gain a `[session]` tag while running in parallel.
  Only valid together with `--run-tests`.

  ```bash
  # the whole dev matrix at once instead of role after role
  python3 session_launcher.py --env=dev --run-tests=config --jobs=all
  ```

  **Parallelise windows, not data.** Read-only access matrices are safe. The
  write-flows mutate shared records in the app under test — and a fixture scenario
  other flows depend on must run first — so leave those at the default `--jobs=1`.
- `--close-after` — when running flows (`--run-tests`), close the browser windows
  automatically once the run finishes (report and screenshots written). Without it
  the windows stay open after execution so you can inspect the final app state (and
  the Execution Overlay, if enabled); close them manually or with CTRL+C. The
  launcher still exits with the aggregate run result either way. Only valid together
  with `--run-tests`.
- `--report-level=LIST` — which report artifacts to generate: `console`, `dom`,
  `result`, `screen`, `url`. Only the listed artifacts are produced, on success and
  on failure alike. Omit it to keep the default set (see below).
- `--report-always` — also produce a full report after a **successful** run, not
  only on failure. With `--report-level` the selected artifacts are used; otherwise
  the full default set is produced.
- `--report-screen=LIST` — when screenshots are captured: `start` (before the first
  step), `each` (after every step that passes), `finish` (when the flow ends,
  success or failure). Combine freely, e.g. `start,each,finish`. Has effect only
  when `screen` is among the report artifacts. All three report flags require
  `--run-tests`.

### Configurable reports

By default (no `--report-*` flag) each scenario writes `result.json` on success and
the full diagnostic bundle on failure — unchanged from before:

```
report/                       report/                  # on failure
    result.json                   console.log
                                  dom.html
                                  result.json
                                  screenshot.png
                                  url.txt
```

The three flags configure this independently: `--report-level` selects **what** is
generated, `--report-always` selects **when** a report is generated, and
`--report-screen` selects **when** screenshots are captured. Screenshots are named
`screenshot_start.png`, `screenshot_001.png`, `screenshot_002.png`, …,
`screenshot_finish.png` by capture point.

```bash
# result.json + a before/after screenshot, on both success and failure:
python3 session_launcher.py --user=admin --password=admin \
  --url=http://localhost:8069 --run-tests=my_scenario \
  --report-level=result,screen --report-always --report-screen=start,finish
```

An unknown artifact name or screenshot mode is a hard error; duplicate screenshot
modes are ignored; a `--report-screen` with no `screen` in `--report-level` is
ignored (with a warning).

### Execution overlay (HUD)

When running flows with `--run-tests`, add `--execution-overlay` to draw a live
Heads-Up Display **inside** the automated Chrome window — a real-time execution
tree, progress bar, status, and log stream. It is injected over the existing CDP
connection into an isolated Shadow DOM and never touches the app's own DOM; the
empty space around the card is click-through, so the page stays interactive.

The card itself is a small floating panel you can work with:

- **scroll** the tree / logs (the tree can get long),
- **collapse** any branch (click a group row) or all at once (the `⊟` button),
- **drag** it by its header to reposition it,
- **minimize** it (`–`), and
- toggle **click-through** (`▣`) — makes the whole card `pointer-events:none`
  again, so if it ever sits over an element the automation needs to click you can
  let the mouse pass straight through it (dragging/minimizing works too).

```bash
python3 session_launcher.py --user=admin --password=admin \
  --url=http://localhost:8069 --run-tests=my_scenario \
  --execution-overlay=tree,progress,status,logs
```

Single ad-hoc user:

```bash
python3 session_launcher.py --user=role_agent --password=CHANGE-ME \
  --url=http://localhost:8069/web/login
```

Full example (every option at once):

```bash
  python3 ./session_launcher.py \
    --env=localhost \
    --sessions-dir=./user_sessions \
    --config=./users.json \
    --filter-users=all
```

Example with user parameters:
```bash
  python3 ./session_launcher.py \
    --env=localhost \
    --sessions-dir=./user_sessions \
    --user='test' --password='test'
```

This launches one window per user from `users.json`, storing each profile under
`./user_sessions/localhost:8069-<user>`, and leaves the windows running after the
script exits (`--detach`).

Simplest possible run (all users from `users.json`, foreground, default dirs):

```bash
python3 ./session_launcher.py --env=localhost
```

## Windows

Create and activate the virtual environment, then install dependencies (PowerShell):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run — full example (PowerShell uses a backtick for line continuation):

```powershell
python .\session_launcher.py `
  --env=localhost `
  --sessions-dir=.\user_sessions `
  --config=.\users.json `
  --filter-users=all
```

## macOS

Create and activate the virtual environment, then install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Run — full example:

```bash
python3 ./session_launcher.py \
  --env=localhost \
  --sessions-dir=./user_sessions \
  --config=./users.json \
  --filter-users=all
```

**Keychain prompt:** macOS Chrome encrypts saved passwords with a secret kept in the
login Keychain, so the first run shows a *"security wants to use your confidential
information"* dialog. Choose **Always Allow** — later runs are then silent. If you
dismiss it you'll see `Skipping password save … could not read the Chrome Safe
Storage key`; the windows still open and auto-login still works, only the entry in
Chrome's password manager is missing.


------------------------------------------


cd ~/chrome-multi-session

# dev — all 9 roles, read-only matrix (what you just verified piecewise):
./run_access_matrix.sh https://app-dev.example.com

# dev — plus the dev-safe writes (agent edit, manager edit/dictionary/team-roles, admin team-roles, export):
./run_access_matrix.sh https://app-dev.example.com dev --writes

# localhost with the 76SEED seed — originals incl. fin_both and dept/name scoping:
./run_access_matrix.sh http://localhost:8069

# localhost — absolutely everything, incl. the mutating state-dependent flows:
./run_access_matrix.sh http://localhost:8069 seeded --writes

------------- TESTING ROLES ------------------------------

cd <your checkout>

ENV=app-dev        # which environment: app-dev, app-stg or localhost
                     # (--env supplies its URL too, so no ORIGIN/PREFIX pair)

for R in admin manager agent fin_comp fin_fine fin_both division analyst analyst2; do
  echo "=== $R ==="
  python3 session_launcher.py \
    --env="$ENV" --filter-users="role_$R" \
    --run-tests="access_$R" \
    --execution-overlay=all --close-after
done

------ Each command ------

Same thing unrolled, one role per line, so a single role can be re-run on its own.
--env picks the ENVIRONMENT, --filter-users the LOGIN; together they select exactly
one session, which is what keeps the matrix honest (the engine runs every requested
scenario against every ATTACHED session, so two roles must never share a run).
--env also supplies the URL, so there is no origin to keep in sync by hand.
Each window stays open after its run so the overlay can be inspected: close it yourself,
or append --close-after to have the launcher do it.

ENV=app-dev        # or app-stg, or localhost (--env=dev/stg/local also work)

# --- read-only matrix (safe on shared environments) ---
python3 session_launcher.py --env="$ENV" --filter-users=role_admin    --run-tests=access_admin    --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_manager  --run-tests=access_manager  --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_agent    --run-tests=access_agent    --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_fin_comp --run-tests=access_fin_comp --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_fin_fine --run-tests=access_fin_fine --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_fin_both --run-tests=access_fin_both --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_division --run-tests=access_division --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_analyst  --run-tests=access_analyst  --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_analyst2 --run-tests=access_analyst2 --execution-overlay=all

# --- everything tagged for a role: read + write + extras ---
# tag:role:<r> is a UNION, not an intersection - it pulls in that role's WRITE scenarios
# too (role:manager = 7, incl. setup_manager_foreign_ticket and the Team Roles wizard).
# Use on localhost / a scratch DB; on a shared environment prefer the read-only list above.
python3 session_launcher.py --env="$ENV" --filter-users=role_admin    --run-tests=tag:role:admin    --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_manager  --run-tests=tag:role:manager  --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_agent    --run-tests=tag:role:agent    --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_fin_comp --run-tests=tag:role:fin_comp --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_fin_fine --run-tests=tag:role:fin_fine --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_fin_both --run-tests=tag:role:fin_both --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_division --run-tests=tag:role:division --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_division --run-tests=access_division_dev --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_analyst  --run-tests=tag:role:analyst  --execution-overlay=all
python3 session_launcher.py --env="$ENV" --filter-users=role_analyst2 --run-tests=tag:role:analyst2 --execution-overlay=all

# --- fixture: run ONCE as the manager before access_agent / write_agent_edit on any
#     environment without the 76SEED seed (e.g. app-dev) ---
python3 session_launcher.py --env="$ENV" --filter-users=role_manager  --run-tests=setup_manager_foreign_ticket --execution-overlay=all

# There is no role:agent2 - role_agent2 is only ever a TARGET user
# (see write_manager_team_roles), never a session a scenario runs as.

