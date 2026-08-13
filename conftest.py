"""Make the project root importable for the test suite regardless of how pytest
is invoked, so ``from engine import ...`` / ``from domain import ...`` resolve
the top-level packages (there is no installed package)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
