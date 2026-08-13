# Unpacked extensions

Drop an extension's **source** in a directory here and it can be installed into every
launched profile by name, with **no network access**:

```
extensions/
  my_helper/
    manifest.json      <- required; a directory without one is ignored
    content.js
```

```bash
python3 session_launcher.py --env=localhost --extensions=my_helper
python3 session_launcher.py --extensions=list        # shows what is here
```

## Why keep sources here rather than fetch from the Web Store

- **No network.** Nothing is downloaded, so runs work offline and in CI, and there is
  no call out to Google on first launch.
- **Editable.** The source is re-copied into each profile on every launch, so edit a
  file, relaunch, and the change is live — no cache to clear, no version to bump.
- **Pinned.** A store extension can change under you; a vendored copy cannot.
- **Yours.** Write an extension specific to the app under test and keep it beside the
  flows that need it.

## Precedence

A directory here **wins over the same name** in `KNOWN_EXTENSIONS` (the Web Store
table in `session_launcher.py`). That is how you pin or patch a store extension
without renaming every command that installs it: unpack it into
`extensions/odoo_debug/` and every `--extensions=odoo_debug` uses your copy.

## What the launcher does to the manifest

Same treatment a downloaded CRX gets, because Chrome will not silently enable an
extension that looks like a store install or asks for broad host access:

- re-keys it under a per-profile key, so it gets a plain non-store id
- drops `update_url`, so Chrome will not replace it with a store build
- narrows `<all_urls>` content-script and web-accessible matches to the session's
  origin, so it needs one host permission and nothing is left awaiting a prompt

It is then registered through the profile's `Preferences` rather than
`--load-extension`, which Chrome 137+ refuses for unpacked extensions.

Your `manifest.json` is not modified in place — the rewrite happens on the copy
inside the profile.

## `_autologin/` — the launcher's own extension

Underscore-prefixed directories are **machinery, not installable**: they never appear
in `--extensions=list` and `--extensions=_autologin` will not find them. This one is
useless on its own, because the launcher generates its credentials per profile.

```
_autologin/
  manifest.json     name / matches / key are patched per user at install time
  content.js        the behaviour - ordinary, editable, lintable JS
  (config.js)       GENERATED into each profile: credentials + selectors
```

`config.js` is listed first in the same `content_scripts` entry as `content.js`, so
they share one isolated world and `content.js` simply reads `AUTOLOGIN.login`,
`AUTOLOGIN.password` and `AUTOLOGIN.selectors`. **No credential is ever written into
the checked-in source** — it exists only inside the profile.

The form selectors default to `DEFAULT_LOGIN_SELECTORS` in `session_launcher.py`
(`input[name="login"]`, `input[name="password"]`, `button[type="submit"]` — the common
convention) and are passed through per install, so pointing this at an app whose login
form differs is a config change rather than a JavaScript edit.

## Vendored third-party extensions

`odoo_debug/` is **not ours**: it is the "Odoo Debug" extension by Droggol Infotech
Private Limited, unpacked from the Chrome Web Store, kept here so runs need no network.
Its own `LICENSE` and `README.md` ship with it and must stay.

| | |
| --- | --- |
| Upstream | https://chrome.google.com/webstore/detail/odoo-debug/hmdmhilocobgohohpdpolmibjklfgkbi |
| Version | 5.1 |
| Licence | **GPL-3.0** |

**Read this before publishing.** Vendoring a GPL-3.0 extension into a repository you
intend to release under a permissive licence needs a deliberate decision, not a
default. The usual reading is that an unmodified extension in its own directory, run
by Chrome as a separate program, is *mere aggregation* rather than a derivative work —
but that is a legal judgement, so get it confirmed rather than assumed. Whatever you
conclude, state plainly in the top-level README and any NOTICE file that
`extensions/odoo_debug/` is third-party and separately licensed under GPL-3.0.

If that is more trouble than it is worth, delete the directory: `--extensions=odoo_debug`
falls straight back to downloading it from the Web Store at run time, which sidesteps
redistribution entirely. That is the only thing the local copy buys you here — offline
runs and a pinned version.

The `_metadata/` directory (the Web Store's signature over the original files) was
removed: the launcher re-keys the manifest on install, so the signature no longer
matches anything and only invites confusion.
