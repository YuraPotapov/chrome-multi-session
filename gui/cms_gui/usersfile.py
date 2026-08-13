"""Reading, validating and writing ``users.json`` - the core's own config file.

The schema is the launcher's, unchanged: a JSON array of objects with ``env``,
``class``, ``login``, ``password`` and an optional ``run-tests``. The GUI is a
form over that file and nothing more, so the CLI keeps working on it untouched.

Validation deliberately mirrors ``session_launcher.load_users`` /
``_parse_tests_field``: the GUI must never write a file the launcher would then
refuse to read. Saving is atomic (temp file + replace) with a one-slot backup,
because this file holds the only copy of the passwords.
"""

import json
import os

# Keys the launcher reads. Anything else in an entry (comments, notes) is
# preserved as-is when a row round-trips through the editor.
KNOWN_KEYS = ("env", "class", "login", "password", "run-tests", "tests")


class UsersFileError(Exception):
    pass


class UserRow:
    """One config entry, plus whatever unknown keys came with it."""

    def __init__(self, env="", cls="", login="", password="", tests=(), extra=None):
        self.env = env
        self.cls = cls
        self.login = login
        self.password = password
        self.tests = list(tests)
        self.extra = dict(extra or {})

    @classmethod
    def from_entry(cls, entry):
        if not isinstance(entry, dict):
            raise UsersFileError("every entry must be a JSON object, got %r" % (entry,))
        # "prefix" is the retired name for "env"; the launcher still reads it.
        env = entry.get("env", entry.get("prefix", "")) or ""
        field = "run-tests" if "run-tests" in entry else "tests"
        tests = parse_tests(entry.get(field))
        extra = {k: v for k, v in entry.items()
                 if k not in KNOWN_KEYS and k != "prefix"}
        return cls(env=env, cls=entry.get("class", "") or "",
                   login=entry.get("login", "") or "",
                   password=entry.get("password", "") or "",
                   tests=tests, extra=extra)

    def to_entry(self):
        entry = dict(self.extra)
        entry["env"] = self.env
        entry["class"] = self.cls
        entry["login"] = self.login
        entry["password"] = self.password
        if self.tests:
            entry["run-tests"] = list(self.tests)
        return entry

    def tests_text(self):
        return ", ".join(self.tests)

    def copy(self):
        return UserRow(self.env, self.cls, self.login, self.password,
                       list(self.tests), dict(self.extra))


def parse_tests(value):
    """Normalize a ``run-tests`` value the way the launcher does."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise UsersFileError("'run-tests' must be a list or a comma-separated string")
    ids = []
    for item in value:
        if not isinstance(item, str):
            raise UsersFileError("'run-tests' entries must be scenario ids")
        ids.extend(part.strip() for part in item.split(",") if part.strip())
    return ids


def load(path):
    """Read ``users.json`` into rows. Missing file = no rows (not an error)."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise UsersFileError("%s is not valid JSON: %s" % (path, exc))
    except OSError as exc:
        raise UsersFileError("cannot read %s: %s" % (path, exc))
    if not isinstance(data, list):
        raise UsersFileError("%s must be a JSON array of users." % path)
    return [UserRow.from_entry(entry) for entry in data]


def validate(rows):
    """Every problem the launcher would exit on, as a list of messages.

    Returned rather than raised: the editor shows them all at once instead of
    stopping at the first, and a row can be half-typed without an exception.
    """
    problems = []
    seen = {}
    for index, row in enumerate(rows, start=1):
        where = "row %d" % index
        if row.login:
            where += " (%s)" % row.login
        for field, value in (("class", row.cls), ("login", row.login),
                             ("password", row.password)):
            if not str(value).strip():
                problems.append("%s: %s is required." % (where, field))
        for test in row.tests:
            # The launcher exits on this one: the selector is singular, and
            # "tags:x" silently becomes a scenario id that does not exist.
            if test.startswith("tags:"):
                problems.append("%s: run-tests has %r; the selector is singular - "
                                "use 'tag:%s'." % (where, test, test[len("tags:"):]))
        key = (row.env, row.login)
        if row.login and key in seen:
            problems.append("%s: env+login duplicates row %d - that pair names the "
                            "profile folder and must be unique." % (where, seen[key]))
        elif row.login:
            seen[key] = index
    return problems


def save(path, rows):
    """Write the rows back, atomically, keeping one backup of what was there.

    The file holds the only copy of every password, so it is written to a temp
    file in the same directory and then moved into place: an interrupted save
    leaves the previous file intact rather than a truncated one.
    """
    problems = validate(rows)
    if problems:
        raise UsersFileError("\n".join(problems))
    if not path:
        raise UsersFileError("No config path configured.")
    payload = [row.to_entry() for row in rows]
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    temp = os.path.join(directory, ".%s.tmp" % os.path.basename(path))
    try:
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(path):
            backup = path + ".bak"
            try:
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(path, backup)
            except OSError:
                pass          # a missing backup must not block the save itself
        os.replace(temp, path)
    except OSError as exc:
        raise UsersFileError("cannot write %s: %s" % (path, exc))
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    return path


def fingerprint(path):
    """(mtime, size) - enough to notice the file changed underneath the editor."""
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
