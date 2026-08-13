"""Small JSON documents the GUI owns, on disk rather than in QSettings.

QSettings is the right home for a preference - a path, a flag, the last page.
It is the wrong home for a growing document: the history is hundreds of entries
deep and every write rewrites the whole blob, and an archived log is a file, not
a value. So anything that grows lives here instead, next to the logs it refers
to, in a directory the user can open and inspect.

Writes are atomic (temp file + ``os.replace``) and reads never raise: a
half-written or hand-edited file degrades to the default rather than taking a
widget down with it. That mirrors ``usersfile.save``, which is kept separate
because it also keeps a ``.bak`` and knows the shape of ``users.json``.
"""

import json
import os
import tempfile

from PySide6.QtCore import QStandardPaths

APP_DIR_NAME = os.path.join("chrome-multi-session", "gui")


def app_data_dir():
    """The GUI's own data directory, created if it is not there yet.

    Built from GenericDataLocation and the name spelled out here rather than
    from AppDataLocation, because that one is derived from the application's
    organisation name - which ``app.py`` deliberately never sets.
    """
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def logs_dir(directory=None):
    """Where per-run log archives go."""
    path = os.path.join(directory or app_data_dir(), "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


class JsonStore:
    """One JSON file, read forgivingly and written atomically."""

    def __init__(self, path, default=None):
        self.path = path
        self._default = default if default is not None else {}

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            return self._copy_default()
        if type(value) is not type(self._default):
            return self._copy_default()
        return value

    def save(self, value):
        """Replace the file's contents; returns True when it reached disk."""
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory, prefix=".tmp-", delete=False)
            try:
                with handle:
                    json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
                os.replace(handle.name, self.path)
            except BaseException:
                _unlink(handle.name)
                raise
        except OSError:
            return False
        return True

    def _copy_default(self):
        return json.loads(json.dumps(self._default))


class NamedConfigs:
    """``{name: document}`` on disk - the saved Launch Sessions configurations."""

    def __init__(self, path):
        self._store = JsonStore(path, default={})
        self._items = {k: v for k, v in self._store.load().items()
                       if isinstance(v, dict)}

    def names(self):
        return sorted(self._items, key=str.lower)

    def get(self, name):
        value = self._items.get(name)
        return json.loads(json.dumps(value)) if isinstance(value, dict) else None

    def put(self, name, document):
        self._items[name] = document
        self._store.save(self._items)

    def remove(self, name):
        if self._items.pop(name, None) is not None:
            self._store.save(self._items)

    def rename(self, old, new):
        if old in self._items and new and new != old:
            self._items[new] = self._items.pop(old)
            self._store.save(self._items)

    def unique_name(self, base):
        """``base``, or ``base (2)`` - the first form nothing else is using."""
        if base not in self._items:
            return base
        index = 2
        while "%s (%d)" % (base, index) in self._items:
            index += 1
        return "%s (%d)" % (base, index)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass
