"""The application icon, painted from the design tokens.

Drawn rather than shipped as a file, for the same reason the stylesheet is
generated rather than checked in: the icon is part of the design system, and a
committed PNG would be one more place the accent colour has to be changed by hand.
Painting it also means every size is rendered at its own size instead of being
scaled down from one bitmap, which is what makes the 16px taskbar version legible.

The mark is three offset windows in ascending lightness on a dark slate ground -
"more than one browser session", which is the whole point of the tool. At 16px the
title bars and hairlines drop out and it reduces to three light bands, which is
still the same idea.

``python -m cms_gui.icon <directory>`` writes the PNG and ICO files a packaged
build needs.
"""

import os
import struct
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from . import theme

# The design grid every coordinate below is expressed in.
GRID = 32.0

# Rendered sizes: the platform picks; 16/32 are the ones that have to survive.
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

# (x, y, fill) on the grid, painted back to front. One window size for all three.
# The three fills are two ramp steps apart rather than one: at 16px the outlines
# are gone and lightness is the only thing separating them.
WINDOW_W, WINDOW_H = 18.0, 14.0
WINDOWS = ((2.0, 4.0, theme.ACCENT_RAMP[600]),
           (7.0, 9.0, theme.ACCENT_RAMP[400]),
           (12.0, 14.0, theme.ACCENT_RAMP[100]))
GROUND = theme.ACCENT_RAMP[900]
TITLE_BAR = theme.ACCENT_RAMP[800]
TITLE_H = 3.0

# Below this the title bars and the 1px outlines stop separating anything and
# start muddying the three bands, so they are left out entirely.
DETAIL_FROM = 24

_cache = {}


def pixmap(size):
    """The icon at one size, painted for that size."""
    if size in _cache:
        return _cache[size]
    image = QPixmap(size, size)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        _paint(painter, size)
    finally:
        painter.end()
    _cache[size] = image
    return image


def app_icon():
    """A QIcon carrying every size, for the window and the task switcher."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(pixmap(size))
    return icon


def _paint(painter, size):
    scale = size / GRID
    detail = size >= DETAIL_FROM
    hairline = max(1.0, scale)

    def rect(x, y, width, height):
        return QRectF(x * scale, y * scale, width * scale, height * scale)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(GROUND))
    painter.drawRect(QRectF(0, 0, size, size))

    for x, y, fill in WINDOWS:
        body = rect(x, y, WINDOW_W, WINDOW_H)
        if detail:
            # Outline in the ground colour so two overlapping windows still read
            # as two windows rather than as one lighter shape.
            pen = painter.pen()
            pen.setColor(QColor(GROUND))
            pen.setWidthF(hairline)
            painter.setPen(pen)
        painter.setBrush(QColor(fill))
        painter.drawRect(body)
        if detail:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(TITLE_BAR))
            painter.drawRect(rect(x, y, WINDOW_W, TITLE_H))


# -- files, for packaging -----------------------------------------------------
def write_png(path, size=256):
    return pixmap(size).save(path, "PNG")


def write_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """A genuinely multi-size .ico - every size painted at its own size.

    Assembled here rather than handed to Qt's ICO writer, which takes a single
    pixmap: that wrote one 256px image and left Windows to scale it down, which
    is exactly what the 16px taskbar icon must not be. The container is the
    PNG-in-ICO form every Windows since Vista reads.
    """
    from PySide6.QtCore import QBuffer, QIODevice

    encoded = []
    for size in sorted(sizes):
        # The buffer owns its bytes: handing QBuffer a temporary QByteArray
        # leaves Qt writing into something Python has already freed.
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not pixmap(size).save(buffer, "PNG"):
            return False
        buffer.close()
        encoded.append((size, bytes(buffer.data())))

    header = struct.pack("<HHH", 0, 1, len(encoded))     # reserved, type=icon, count
    offset = len(header) + 16 * len(encoded)
    directory, payload = b"", b""
    for size, data in encoded:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,      # 0 means 256 in this field
            0 if size >= 256 else size,
            0, 0,                            # palette, reserved
            1, 32,                           # planes, bits per pixel
            len(data), offset)
        payload += data
        offset += len(data)
    try:
        with open(path, "wb") as handle:
            handle.write(header + directory + payload)
    except OSError:
        return False
    return True


def main(argv=None):
    from PySide6.QtGui import QGuiApplication

    argv = list(sys.argv if argv is None else argv)
    directory = argv[1] if len(argv) > 1 else os.getcwd()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    os.makedirs(directory, exist_ok=True)
    written = []
    for size in SIZES:
        path = os.path.join(directory, "icon-%d.png" % size)
        if write_png(path, size):
            written.append(path)
    ico = os.path.join(directory, "icon.ico")
    if write_ico(ico):
        written.append(ico)
    for path in written:
        print(path)
    del app
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
