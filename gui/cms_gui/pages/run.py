"""Run page: the live view of a run, assembled purely from the event stream.

One panel per driven window, each showing the same step tree the in-page HUD
draws - it comes from the identical ``PlanNode.to_dict()`` payload, carried in
the ``flow.start`` event. Nothing here polls or guesses: every state change is
an event the launcher sent.
"""

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QLabel, QProgressBar, QScrollArea, QVBoxLayout,
                               QWidget)

from .. import theme, widgets

STATE_TAGS = {
    "launching": ("LAUNCHING", "neutral"),
    "launched": ("LAUNCHED", "neutral"),
    "attached": ("ATTACHED", "outline"),
    "running": ("RUNNING", "outline"),
    "passed": ("PASS", "accent"),
    "failed": ("FAIL", "bad"),
    "closed": ("CLOSED", "neutral"),
}

def _mark(status):
    """Status character, resolved against the installed font."""
    name = {"error": "fail"}.get(status, status)
    return theme.glyph(name if name in ("pass", "fail", "running") else "pending")


class SessionPanel(widgets.BlueprintPanel):
    """One window's state and step tree."""

    def __init__(self, parent=None):
        super().__init__(parent, padding=(0, 0, 0, 0))
        column = self.layout()
        column.setSpacing(0)

        head = QWidget()
        widgets.scoped_style(head, "border-bottom: 1px solid %s;" % theme.DIVIDER)
        self.name = QLabel("")
        self.name.setStyleSheet("font-family: %s; font-size: 13px; font-weight: 500;"
                                % theme.MONO_CSS)
        self.tag = widgets.Tag("", "neutral")
        self.meta = widgets.mono("")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        self.counter = widgets.mono("")
        self.counter.setFixedWidth(56)
        self.counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head_row = widgets.row(self.name, self.tag, self.meta, None,
                               self.progress, self.counter)
        head_row.setParent(head)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(14, 10, 14, 10)
        head_layout.addWidget(head_row)
        column.addWidget(head)

        self.steps = QWidget()
        self.steps_layout = QVBoxLayout(self.steps)
        self.steps_layout.setContentsMargins(14, 8, 14, 12)
        self.steps_layout.setSpacing(1)
        column.addWidget(self.steps)

    def update_from(self, session):
        self.name.setText(session["name"])
        label, variant = STATE_TAGS.get(session["state"], (session["state"].upper(),
                                                           "neutral"))
        self.tag.set(label, variant)
        bits = []
        if session.get("pid"):
            bits.append("pid %s" % session["pid"])
        if session.get("scenario"):
            bits.append(session["scenario"])
        self.meta.setText(" · ".join(bits))
        total = session.get("total") or 0
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(session.get("done", 0))
        self.counter.setText("%d/%d" % (session.get("done", 0), total) if total else "")
        self._render_tree(session)

    def _render_tree(self, session):
        """Every scenario this window has run, not only the one running now.

        The in-page overlay has always shown the whole planned list with each
        outcome; this page showed the current scenario alone, because the state
        it read was overwritten at every flow.start. Reading the per-scenario
        records instead puts the same picture here - and the ones still to come
        are listed too, so the shape of the run is visible from the start.
        """
        while self.steps_layout.count():
            item = self.steps_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        runs = session.get("runs") or {}
        if not runs and not session.get("scenarios"):
            hint = QLabel("waiting for the first scenario…")
            hint.setStyleSheet("color: %s; font-size: 12px;" % theme.NEUTRAL[600])
            self.steps_layout.addWidget(hint)
            return

        for scenario, run in runs.items():
            self.steps_layout.addWidget(_scenario_header(scenario, run))
            for node, depth in _walk(run.get("tree") or {}):
                if depth == 0:
                    continue        # the header above already names the flow
                self.steps_layout.addWidget(_step_row(node, depth, run))
        for scenario in session.get("scenarios") or []:
            if scenario not in runs:
                self.steps_layout.addWidget(
                    _scenario_header(scenario, {"status": "pending"}))


def _scenario_header(scenario, run):
    """One scenario's own line: its mark, its id, and how far it got."""
    status = run.get("status", "pending")
    mark = {"pass": "pass", "fail": "fail", "error": "fail",
            "running": "running"}.get(status, "pending")
    counts = ""
    if run.get("total"):
        counts = "   %s/%s" % (run.get("done", 0), run["total"])
    label = QLabel("%s  %s%s" % (theme.glyph(mark), scenario, counts))
    colour = {"pass": theme.OK, "fail": theme.BAD, "error": theme.BAD,
              "running": theme.ACCENT}.get(status, theme.NEUTRAL[500])
    label.setStyleSheet("font-family: %s; font-size: 12px; font-weight: 600; "
                        "color: %s; padding-top: 6px;" % (theme.MONO_CSS, colour))
    return label


def _walk(node, depth=0):
    """Depth-first (node, depth) pairs, in the order the runner executes them."""
    yield node, depth
    for child in node.get("children") or []:
        yield from _walk(child, depth + 1)


def _step_row(node, depth, session):
    is_step = node.get("kind") == "step"
    index = node.get("step_index")
    state = session["steps"].get(index, {}) if is_step else {}
    status = state.get("status", "pending" if is_step else "")
    row = QWidget()
    line = QVBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)

    mark = _mark(status) if is_step else theme.glyph("group")
    text = "%s%s  %s" % ("    " * depth, mark, node.get("label", ""))
    label = QLabel(text)
    if is_step:
        colour = {"pass": theme.OK, "fail": theme.BAD, "error": theme.BAD,
                  "running": theme.ACCENT}.get(status, theme.NEUTRAL[500])
        label.setStyleSheet("font-family: %s; font-size: 12px; color: %s;"
                            % (theme.MONO_CSS, colour))
    else:
        label.setStyleSheet("font-family: %s; font-size: 13px; font-weight: 600;"
                            "color: %s;" % (theme.HEADING_CSS, theme.ACCENT_RAMP[800]))
    if state.get("message") and status in ("fail", "error"):
        label.setToolTip(state["message"])
    line.addWidget(label)
    return row


class RunPage(QWidget):
    """Header (state, elapsed) plus one panel per session, and the summary."""

    open_artifacts = Signal()

    def __init__(self, run_state, parent=None):
        super().__init__(parent)
        self.run_state = run_state
        self._panels = {}
        self._started_at = None

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        head = QWidget()
        head.setProperty("role", "bar")
        widgets.scoped_style(head, "border-bottom: 1px solid %s;" % theme.DIVIDER)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(24, 14, 24, 12)
        self.tag = widgets.Tag("IDLE", "neutral")
        self.meta = widgets.mono("")
        self.elapsed = widgets.mono("—")
        head_layout.addWidget(widgets.row(widgets.heading("Run"), self.tag,
                                          self.meta, None, self.elapsed))
        column.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(24, 18, 24, 18)
        self.body_layout.setSpacing(16)
        self.placeholder = widgets.lede(
            "No run yet. Build a command on the Command page and press RUN - "
            "windows, scenarios and every step appear here as the launcher reports "
            "them.")
        self.body_layout.addWidget(self.placeholder)
        self.body_layout.addStretch(1)
        scroll.setWidget(body)
        column.addWidget(scroll, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setVisible(False)
        self.summary.setStyleSheet(
            "background: %s; color: %s; font-family: %s; font-size: 13px; padding: "
            "14px 18px;" % (theme.ACCENT_RAMP[900], theme.NEUTRAL[100], theme.MONO_CSS))
        column.addWidget(self.summary)

        self._refresh_pending = False
        run_state.changed.connect(self._schedule_refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    def _schedule_refresh(self):
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self.refresh)

    # -- lifecycle ------------------------------------------------------------
    def run_started(self, meta=""):
        self._started_at = time.time()
        self._panels.clear()
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget() and item.widget() is not self.placeholder:
                item.widget().deleteLater()
        self.body_layout.addWidget(self.placeholder)
        self.body_layout.addStretch(1)
        self.placeholder.setText("Launching…")
        self.placeholder.setVisible(True)
        self.summary.setVisible(False)
        self.tag.set("RUNNING", "outline")
        self.meta.setText(meta)
        self._timer.start()

    def run_finished(self, code):
        """Settle the header. Called when the SCENARIOS end, not the process.

        Idempotent: run.finished lands first, and the launcher exiting later
        calls this again with the same answer.
        """
        self._timer.stop()
        self.tag.set("FINISHED (%d)" % code, "accent" if code == 0 else "bad")
        summary = self.run_state.summary
        if summary:
            self.summary.setText(
                "RUN SUMMARY    %d/%d passed · exit %s · %s"
                % (summary.get("passed", 0), summary.get("total", 0),
                   summary.get("exit_code", code), self.run_state.run_dir or "—"))
            self.summary.setVisible(True)
        elif self.run_state.run_dir:
            self.summary.setText("Reports -> %s" % self.run_state.run_dir)
            self.summary.setVisible(True)

    def _tick(self):
        if self._started_at:
            self.elapsed.setText("%.1f s" % (time.time() - self._started_at))

    # -- rendering ------------------------------------------------------------
    def refresh(self):
        self._refresh_pending = False
        sessions = self.run_state.ordered()
        if sessions:
            self.placeholder.setVisible(False)
        for session in sessions:
            panel = self._panels.get(session["name"])
            if panel is None:
                panel = SessionPanel()
                self._panels[session["name"]] = panel
                # Keep the trailing stretch last.
                self.body_layout.insertWidget(self.body_layout.count() - 1, panel)
            panel.update_from(session)
