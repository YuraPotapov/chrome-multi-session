"""The shared widgets that stand in for a stock Qt control.

Each one replaces something the design system could not restyle, so what matters
is that it still behaves like the control it replaced - and that the affordance
it was built for is actually there.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import theme, widgets


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
    closed = section.button.text()
    section.set_expanded(True)
    opened = section.button.text()
    assert closed.endswith("Advanced") and opened.endswith("Advanced")
    assert closed != opened
    assert closed.split()[0] == theme.glyph("disclosure_closed")
    assert opened.split()[0] == theme.glyph("disclosure_open")


def test_folding_hides_the_body(qapp):
    section = widgets.Disclosure("Advanced")
    section.body().addWidget(widgets.mono("inner"))
    section.show()
    qapp.processEvents()
    assert not section._body.isVisible()
    section.set_expanded(True)
    assert section._body.isVisible()
