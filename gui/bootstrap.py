#!/usr/bin/env python3
"""Create the GUI's own virtualenv, install PySide6 into it, and start the app.

Run it with any Python 3.9+::

    python3 bootstrap.py            # set up (if needed) and launch
    python3 bootstrap.py --setup    # set up only
    python3 bootstrap.py --upgrade  # re-install dependencies, then launch

Stdlib only, and no shell: the same file works on Linux, macOS and Windows. The
environment lives in ``gui/.venv`` and is deliberately separate from the core's
- the GUI needs PySide6 and nothing else, the launcher needs playwright and
cryptography, and on Windows they are frequently not even the same Python.
"""

import os
import subprocess
import sys
import venv

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")
REQUIREMENTS = os.path.join(HERE, "requirements.txt")
STAMP = os.path.join(VENV_DIR, ".requirements-stamp")


def venv_python(venv_dir=VENV_DIR):
    """Path to the interpreter inside a venv, on any platform."""
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _requirements_stamp():
    try:
        with open(REQUIREMENTS, "rb") as fh:
            return str(len(fh.read())) + ":" + str(int(os.path.getmtime(REQUIREMENTS)))
    except OSError:
        return ""


def _up_to_date():
    """True when the venv exists and was built from today's requirements.txt."""
    if not os.path.exists(venv_python()):
        return False
    try:
        with open(STAMP, encoding="utf-8") as fh:
            return fh.read().strip() == _requirements_stamp()
    except OSError:
        return False


def ensure_venv(upgrade=False):
    """Create the venv and install requirements; returns its interpreter path."""
    python = venv_python()
    if not os.path.exists(python):
        print("Creating %s ..." % VENV_DIR)
        # with_pip=True is the whole point here; on Debian/Ubuntu this needs the
        # python3-venv package, and the error below says so.
        try:
            venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
        except Exception as exc:
            sys.exit("Could not create %s (%s).\nOn Debian/Ubuntu: sudo apt install "
                     "python3-venv" % (VENV_DIR, exc))
    if upgrade or not _up_to_date():
        print("Installing dependencies from requirements.txt ...")
        cmd = [python, "-m", "pip", "install", "-q", "--upgrade", "-r", REQUIREMENTS]
        result = subprocess.call(cmd)
        if result != 0:
            sys.exit("pip install failed (exit %d). Fix the error above and re-run."
                     % result)
        with open(STAMP, "w", encoding="utf-8") as fh:
            fh.write(_requirements_stamp())
    return python


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_only = "--setup" in argv
    upgrade = "--upgrade" in argv
    passthrough = [a for a in argv if a not in ("--setup", "--upgrade")]
    python = ensure_venv(upgrade=upgrade)
    if setup_only:
        print("Ready. Start the GUI with:\n  %s -m cms_gui" % python)
        return 0
    # Run the app in the venv's interpreter, from this directory so `cms_gui` is
    # importable without installing the package.
    env = dict(os.environ)
    env["PYTHONPATH"] = HERE + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.call([python, "-m", "cms_gui"] + passthrough, cwd=HERE, env=env)


if __name__ == "__main__":
    sys.exit(main())
