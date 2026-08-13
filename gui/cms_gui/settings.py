"""Persistent GUI settings (QSettings) - paths, per-environment overrides, presets.

Only things that belong to the *front-end* live here. Anything the launcher
itself owns (users, environments, scenarios) stays in ``users.json`` and the
flows tree, so the CLI and the GUI never disagree about them.
"""

import json

from PySide6.QtCore import QSettings

ORG = "chrome-multi-session"
APP = "gui"


class Settings:
    """Thin, typed wrapper so pages never spell a settings key by hand."""

    def __init__(self):
        self._qs = QSettings(ORG, APP)

    # -- core location --------------------------------------------------------
    @property
    def core_script(self):
        return self._qs.value("core/script", "", str)

    @core_script.setter
    def core_script(self, value):
        self._qs.setValue("core/script", value or "")

    @property
    def interpreter(self):
        return self._qs.value("core/interpreter", "", str)

    @interpreter.setter
    def interpreter(self, value):
        self._qs.setValue("core/interpreter", value or "")

    @property
    def config(self):
        """--config value; empty means the core's own default (users.json)."""
        return self._qs.value("core/config", "", str)

    @config.setter
    def config(self, value):
        self._qs.setValue("core/config", value or "")

    # -- window ---------------------------------------------------------------
    def geometry(self):
        return self._qs.value("window/geometry", None)

    def save_geometry(self, data):
        self._qs.setValue("window/geometry", data)

    @property
    def page(self):
        return self._qs.value("window/page", "commands", str)

    @page.setter
    def page(self, value):
        self._qs.setValue("window/page", value)

    # -- per-environment extras (the core has no place for these) -------------
    def env_override(self, alias):
        return self._qs.value("env/override/" + alias, "", str)

    def set_env_override(self, alias, url):
        self._qs.setValue("env/override/" + alias, url or "")

    def directory(self, name, default=""):
        """Default --flows-dir / --reports-dir / --sessions-dir for runs."""
        return self._qs.value("dirs/" + name, default, str)

    def set_directory(self, name, value):
        self._qs.setValue("dirs/" + name, value or "")

    # -- command form ---------------------------------------------------------
    def command_state(self):
        """The last command the user built, as a dict (empty on first run)."""
        raw = self._qs.value("command/state", "", str)
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def save_command_state(self, state):
        self._qs.setValue("command/state", json.dumps(state))

    def presets(self):
        """Saved command forms: {name: state dict}."""
        raw = self._qs.value("command/presets", "", str)
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def save_presets(self, presets):
        self._qs.setValue("command/presets", json.dumps(presets))
