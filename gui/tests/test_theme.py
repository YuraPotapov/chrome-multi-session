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


# ------------------------------------------------- stylesheets that cascade

def _dominant_colour(widget):
    image = widget.grab().toImage()
    counts = {}
    for x in range(image.width()):
        for y in range(image.height()):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def test_a_primary_button_keeps_its_fill_inside_a_styled_container(qapp):
    """A container's stylesheet must not repaint the widgets inside it.

    Regression: the preview strip set a plain `background:`, which cascades to
    its children and outranks the application sheet. The RUN button lost its
    accent fill and painted its near-white label onto the strip's near-white
    background - a button that looked empty.
    """
    from cms_gui.pages.command import CommandPage
    from cms_gui.settings import Settings
    page = CommandPage(Settings())
    page.resize(1100, 700)
    page.show()
    qapp.processEvents()
    assert _dominant_colour(page.run_button).lower() == theme.ACCENT.lower()


def test_a_ticked_box_actually_has_a_tick_in_it(qapp):
    """Regression: a stylesheet can fill an indicator but cannot mark it.

    `::indicator:checked` only ever set a background, so a ticked box was an
    accent square with nothing inside - indistinguishable at a glance from a
    disabled one. The mark is painted by the style now, which this proves is
    still reached: a stylesheet rule for `::indicator` would silently take the
    drawing back off it, and the box would go blank again.
    """
    from PySide6.QtWidgets import QCheckBox
    box = QCheckBox("Leave the windows open")
    box.setChecked(True)
    box.show()
    qapp.processEvents()
    inside = _indicator(box).copy(3, 3, theme.INDICATOR - 6, theme.INDICATOR - 6)
    lightnesses = [inside.pixelColor(x, y).lightness()
                   for x in range(inside.width()) for y in range(inside.height())]
    assert min(lightnesses) < 160, "no accent fill"
    assert max(lightnesses) > 200, "filled, but nothing drawn in it"


def test_an_unticked_box_is_not_filled(qapp):
    from PySide6.QtWidgets import QCheckBox
    box = QCheckBox("Leave the windows open")
    box.show()
    qapp.processEvents()
    inside = _indicator(box).copy(3, 3, theme.INDICATOR - 6, theme.INDICATOR - 6)
    darkest = min(inside.pixelColor(x, y).lightness()
                  for x in range(inside.width()) for y in range(inside.height()))
    assert darkest > 200


def test_the_rows_of_a_list_get_the_same_tick_as_a_standalone_box(qapp):
    """The Accounts, Extensions and Scenarios lists are check boxes too.

    Those indicators are drawn by the style rather than by a widget, so they are
    the half of the change a stylesheet could never have covered.
    """
    from cms_gui import widgets
    check_list = widgets.CheckList()
    check_list.add("role_admin", "role_admin", "VRR Admin")
    check_list.set_checked(["role_admin"])
    check_list.resize(300, 160)
    check_list.show()
    qapp.processEvents()
    image = check_list.list.viewport().grab().toImage()
    accent = theme.color(theme.ACCENT).lightness()
    assert any(abs(image.pixelColor(x, y).lightness() - accent) < 12
               for x in range(min(image.width(), theme.INDICATOR * 2))
               for y in range(min(image.height(), theme.INDICATOR * 2)))


def _indicator(box):
    """The tick box cropped out of a rendered QCheckBox."""
    from PySide6.QtWidgets import QStyle, QStyleOptionButton
    option = QStyleOptionButton()
    option.initFrom(box)
    rect = box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)
    return box.grab().toImage().copy(rect)


def test_scoped_style_does_not_reach_children(qapp):
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget
    from cms_gui import widgets
    box = QWidget()
    layout = QVBoxLayout(box)
    button = QPushButton("x")
    layout.addWidget(button)
    widgets.scoped_style(box, "background: #123456;")
    assert box.styleSheet().startswith("#")          # scoped by object name
    assert "#123456" not in button.styleSheet()
