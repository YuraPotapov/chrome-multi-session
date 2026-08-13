"""The application icon.

It is painted rather than loaded, so the things that can go wrong are a size that
comes out blank and a small size where the three windows stop being three
windows. Both are checked by looking at the pixels.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import icon, theme


def _colours(size):
    """Every colour in the icon at ``size``, with how many pixels each covers."""
    image = icon.pixmap(size).toImage()
    counts = {}
    for x in range(image.width()):
        for y in range(image.height()):
            name = image.pixelColor(x, y).name().lower()
            counts[name] = counts.get(name, 0) + 1
    return counts


def test_every_size_renders_something(qapp):
    for size in icon.SIZES:
        pixmap = icon.pixmap(size)
        assert not pixmap.isNull(), size
        assert (pixmap.width(), pixmap.height()) == (size, size)


def test_the_icon_carries_every_size(qapp):
    available = {size.width() for size in icon.app_icon().availableSizes()}
    assert available == set(icon.SIZES)


def test_the_whole_square_is_painted(qapp):
    # A transparent corner would show as a notch against a dark task bar.
    image = icon.pixmap(64).toImage()
    for x, y in ((0, 0), (63, 0), (0, 63), (63, 63), (32, 32)):
        assert image.pixelColor(x, y).alpha() == 255, (x, y)


def test_it_is_painted_in_the_design_s_own_colours(qapp):
    colours = _colours(256)
    for token in (icon.GROUND, theme.ACCENT_RAMP[600], theme.ACCENT_RAMP[400],
                  theme.ACCENT_RAMP[100]):
        assert token.lower() in colours, token


def test_the_ground_covers_less_than_half_so_the_mark_is_the_subject(qapp):
    colours = _colours(64)
    assert colours[icon.GROUND.lower()] < 64 * 64 * 0.55


def test_three_windows_are_still_distinguishable_at_sixteen_pixels(qapp):
    """The size that actually matters, and the one with no outlines to help.

    Below DETAIL_FROM the title bars and hairlines are dropped, so the three
    lightness steps are the only thing left telling them apart - each has to hold
    a meaningful number of pixels of its own.
    """
    colours = _colours(16)
    for fill in (theme.ACCENT_RAMP[600], theme.ACCENT_RAMP[400],
                 theme.ACCENT_RAMP[100]):
        assert colours.get(fill.lower(), 0) >= 8, fill


def test_the_small_sizes_leave_out_the_detail_that_would_muddy_them(qapp):
    # The title bar tone appears once the icon is big enough to hold it, and not
    # before.
    assert theme.ACCENT_RAMP[800].lower() not in _colours(16)
    assert theme.ACCENT_RAMP[800].lower() in _colours(32)


def test_it_can_be_written_out_for_packaging(qapp, tmp_path):
    png = tmp_path / "icon-256.png"
    ico = tmp_path / "icon.ico"
    assert icon.write_png(str(png), 256)
    assert icon.write_ico(str(ico))
    assert png.stat().st_size > 0 and ico.stat().st_size > 0
