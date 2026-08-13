"""Run context and ``{{param}}`` substitution.

Flows are parameterized with ``{{user.login}}`` / ``{{env.origin}}`` style
placeholders. The context carries a ``user`` dict and an ``env`` dict; a tiny
regex substitutor resolves dotted paths against them - no Jinja dependency.
Unknown placeholders raise, so a typo fails fast at compile time.
"""

import re
from dataclasses import dataclass, field

_PARAM_RE = re.compile(r"{{\s*([\w.]+)\s*}}")


class ParamError(Exception):
    """A ``{{...}}`` placeholder referenced an unknown or null value."""


@dataclass
class RunContext:
    user: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)

    def as_root(self):
        return {"user": self.user, "env": self.env}


def substitute(value, ctx):
    """Return ``value`` with every ``{{a.b}}`` replaced from ``ctx``.

    Non-strings pass through unchanged. ``ctx`` may be a :class:`RunContext`, a
    plain nested dict, or None (in which case any placeholder is an error).
    """
    if not isinstance(value, str):
        return value
    if ctx is None:
        root = {}
    elif isinstance(ctx, RunContext):
        root = ctx.as_root()
    else:
        root = ctx

    def repl(match):
        path = match.group(1)
        cur = root
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise ParamError("unknown parameter {{%s}}" % path)
        if cur is None:
            raise ParamError("parameter {{%s}} is null" % path)
        return str(cur)

    return _PARAM_RE.sub(repl, value)
