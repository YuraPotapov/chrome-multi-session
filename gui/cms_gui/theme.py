"""The "Industry" design system, translated to Qt.

The look comes from the design export (``_ds/industry-*/styles.css``): a light
blueprint/wireframe surface - square corners, hairline dividers, one slate-blue
accent, condensed headings, monospace for anything the machine produced.

Everything is a token here rather than a literal in a widget, for the same
reason the CSS file is the source of truth in the design: retuning the system
means editing this file, not hunting through six pages.
"""

import os

from PySide6.QtGui import QColor, QFontDatabase

# --- palette (from the design system's OKLCH ramps) -------------------------
BG = "#f2f2f3"
SURFACE = "#e9e9ea"
TEXT = "#1d1f20"
ACCENT = "#5980a6"

NEUTRAL = {100: "#f5f5f8", 200: "#e7e7ea", 300: "#d4d4d7", 400: "#b7b7ba",
           500: "#98989b", 600: "#7a7a7d", 700: "#5d5d60", 800: "#424244",
           900: "#2b2b2d"}
ACCENT_RAMP = {100: "#eef6ff", 200: "#d6ebff", 300: "#b5d9fd", 400: "#94bce3",
               500: "#749dc4", 600: "#597ea3", 700: "#416180", 800: "#2c455d",
               900: "#1d2d3d"}

# color-mix(in srgb, #1d1f20 16%, transparent) over the page background, which is
# what the browser actually paints. Qt has no color-mix, so it is resolved once.
DIVIDER = "#cfcfd0"
DIVIDER_STRONG = "#a8a8ab"

# Status colours, reused by the run tree, the log pane and the tags.
OK = ACCENT_RAMP[700]
WARN = "#a8712a"
BAD = "#a33a2e"

# --- type -------------------------------------------------------------------
# The design asks for Barlow / Barlow Condensed / JetBrains Mono. They are web
# fonts, so they may not be installed: each family is a stack, and Qt falls
# through to the first one present. Drop the .ttf files into
# cms_gui/assets/fonts/ and they are loaded ahead of anything system-wide.
FONT_BODY = ['Barlow', 'Inter', 'Noto Sans', 'DejaVu Sans', 'sans-serif']
FONT_HEADING = ['Barlow Condensed', 'Barlow', 'Oswald', 'DejaVu Sans Condensed',
                'Noto Sans', 'sans-serif']
FONT_MONO = ['JetBrains Mono', 'DejaVu Sans Mono', 'Consolas', 'Menlo', 'monospace']

ASSET_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")


def load_fonts():
    """Register any bundled font files; returns the families actually added."""
    families = []
    try:
        names = sorted(os.listdir(ASSET_FONTS))
    except OSError:
        return families
    for name in names:
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        font_id = QFontDatabase.addApplicationFont(os.path.join(ASSET_FONTS, name))
        if font_id != -1:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def _stack(families):
    return ", ".join('"%s"' % f if " " in f else f for f in families)


BODY_CSS = _stack(FONT_BODY)
HEADING_CSS = _stack(FONT_HEADING)
MONO_CSS = _stack(FONT_MONO)


def mono_font(point_size=None):
    """A QFont for machine text, resolved against what is installed."""
    from PySide6.QtGui import QFont
    font = QFont()
    font.setFamilies(FONT_MONO)
    font.setStyleHint(QFont.Monospace)
    if point_size:
        font.setPointSize(point_size)
    return font


def color(name):
    """QColor for a token name, for the places that paint rather than style."""
    return QColor(name)


# --- glyphs -----------------------------------------------------------------
# Never hard-code a symbol in a widget: the three platforms ship different
# fonts, and a character the font lacks renders as an empty box. Each name below
# is a list of candidates in order of preference, ending in something ASCII that
# every font has; :func:`glyph` picks the first one the resolved body font can
# actually draw.
_GLYPH_CANDIDATES = {
    "run": ("▶", "►", ">"),
    "stop": ("■", "▪", "#"),
    # No ASCII fallback on purpose: every stand-in for "copy" is a bare square,
    # which reads as a missing-glyph box rather than as an icon. Better nothing.
    "copy": ("⧉", ""),
    "refresh": ("↻", "⟲", "~"),
    "settings": ("⚙", "✱", "*"),
    "environments": ("▤", "▦", "="),
    "credentials": ("◍", "●", "o"),
    "command": ("⌗", "#", "#"),
    "launch": ("◈", "◆", "+"),
    "history": ("◷", "◔", "@"),
    "developer": ("◧", "◐", "%"),
    "log": ("≡", "☰", "="),
    "artifacts": ("◫", "▢", "[]"),
    "pass": ("✓", "√", "+"),
    "fail": ("✖", "×", "x"),
    "running": ("▸", "►", ">"),
    "pending": ("·", "•", "."),
    "group": ("▸", "►", ">"),
    "browse": ("…", "...", "..."),
}
_glyph_cache = {}


def glyph(name):
    """The best available character for ``name`` in the resolved body font."""
    if name in _glyph_cache:
        return _glyph_cache[name]
    candidates = _GLYPH_CANDIDATES.get(name, ("",))
    chosen = candidates[-1]
    try:
        from PySide6.QtGui import QFont, QRawFont
        font = QFont()
        font.setFamilies(FONT_BODY)
        raw = QRawFont.fromFont(font)
        for candidate in candidates:
            if all(raw.supportsCharacter(ord(c)) for c in candidate):
                chosen = candidate
                break
    except Exception:
        pass          # no QApplication yet, or a Qt build without QRawFont
    _glyph_cache[name] = chosen
    return chosen


def labelled(name, text):
    """"<glyph> text", or just the text when nothing suitable is installed."""
    mark = glyph(name)
    return "%s %s" % (mark, text) if mark else text


# --- stylesheet -------------------------------------------------------------
# One sheet for the whole app. Square corners everywhere and 1px hairlines are
# the design's "wireframe object" rule (styles.css: .card,.btn,.input,.tag
# {border-radius: 0}), so they are set globally rather than per widget.
STYLESHEET = """
* {{
    font-family: {body};
    font-size: 13px;
    color: {text};
}}
QWidget {{ background: {bg}; }}
QMainWindow, QDialog {{ background: {bg}; }}
/* A label is text, not a panel. Inheriting the page background from the rule
   above made every label paint an opaque rectangle of it, which showed up as a
   band of the wrong colour wherever a label sat on a tinted surface - the
   CONFIGURE and OBSERVE headings on the sidebar. Anything that genuinely needs a
   fill (the Tag pill) sets its own. */
QLabel {{ background: transparent; }}

QToolTip {{
    background: {n800}; color: {n100};
    border: 1px solid {n900}; padding: 4px 7px;
}}

/* --- headings ------------------------------------------------------------ */
QLabel[role="h1"] {{
    font-family: {heading}; font-size: 26px; font-weight: 600;
    letter-spacing: 0.3px; padding: 0;
}}
QLabel[role="h2"] {{ font-family: {heading}; font-size: 17px; font-weight: 600; }}
QLabel[role="kicker"] {{
    font-family: {heading}; font-size: 11px; font-weight: 600;
    letter-spacing: 2px; color: {n600};
}}
QLabel[role="lede"] {{ font-size: 13px; color: {n700}; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: 12px; color: {n700}; }}
QLabel[role="muted"] {{ color: {n600}; }}

/* --- buttons ------------------------------------------------------------- */
QPushButton {{
    font-family: {heading}; font-size: 14px; font-weight: 600;
    background: transparent; color: {text};
    border: 1px solid {divider}; border-radius: 0;
    padding: 5px 13px; min-height: 20px;
}}
QPushButton:hover {{ background: {n200}; }}
QPushButton:pressed {{ background: {n300}; }}
QPushButton:disabled {{ color: {n500}; border-color: {n300}; }}
QPushButton[variant="primary"] {{
    background: {accent}; color: {bg}; border-color: {accent};
}}
QPushButton[variant="primary"]:hover {{ background: {a600}; border-color: {a600}; }}
QPushButton[variant="primary"]:pressed {{ background: {a700}; border-color: {a700}; }}
QPushButton[variant="primary"]:disabled {{
    background: {n300}; border-color: {n300}; color: {n500};
}}
QPushButton[variant="ghost"] {{
    border-color: transparent; color: {a700};
    font-family: {body}; font-size: 12px; padding: 2px 6px;
}}
QPushButton[variant="ghost"]:hover {{ background: {a100}; }}
QPushButton[variant="nav"] {{
    text-align: left; border: none; border-left: 3px solid transparent;
    font-family: {body}; font-size: 13px; font-weight: 400;
    padding: 7px 13px; border-radius: 0;
}}
QPushButton[variant="nav"]:hover {{ background: {n200}; }}
QPushButton[variant="nav"][active="true"] {{
    background: {a200}; color: {a900}; border-left: 3px solid {accent};
}}

/* --- inputs -------------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {surface}; color: {text};
    border: 1px solid {divider}; border-radius: 0;
    padding: 4px 8px; min-height: 20px;
    selection-background-color: {a300}; selection-color: {text};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {divider_strong}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {n200}; color: {n500};
}}
QLineEdit[mono="true"], QComboBox[mono="true"] {{ font-family: {mono}; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {n600}; width: 0; height: 0; margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {bg}; border: 1px solid {divider};
    selection-background-color: {a200}; selection-color: {a900};
    outline: none;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {divider_strong}; background: {surface}; border-radius: 0;
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QCheckBox::indicator:disabled {{ border-color: {n300}; background: {n200}; }}

/* --- tables -------------------------------------------------------------- */
QTableView, QTreeView, QListView {{
    background: {bg}; alternate-background-color: {bg};
    border: none; gridline-color: {n200};
    selection-background-color: {a200}; selection-color: {a900};
    outline: none;
}}
QTableView::item, QTreeView::item {{ padding: 3px 4px; border: none; }}
QHeaderView::section {{
    background: {bg}; color: {n600};
    font-family: {heading}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; text-transform: uppercase;
    border: none; border-bottom: 1px solid {divider};
    padding: 6px 6px;
}}
QTableCornerButton::section {{ background: {bg}; border: none; }}

/* --- containers ---------------------------------------------------------- */
QFrame[role="panel"] {{ background: transparent; border: 1px solid {divider}; }}
QFrame[role="sidebar"] {{ background: {n100}; border-right: 1px solid {divider}; }}
QFrame[role="bar"] {{ background: {bg}; border-bottom: 1px solid {divider}; }}
QFrame[role="footer"] {{ background: {n100}; border-top: 1px solid {divider}; }}
QFrame[role="hline"] {{ background: {divider}; max-height: 1px; border: none; }}
QFrame[role="vline"] {{ background: {divider}; max-width: 1px; border: none; }}

QScrollArea {{ border: none; background: {bg}; }}
QScrollBar:vertical {{ background: {n200}; width: 10px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: {n400}; min-height: 24px; border-radius: 0; }}
QScrollBar::handle:vertical:hover {{ background: {n500}; }}
QScrollBar:horizontal {{ background: {n200}; height: 10px; margin: 0; border: none; }}
QScrollBar::handle:horizontal {{ background: {n400}; min-width: 24px; border-radius: 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{
    background: {n300}; border: none; border-radius: 0; height: 4px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {accent}; }}

QMenuBar {{ background: {bg}; border-bottom: 1px solid {divider}; }}
QMenuBar::item {{ padding: 4px 9px; background: transparent; }}
QMenuBar::item:selected {{ background: {n200}; }}
QMenu {{ background: {bg}; border: 1px solid {divider}; padding: 3px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; }}
QMenu::item:selected {{ background: {a200}; color: {a900}; }}
QMenu::separator {{ height: 1px; background: {divider}; margin: 4px 6px; }}

QSplitter::handle {{ background: {divider}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QStatusBar {{ background: {n200}; border-top: 1px solid {divider}; }}
QStatusBar::item {{ border: none; }}
"""


def stylesheet():
    return STYLESHEET.format(
        bg=BG, surface=SURFACE, text=TEXT, accent=ACCENT,
        divider=DIVIDER, divider_strong=DIVIDER_STRONG,
        body=BODY_CSS, heading=HEADING_CSS, mono=MONO_CSS,
        n100=NEUTRAL[100], n200=NEUTRAL[200], n300=NEUTRAL[300], n400=NEUTRAL[400],
        n500=NEUTRAL[500], n600=NEUTRAL[600], n700=NEUTRAL[700], n800=NEUTRAL[800],
        n900=NEUTRAL[900],
        a100=ACCENT_RAMP[100], a200=ACCENT_RAMP[200], a300=ACCENT_RAMP[300],
        a600=ACCENT_RAMP[600], a700=ACCENT_RAMP[700], a900=ACCENT_RAMP[900],
    )
