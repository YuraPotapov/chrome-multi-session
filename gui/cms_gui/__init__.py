"""Desktop front-end for chrome-multi-session.

The GUI is a *client* of the launcher, never a copy of it: it spawns
``session_launcher.py`` through a configured interpreter, asks ``--describe``
what exists, and follows ``--events=-`` for what happens. Nothing about
environments, users or scenarios is defined here.
"""

__version__ = "0.1.0"
