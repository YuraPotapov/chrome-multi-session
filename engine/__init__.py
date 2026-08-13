"""The flow-execution engine.

Layers (see docs/flows.md for the YAML format): loader (YAML in), compiler (expand reusable
blocks + resolve selectors/params into a flat plan), assertions (validation
registry), runner (attach + execute + report), artifacts (diagnostics on
failure). The engine talks to browsers only through :mod:`adapters`.
"""
