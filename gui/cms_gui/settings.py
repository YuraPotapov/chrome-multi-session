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
        return self._qs.value("window/page", "launch", str)

    @page.setter
    def page(self, value):
        self._qs.setValue("window/page", value)

    @property
    def developer_mode(self):
        """Whether the low-level surfaces (Command page, raw command) show."""
        return self._qs.value("window/developer_mode", False, bool)

    @developer_mode.setter
    def developer_mode(self, value):
        self._qs.setValue("window/developer_mode", bool(value))

    @property
    def run_source(self):
        """Which page the toolbar's RUN acts on: "launch" or "commands"."""
        return self._qs.value("window/run_source", "launch", str)

    @run_source.setter
    def run_source(self, value):
        self._qs.setValue("window/run_source", value or "launch")

    # -- per-environment extras (the core has no place for these) -------------
    def env_override(self, alias):
        return self._qs.value("env/override/" + alias, "", str)

    def set_env_override(self, alias, url):
        self._qs.setValue("env/override/" + alias, url or "")

    # -- what the observing pages were last looking at ------------------------
    @property
    def artifacts_dir(self):
        """The report folder the Artifacts page should reopen on."""
        return self._qs.value("observe/artifacts_dir", "", str)

    @artifacts_dir.setter
    def artifacts_dir(self, value):
        self._qs.setValue("observe/artifacts_dir", value or "")

    @property
    def log_source(self):
        """An archived log's path, or "" for the running log."""
        return self._qs.value("observe/log_source", "", str)

    @log_source.setter
    def log_source(self, value):
        self._qs.setValue("observe/log_source", value or "")

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

    # -- launch sessions form -------------------------------------------------
    def launch_state(self):
        """The last configuration the user built on Launch Sessions.

        Named configurations are a growing document and live in the GUI's data
        directory (see :mod:`cms_gui.store`); this is only the working copy, the
        counterpart of :meth:`command_state`.
        """
        return self._json("launch/state", {})

    def save_launch_state(self, config):
        self._qs.setValue("launch/state", json.dumps(config))

    @property
    def launch_config_name(self):
        """Which saved configuration is currently loaded ("" = unsaved)."""
        return self._qs.value("launch/config_name", "", str)

    @launch_config_name.setter
    def launch_config_name(self, value):
        self._qs.setValue("launch/config_name", value or "")

    def _json(self, key, default):
        raw = self._qs.value(key, "", str)
        try:
            value = json.loads(raw) if raw else default
        except ValueError:
            return default
        return value if isinstance(value, type(default)) else default

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
