"""The Excerpts window: who ordered which pages of the whole case, as a table (see excerpts.py).

A row per run of pages of a day: the day, the pages (typed in the transcript's own page numbers, "141-170", or
by place in the day when they aren't known; the runs around a run typed make room for it), optionally its
place in the day ("1–20 of 85": Settings.excerpt_show_place), a box per firm (ticked = that firm ordered the
run), the pages billed in it and, for each speed offered, what each firm that ordered it pays for it. Under
the table the same prices by the firms ordering together ("A + B: 15 pp., each pays ...") and each firm's
invoice. Every change is kept at once (on the days' jobs) and the main window follows it; a change made there
(an attorney ticked, a speed, Extras...) shows here at once too: the window stays open beside it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from ..excerpts import Day, Run, billed_in, carve, case_days, firms_of, prices, runs_of, store
from ..invoice_calc import fmt
from ..invoice_math import money_exact
from .zoom import z

DAY, PAGES, PLACE = 0, 1, 2  # the first columns; then a box per firm, the pages billed, a price per speed
SHARED_COLOR = "#2563eb"  # a firm's box on a run several firms ordered together


class ExcerptsWindow(QDialog):
    """Who ordered which pages of the case: see the module docstring. load(group) shows the days of an invoice
    (MainWindow keeps it up to date); changed() is called after each change is kept."""

    def __init__(self, s, changed, parent=None):
        """s: the settings (the rate sheet, the speeds, what this window hides); changed: called after a change
        is kept on the jobs, so the main window shows it."""
        super().__init__(parent)
        self.s, self.changed = s, changed
        self.setWindowTitle("Excerpts: who ordered which pages")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.days: list[Day] = []
        self.runs: list[Run] = []
        self.firms = []
        self.speeds: list[str] = []
        self._loading = False
        self._pending = None  # the jobs to show once the cell being typed in is left (see load)
        lay = QVBoxLayout(self)
        intro = QLabel("A row per run of pages. Type a run in the transcript's page numbers (141-170) and the rows "
                       "around it make room; tick the firms that ordered it. Pages several firms ordered share the "
                       "original and the index; each firm pays its own copy. A run nobody ticks is billed to nobody.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)
        opts = QHBoxLayout()
        self.place = QCheckBox("Show each run's place in the day")
        self.place.setChecked(s.excerpt_show_place)
        self.place.setToolTip("Besides the printed page numbers: pages 1–20 of 85")
        self.place.toggled.connect(self._place_toggled)
        opts.addWidget(self.place)
        opts.addSpacing(z(18))
        self.speeds_label = QLabel("Prices for:")
        opts.addWidget(self.speeds_label)
        self.speed_row = QHBoxLayout()
        opts.addLayout(self.speed_row)
        self.speed_boxes: dict[str, QCheckBox] = {}
        opts.addStretch(1)
        lay.addLayout(opts)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemDelegate().closeEditor.connect(self._editor_closed)
        lay.addWidget(self.table, 3)
        row = QHBoxLayout()
        for text, slot, tip in (("Split run", self._split, "Cuts the run chosen in two, at its middle"),
                                ("Join with the run above", self._join,
                                 "The run chosen becomes part of the run above it (with its firms)"),
                                ("Every firm ordered this day", self._whole_day,
                                 "The day of the run chosen: one run, ordered by every firm ticked on it")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.problem = QLabel("")
        self.problem.setObjectName("problem")
        self.problem.setWordWrap(True)
        row.addWidget(self.problem, 1)
        lay.addLayout(row)
        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.summary, 2)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self.resize(z(1000), z(640))

    # ------------------------------------------------------------------ showing
    def load(self, group: list) -> None:
        """Shows the days of an invoice (jobs) as they are now. While a cell is being typed in, the table is
        left alone (it would close the editor) but marked stale: it is shown again before the typing is kept,
        or when the editor closes, so runs the main window has changed meanwhile aren't written back."""
        if self.table.state() == QAbstractItemView.EditingState:
            self._pending = list(group)
            return
        self._fill(group)

    def clear(self) -> None:
        """Forgets the days shown and closes the window (their jobs are gone, or have no days to show: New job,
        a job removed, a job without a transcript)."""
        self._pending = None
        self.days, self.runs, self.firms = [], [], []
        self._loading = True
        self.table.setRowCount(0)
        self._loading = False
        self.summary.setText("")
        self.problem.setText("")
        self.close()

    def _editor_closed(self, *_):
        """The editor closed without a change kept (Escape, or the same text): a reload skipped while typing
        is done now."""
        if self._pending is not None:
            QTimer.singleShot(0, self._reload_pending)

    def _reload_pending(self) -> None:
        """Shows the jobs load() left waiting, unless a cell is being typed in again by now."""
        if self._pending is not None and self.table.state() != QAbstractItemView.EditingState:
            self._fill(self._pending)

    def _fill(self, group: list) -> None:
        """Reads the days (those with pages), the firms and the runs of these jobs afresh and shows them."""
        self._pending = None
        self.days = [d for d in case_days(group) if d.pages > 0]
        self.firms = firms_of(group)
        self.runs = [r for d in self.days for r in runs_of(d, self.firms)]
        self._show()

    def _show(self) -> None:
        """Fills the table and the prices under it from self.runs."""
        p = prices(self.days, self.runs, self.s)
        self.speeds = p.speeds
        self._speed_boxes(p.speeds)
        shown = [sp for sp in p.speeds if sp not in self.s.excerpt_hidden_speeds]
        firms = self.firms
        heads = ["Day", "Pages", "Place in the day"] + [a.name or a.firm for a in firms] + ["Pages billed"] + [
            f"Each pays · {sp}" for sp in shown]
        self._loading = True
        t = self.table
        keep = t.currentRow()
        t.clear()
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        for c, a in enumerate(firms):
            t.horizontalHeaderItem(3 + c).setToolTip(" - ".join(x for x in (a.name, a.firm) if x))
        t.setRowCount(len(self.runs))
        first = 3 + len(firms)
        for r, run in enumerate(self.runs):
            day = run.day
            new_day = r == 0 or self.runs[r - 1].day is not day
            self._cell(r, DAY, day.label if new_day else "", False)
            pages = self._cell(r, PAGES, day.span(run.start, run.end), day.splittable)
            pages.setToolTip(f"Type a run of {day.label}'s pages, e.g. {day.span(1, min(day.pages, 20))}"
                             if day.splittable else "This job covers several days: it is ordered whole")
            self._cell(r, PLACE, day.place(run.start, run.end), False)
            for c, a in enumerate(firms):
                it = QTableWidgetItem("")
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                on = a.key() in run.keys
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                if on and len(run.keys) > 1:
                    it.setBackground(QColor(SHARED_COLOR).lighter(185))
                    it.setToolTip(f"Ordered together by {len(run.keys)} firms: they share these pages")
                t.setItem(r, 3 + c, it)
            self._cell(r, first, str(billed_in(day, run)) if run.keys else "0 (nobody ordered it)", False)
            each = p.runs.get(id(run), {})
            for k, sp in enumerate(shown):
                text = money_exact(each[sp]) if sp in each else ""
                if text and len(run.keys) > 1:
                    text += f"  (÷ {len(run.keys)})"
                self._cell(r, first + 1 + k, text, False)
        t.setColumnHidden(PLACE, not self.s.excerpt_show_place)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(PAGES, QHeaderView.Interactive)
        t.setColumnWidth(PAGES, max(t.columnWidth(PAGES), z(110)))
        if 0 <= keep < t.rowCount():
            t.selectRow(keep)
        self._loading = False
        self.summary.setText(self._summary(p, shown))

    def _cell(self, r: int, c: int, text: str, editable: bool) -> QTableWidgetItem:
        """Puts a text cell in the table (only a run's Pages can be typed in) and returns it."""
        it = QTableWidgetItem(text)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        it.setFlags(flags | Qt.ItemIsEditable if editable else flags)
        self.table.setItem(r, c, it)
        return it

    def _summary(self, p, shown: list[str]) -> str:
        """The prices under the table: by the firms ordering together, then each firm's invoice."""
        names = {a.key(): a.name or a.firm for a in self.firms}
        if not shown:
            return "<i>Tick a speed above to see the prices.</i>"
        lines = ["<b>The pages by who ordered them together</b>"]
        for keys, pages, each in p.groups:
            who = " + ".join(names.get(k, k) for k in keys)
            alone = len(keys) == 1
            costs = " · ".join(f"{sp} {money_exact(each[sp])}" for sp in shown if sp in each)
            lines.append(f"{who}{' only' if alone else ''}: {pages} pp. - "
                         + (f"costs {costs}" if alone else f"each of the {len(keys)} pays {costs}"))
        nobody = sum(billed_in(r.day, r) for r in self.runs if not r.keys)
        if nobody:
            lines.append(f"Nobody: {nobody} pp. (not billed)")
        if p.firms:
            lines.append("<br><b>Each firm's invoice</b> (rounded up to the cent)")
            for key, amounts in p.firms:
                costs = " · ".join(f"{sp} {fmt(amounts[sp])}" for sp in shown if sp in amounts)
                lines.append(f"{names.get(key, key)}: {costs}")
        return "<br>".join(lines)

    def _speed_boxes(self, speeds: list[str]) -> None:
        """A box per speed offered: unticked hides its prices (kept in Settings.excerpt_hidden_speeds)."""
        if list(self.speed_boxes) == speeds:
            return
        while self.speed_row.count():
            w = self.speed_row.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.speed_boxes = {}
        for sp in speeds:
            cb = QCheckBox(sp)
            cb.setChecked(sp not in self.s.excerpt_hidden_speeds)
            cb.toggled.connect(self._speeds_toggled)
            self.speed_row.addWidget(cb)
            self.speed_boxes[sp] = cb

    def _speeds_toggled(self, _=None) -> None:
        hidden = [sp for sp, cb in self.speed_boxes.items() if not cb.isChecked()]
        # (a speed hidden that this case doesn't offer stays hidden for the next case that does)
        self.s.excerpt_hidden_speeds = hidden + [x for x in self.s.excerpt_hidden_speeds if x not in self.speed_boxes]
        self._save_settings()
        self._show()

    def _place_toggled(self, on: bool) -> None:
        self.s.excerpt_show_place = on
        self._save_settings()
        self.table.setColumnHidden(PLACE, not on)

    def _save_settings(self) -> None:
        """Saves a choice of what is shown; a failure is let go (only that choice would be lost)."""
        try:
            self.s.save()
        except OSError:
            pass

    # ------------------------------------------------------------------ changing
    def _day_runs(self, day: Day) -> list[Run]:
        """The runs of one day, in order."""
        return [r for r in self.runs if r.day is day]

    def _keep(self, day: Day, runs: list[Run]) -> None:
        """A day's new runs: kept on its job, shown, and passed on to the main window."""
        store(day, runs, self.firms)
        i = next(k for k, r in enumerate(self.runs) if r.day is day)
        self.runs = [r for r in self.runs if r.day is not day]
        self.runs[i:i] = runs
        self.problem.setText("")
        self._show()
        self.changed()

    def _item_changed(self, it: QTableWidgetItem) -> None:
        """A run typed, or a firm ticked or unticked."""
        if self._loading:
            return
        r, c = it.row(), it.column()
        if not 0 <= r < len(self.runs):
            return
        run = self.runs[r]
        text, on = it.text(), it.checkState() == Qt.Checked
        key = self.firms[c - 3].key() if 3 <= c < 3 + len(self.firms) else None
        if self._pending is not None:
            # The main window changed the days while this was typed: the change goes to the runs as they are
            # now (the table is shown again first), not to the old ones, which would undo the main window's.
            job, start, end = run.day.job, run.start, run.end
            self._fill(self._pending)
            run = next((x for x in self.runs if x.day.job is job and (x.start, x.end) == (start, end)), None)
            if run is None or (c != PAGES and key not in {a.key() for a in self.firms}):
                self.problem.setText("The table changed while you typed: please do it again.")
                return
        day = run.day
        runs = self._day_runs(day)
        i = runs.index(run)
        if c == PAGES:
            try:
                a, b = day.parse(text)
                runs = carve(runs, i, a, b)
            except ValueError as e:
                self.problem.setText(f"⚠ {e}")
                self._show()  # back to the run as it was
                return
            self._keep(day, runs)
        elif key is not None:
            keys = set(run.keys) - {key} | ({key} if on else set())
            run.keys = [a.key() for a in self.firms if a.key() in keys]
            self._keep(day, runs)

    def _chosen(self) -> Run | None:
        """The run of the row chosen; None, after asking for one, when no row is."""
        r =self.table.currentRow()
        if not 0 <= r < len(self.runs):
            self.problem.setText("Choose a run in the table first.")
            return None
        return self.runs[r]

    def _split(self) -> None:
        """Split run: the run chosen becomes two at its middle ("1-20" -> "1-10", "11-20"), both with its firms."""
        run = self._chosen()
        if run is None:
            return
        if run.end == run.start or not run.day.splittable:
            self.problem.setText("This run can't be cut in two.")
            return
        runs = self._day_runs(run.day)
        mid = (run.start + run.end) // 2
        self._keep(run.day, carve(runs, runs.index(run), run.start, mid))

    def _join(self) -> None:
        """Join with the run above: one run of both their pages, ordered by the firms of the run above."""
        run = self._chosen()
        if run is None:
            return
        runs = self._day_runs(run.day)
        i = runs.index(run)
        if i == 0:
            self.problem.setText("The first run of a day has none above it.")
            return
        above = runs[i - 1]
        self._keep(run.day, runs[:i - 1] + [Run(run.day, above.start, run.end, list(above.keys))] + runs[i + 1:])

    def _whole_day(self) -> None:
        """Every firm ordered this day: the day of the run chosen becomes one run, ordered by every firm that
        ordered any of its runs (by the attorneys ticked on its job when none did)."""
        run = self._chosen()
        if run is None:
            return
        day = run.day
        keys = list(dict.fromkeys(k for r in self._day_runs(day) for k in r.keys)) or list(day.job.ticked_keys())
        self._keep(day, [Run(day, 1, day.pages, keys)])
