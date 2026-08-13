# Writing flows

A **flow** is a YAML file describing browser steps. A **scenario** is a flow you can run
by name; a **block** is a flow other flows reuse with `use:`. They are the same format —
only where they live differs.

```
flows/
  selectors.yaml          # named selectors, shared by every flow
  scenarios/              # runnable by id:  --run-tests=my_scenario
    my_scenario.yaml
  auth/login.yaml         # a block, referenced as `auth.login`
  common/host_up.yaml     # a block, referenced as `common.host_up`
```

The id maps to the path: a **dotted** id `auth.login` is `auth/login.yaml`; a **bare** id
`my_scenario` is `scenarios/my_scenario.yaml`. Point the engine at a different tree with
`--flows-dir=DIR`.

A complete, runnable example lives in [`tests/fixtures/flows/`](../tests/fixtures/flows).

## A minimal scenario

```yaml
id: my_scenario
name: What this proves, in one line
tags: [smoke]
steps:
  - use: auth.login                 # reuse a block
  - goto: "{{env.origin}}/orders"
  - wait_for: list_view
  - fill:
      target: search_input
      value: "ACME"
  - press: Enter
  - assert_visible: first_list_row
```

`id` must match the filename. `name` and `tags` are optional.

## Step shapes

Two forms, interchangeable:

**Shorthand** — exactly one key, the action name:

```yaml
- click: submit_button
- press: Enter
- assert_visible: dashboard
```

**Verbose** — needed whenever you want `timeout:`, `retry:` or `state:`:

```yaml
- type: assert_not_visible
  target: error_dialog
  timeout: 2500
  retry: {attempts: 3, delay: 2}
```

A shorthand step with two keys is an error, so `- click: a` and `- fill: {...}` cannot be
accidentally merged into one step.

## Actions

| Action | Argument | Notes |
| --- | --- | --- |
| `goto` | url | `{{env.origin}}` is the usual prefix |
| `click` | selector | |
| `fill` | `{target, value}` | types into an input |
| `select` | `{target, value}` | native `<select>`; `value` is the **option value**, not its label — clicking an `<option>` does nothing |
| `press` | key | presses on whatever has **focus**, e.g. `Enter`, `ArrowDown` |
| `wait_for` | selector | waits for visible; `state:` can be `attached`, `hidden`, … |
| `use` | flow id | inlines another flow (see below) |

## Assertions

| Assertion | Argument | Passes when |
| --- | --- | --- |
| `assert_exists` | selector | the element is in the DOM (may be invisible — the only way to check an `<option>`) |
| `assert_visible` | selector | the element is visible |
| `assert_not_visible` | selector | the element does **not** appear within `timeout` |
| `assert_text_contains` | `{target, value}` | the element's text contains `value` |
| `assert_url_contains` | substring | |
| `assert_title` | string | |
| `assert_host_up` | url | HTTP reachability, no browser involved |

**`assert_not_visible` always waits its full timeout when it passes** — proving absence
means waiting the window out. It is usually where a slow scenario's time goes, so give it
a short explicit `timeout:` rather than the 30 s default.

## Timeouts and retries

One default: **30 000 ms**, set once on the page when the engine attaches. Any step
without an explicit `timeout:` gets it.

```yaml
- type: assert_text_contains
  target: page_title
  value: "Order 1042"
  timeout: 5000
  retry: {attempts: 10, delay: 1}     # worst case 10 x 5s + 9 x 1s = 59s
```

Worst case for a step is `attempts x timeout + (attempts - 1) x delay`. There is **no
scenario-level or run-level timeout** — the only ceiling is the sum of the steps.

## Selectors

`target:` is looked up in `selectors.yaml` by name; anything not found is passed through
as a raw selector. So these are equivalent:

```yaml
- click: submit_button          # selectors.yaml: submit_button: "button[type=submit]"
- click: "button[type=submit]"
```

Prefer names: they are the seam that lets one tree target several app versions, and the
overlay shows the friendly name rather than raw CSS.

Selectors are **Playwright** selectors, not plain CSS — `:has-text()`, `:text-is()`,
`:visible` and `:nth-match()` all work. `:nth-match(.row, 2)` is how you express "there
is no second one":

```yaml
- assert_visible: ".group-header"                   # at least one
- type: assert_not_visible
  target: ":nth-match(.group-header, 2)"            # ...and no second -> exactly one
  timeout: 2500
```

**`dashboard` is a de-facto reserved name.** `auth.login` gates on it, and every scenario
starts with `use: auth.login`, so a new flows tree must define it as "the app is loaded
and signed in".

## Composition with `use:`

`use:` inlines another flow's steps at that point, recursively. Cycles are detected at
compile time.

```yaml
steps:
  - use: auth.login
  - use: orders.open_list
  - assert_visible: first_list_row
```

Blocks keep the readiness gate written once, and the overlay draws each `use:` as a
collapsible group.

## Parameters

`{{user.login}}` and `{{env.origin}}` are substituted at compile time from the run
context. `env.origin` is the environment's origin with no trailing slash; `env.url` is
the URL the window opened.

An unknown placeholder is an **error at compile time**, not a silent empty string — a
typo fails before the browser is touched.

## Tags

```yaml
tags: [smoke, role:agent]
```

Select by tag with `--run-tests=tag:smoke`. A tag selector is a **union**, not an
intersection: `tag:manual` selects everything tagged manual.

Three tags are excluded from `--run-tests=all`:

| Tag | Meaning |
| --- | --- |
| `template` | needs real selectors filled in before it can run |
| `manual` | has side effects (writes data) |
| `blocked` | parked on a known unmet dependency |

Naming a scenario by id runs it regardless of its tags — these only affect `all`.

## Adding a new action

The action vocabulary is deliberately small. Adding one touches **six** places, and
missing the first gives you "unknown action" at compile time while missing the second
gives a `ValueError` at run time:

1. `engine/compiler.py` — add the name to `SELECTOR_ONLY`, `SELECTOR_AND_VALUE`,
   `VALUE_ONLY` or `URL_TARGET`, according to its argument shape.
2. `engine/runner.py` — a branch in `_do_action`.
3. `adapters/base.py` — the method on `BrowserAdapter`. **Adding an `@abstractmethod`
   breaks any out-of-tree adapter**; prefer a concrete no-op default, as the overlay
   hooks do.
4. `adapters/playwright_adapter.py` — the implementation.
5. `engine/runner.py` — `_MARKED_ACTIONS` and `_mark_label`, if the overlay should flash
   a marker on the element it touches.
6. `tests/test_runner.py` — a dispatch test with the fake adapter.

Adding an **assertion** is smaller: a function in `engine/assertions.py`, an entry in its
`ASSERTIONS` dict, **and** the name in one of the compiler sets in step 1 — that last one
is easy to miss, and without it `_validate` rejects the assertion as an unknown action.
