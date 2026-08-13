"""The theme's glyph resolution.

The GUI runs on three platforms with three different font sets, and a character
the font lacks renders as an empty box. Nothing may hard-code a symbol: every
one goes through :func:`theme.glyph`, which is what these tests check.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import theme

GUI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_glyph_resolves_to_something_the_font_can_draw(qapp):
    from PySide6.QtGui import QFont, QRawFont
    font = QFont()
    font.setFamilies(theme.FONT_BODY)
    raw = QRawFont.fromFont(font)
    unusable = []
    for name in theme._GLYPH_CANDIDATES:
        chosen = theme.glyph(name)
        if not all(raw.supportsCharacter(ord(c)) for c in chosen):
            unusable.append("%s -> %r" % (name, chosen))
    assert not unusable, ("these would render as empty boxes in %s: %s"
                          % (raw.familyName(), ", ".join(unusable)))


def test_every_candidate_list_ends_in_something_ascii():
    # The last entry is the guaranteed fallback, so it must not itself be exotic.
    for name, candidates in theme._GLYPH_CANDIDATES.items():
        assert candidates[-1].isascii(), name


def test_labelled_puts_the_glyph_before_the_text(qapp):
    assert theme.labelled("run", "RUN").endswith("RUN")
    assert len(theme.labelled("run", "RUN")) > len("RUN")


def test_an_unknown_glyph_name_degrades_to_no_icon(qapp):
    assert theme.glyph("no-such-icon") == ""
    assert theme.labelled("no-such-icon", "Text") == "Text"


def test_no_module_hard_codes_a_symbol_outside_the_theme():
    """Guards the rule, not just today's symbols.

    A literal glyph in a page is exactly how the broken '⧉' and '⌗' got in: they
    looked fine in the design's web font and rendered as boxes in DejaVu.
    """
    offenders = []
    for root, _dirs, files in os.walk(os.path.join(GUI_DIR, "cms_gui")):
        for name in sorted(files):
            if not name.endswith(".py") or name == "theme.py":
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, start=1):
                    if line.lstrip().startswith("#"):
                        continue        # prose in a comment is fine
                    for char in line:
                        # Symbols/pictographs live well above Latin-1; the em
                        # dash, ellipsis and middle dot used in prose do not.
                        if ord(char) > 0x2100:
                            offenders.append("%s:%d %r" % (name, number, char))
    assert not offenders, ("use theme.glyph() instead of a literal: %s"
                           % ", ".join(offenders[:8]))
