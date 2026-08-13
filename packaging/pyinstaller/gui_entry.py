"""Frozen entry point for the GUI.

PyInstaller needs a script, and ``cms_gui`` is only ever run as a module
(``python -m cms_gui``), so this is the one line that bridges the two. It is not
used outside a build - a checkout still starts at gui/bootstrap.py.
"""

import sys

from cms_gui.app import main

if __name__ == "__main__":
    sys.exit(main())
