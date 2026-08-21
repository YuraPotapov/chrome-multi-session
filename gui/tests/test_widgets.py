"""The shared widgets that stand in for a stock Qt control.

Each one replaces something the design system could not restyle, so what matters
is that it still behaves like the control it replaced - and that the affordance
it was built for is actually there.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import icons, theme, widgets


# ------------------------------------------------------------------- stepper

def test_the_stepper_carries_the_spin_box_interface_the_pages_use(qapp):
    """It stands in for a QSpinBox on the Launch page, so it has to answer like one."""
    stepper = widgets.Stepper(1, 64)
    seen = []
    stepper.valueChanged.connect(seen.append)
    stepper.setValue(4)
    assert stepper.value() == 4
    assert seen == [4]
    stepper.setRange(1, 8)
    assert stepper.value() == 4


def test_the_ends_stop_at_the_range(qapp):
    stepper = widgets.Stepper(1, 3)
    assert not stepper.down.isEnabled()          # already at the minimum
    stepper.up.click()
    stepper.up.click()
    assert stepper.value() == 3
    assert not stepper.up.isEnabled()
    assert stepper.down.isEnabled()
    stepper.down.click()
    assert stepper.value() == 2
    assert stepper.up.isEnabled()


def test_the_stepper_disables_as_a_whole(qapp):
    """The Launch page greys it out while "All at once" is ticked."""
    stepper = widgets.Stepper(1, 64, 4)
    stepper.setEnabled(False)
    assert not stepper.spin.isEnabled()
    assert not stepper.up.isEnabled()
    stepper.setEnabled(True)
    assert stepper.spin.isEnabled()
    assert stepper.up.isEnabled()


def test_the_three_pieces_line_up(qapp):
    """A stepper whose buttons are a different height reads as three controls."""
    stepper = widgets.Stepper(1, 64)
    stepper.show()
    qapp.processEvents()
    assert stepper.down.height() == stepper.spin.height() == stepper.up.height()


# ---------------------------------------------------------------- disclosure

def test_a_folded_section_says_it_opens(qapp):
    """Regression: collapsed, Advanced was marked with a middle dot.

    A bullet is decoration - it gives no reason to click, which is the one thing
    a folded section has to do. The mark has to differ between the two states and
    has to be there in the closed one, where most people meet it.
    """
    section = widgets.Disclosure("Advanced")
    closed = section.button.icon().pixmap(16).toImage()
    section.set_expanded(True)
    opened = section.button.icon().pixmap(16).toImage()
    # The label never moves; the chevron is what turns, and it is an icon now
    # rather than a character the font may not have.
    assert section.button.text() == "Advanced"
    assert not closed.isNull() and closed != opened
    # ...and each state shows the chevron that belongs to it.
    assert closed == icons.pixmap("disclosure_closed", 16).toImage()
    assert opened == icons.pixmap("disclosure_open", 16).toImage()


def test_folding_hides_the_body(qapp):
    section = widgets.Disclosure("Advanced")
    section.body().addWidget(widgets.mono("inner"))
    section.show()
    qapp.processEvents()
    assert not section._body.isVisible()
    section.set_expanded(True)
    assert section._body.isVisible()


def test_a_control_on_the_title_line_does_not_narrow_the_body(qapp):
    """Regression: the Server log's box was 150px narrower than its card.

    The button beside it - "Separate Window" - shares the title's line and none
    of the body's, but it was put there by standing the whole section next to it
    in a hbox. A Disclosure is header AND body stacked inside one widget, so
    that arrangement takes the button's width off both.
    """
    from PySide6.QtWidgets import QPlainTextEdit, QPushButton

    section = widgets.Disclosure("Server log", expanded=True)
    inner = QPlainTextEdit()
    section.body().addWidget(inner)
    beside = section.add_to_header(QPushButton("Separate Window"))
    section.resize(600, 300)
    section.show()
    qapp.processEvents()

    assert beside.width() > 0, "the control was never laid out"
    assert inner.width() == section.width(), (
        "the body lost %dpx to a control on the header line"
        % (section.width() - inner.width()))
    # And the control is still where it was asked to be: the far end of the
    # title's line, not below it. Measured against the section, because the two
    # sit in different parents inside it.
    def in_section(widget, corner):
        return widget.mapTo(section, corner(widget.rect()))

    assert in_section(beside, lambda r: r.topRight()).x() >= section.width() - 1
    assert (in_section(beside, lambda r: r.bottomLeft()).y()
            <= in_section(inner, lambda r: r.topLeft()).y())
