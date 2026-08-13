"""Application entry point."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from . import icon, theme
from .main_window import MainWindow


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("chrome-multi-session GUI")
    app.setOrganizationName("chrome-multi-session")
    # Fusion is the one style that looks the same on all three platforms, which
    # is what makes a single stylesheet enough to carry the design - plus the
    # indicators, which are painted because a stylesheet cannot draw them.
    app.setStyle(theme.app_style())
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    # Set on the application as well as the window: on Wayland and on Windows the
    # task switcher reads it from here, not from the window.
    app.setWindowIcon(icon.app_icon())

    window = MainWindow()
    geometry = window.settings.geometry()
    if geometry:
        window.restoreGeometry(geometry)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
