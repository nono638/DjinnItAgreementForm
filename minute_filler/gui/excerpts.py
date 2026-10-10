"""The Excerpts window: who ordered which pages of the whole case, as a table (see excerpts.py).

A row per run of pages of a day: its date and weekday (in italics on the day's later runs), the pages (typed in
the transcript's own page numbers, "141-170", or by place in the day when they aren't known; the runs around a
run typed make room for it), optionally its place in the day ("1–20 of 85": Settings.excerpt_show_place), a box
per firm (firms_of: one column for a firm, however many of its attorneys appear; ticked = that firm ordered the
run; the boxes of each set of firms ordering together have a colour of their own, the colour of that set's line
under the table), the pages billed in it and, for each speed offered, what each firm that ordered it pays for
it. Under the table the same prices by the firms ordering together ("A + B: 15 pp., each pays ...") and each
firm's invoice. Split run cuts a run in two; Remove run (or the Delete key) gives a run's pages to the run above it
(excerpts.remove: every page stays in one run); Remove this day's excerpts makes a day one run again, Remove all
excerpts every day of the case. A right-click on a run offers the same. Every change is kept at once (on the days'
jobs) and the main window follows it; a change made there (an attorney ticked, a speed, Peripherals...) shows here at
once too: the window stays open beside it.

Each firm orders its pages at a speed (Job.speeds, and a run's own in Job.portions; see excerpts.py). The row of
boxes above the table sets a firm's speed for all its pages of the case (_firm_speed_boxes); a right-click on a
firm's box of a run sets the speed of that run alone ("Smith Law on this run at…", _run_menu). A ticked box of a
run whose firms ordered at set speeds says the speed and what the firm pays for the run ("Daily · $345.00");
firms that ordered a run together must commit to one (until then it reads "Expedite? · $98.00", priced at the
agreement form's speed, in amber). Only the runs of a firm billed alone, choosing from its invoice, are priced at
each speed offered: the "Prices for" boxes and their columns are hidden while there is none.
"""
from __future__ import annotations

import re
from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..excerpts import (
    Day, Run, billed_in, carve, case_days, firms_of, prices, remove, runs_of, store, whole_day,
)
from ..invoice import own_words, shared_words
from ..invoice_calc import fmt
from ..invoice_math import money_exact
from ..rates import speed_key
from .widgets import QuietCombo
from .zoom import z

DATE, WEEKDAY, PAGES, PLACE = 0, 1, 2, 3  # the first columns
FIRST_FIRM = 4  # then a box per firm, the pages billed and a price per speed
# A colour for each set of firms ordering together (in the order listed under the table): their boxes are
# tinted with it (see-through, so it works on the light and the dark theme) and their line starts with it
GROUP_COLORS = ("#2563eb", "#16a34a", "#ea580c", "#9333ea", "#0d9488", "#db2777", "#ca8a04", "#dc2626")
TINT = 80  # how opaque a box's tint is (of 255)
AMBER = "#d97706"  # (a firm's box without the speed it must commit to: the warnings' amber, light and dark)
VARIES = "(varies)"  # the Speed box of a firm whose days or runs are at different speeds


def weekdays(label: str) -> str:
    """The weekday of the date(s) in a day's label: '9/28/2026' -> 'Monday'; '6/3/2026, 6/4/2026' (a job of
    several days) -> 'Wed, Thu'; '' when it holds no date."""
    found = []
    for m, d, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", label or ""):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    if len(found) == 1:
        return found[0].strftime("%A")
    return ", ".join(x.strftime("%a") for x in found)


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
        self.group: list = []  # the jobs shown (the days of an invoice)
        self.days: list[Day] = []
        self.runs: list[Run] = []
        self.firms = []
        self.speeds: list[str] = []
        self._loading = False
        self._pending = None  # the jobs to show once the cell being typed in is left (see load)
        lay = QVBoxLayout(self)
        intro = QLabel("A row per run of pages. Type a run in the transcript's page numbers (141-170) and the rows "
                       "around it make room; tick the firms that ordered it. Remove run (or Delete) gives a run's "
                       f"pages to the run above; right-click a run for more. Pages several firms ordered split "
                       f"{shared_words(s)}; each firm pays for its own {own_words(s)}. A run nobody ticks is billed "
                       "to nobody. Each colour is one set of firms ordering together, as listed under the table. "
                       "Firms ordering the same pages commit to a speed: set each firm's above the table, or one "
                       "run's by right-clicking its box. At different speeds the original is billed once, at the "
                       "fastest, and each firm pays its share at its own speed.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)
        speeds = QHBoxLayout()  # a firm's speed for all its pages of the case (Job.speeds), a box per firm
        self.firm_speeds_label = QLabel("Speed ordered (all its pages):")
        self.firm_speeds_label.setToolTip("The speed each firm ordered its pages at. A firm ordering pages "
                                          "together with another commits to one; a firm alone may choose from "
                                          "its invoice. A run at another speed: right-click its box.")
        speeds.addWidget(self.firm_speeds_label)
        self.firm_speed_row = QHBoxLayout()
        speeds.addLayout(self.firm_speed_row)
        speeds.addStretch(1)
        self.firm_speed_boxes: dict[str, QuietCombo] = {}
        lay.addLayout(speeds)
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        # Delete removes the run chosen, only while the table itself has the focus: in a cell being typed in, Delete
        # deletes text (the editor has the focus then; _remove checks too)
        QShortcut(QKeySequence.Delete, self.table, self._remove, context=Qt.WidgetShortcut)
        lay.addWidget(self.table, 3)
        row = QHBoxLayout()
        for text, slot, tip in (("Split run", self._split, "Cuts the run chosen in two, at its middle"),
                                ("Remove run", self._remove,
                                 "The run chosen goes: its pages join the run above it, with that run's firms (the "
                                 "run below, for a day's first run). Delete does it too"),
                                ("Remove this day's excerpts", self._whole_day,
                                 "The day of the run chosen: one run of every page, ordered by every firm that "
                                 "ordered any of it"),
                                ("Remove all excerpts", self._remove_all,
                                 "Every day of the case: one run of every page, ordered by every firm that ordered "
                                 "any of it (asks first)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.problem = QLabel("")
        self._speeds_warning = ""  # (the speeds warning shown in it, taken away once the speeds are fixed)
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
        self.group, self.days, self.runs, self.firms = [], [], [], []
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
        self.group = list(group)
        self.days = [d for d in case_days(group) if d.pages > 0]
        self.firms = firms_of(group)
        self.runs = [r for d in self.days for r in runs_of(d, self.firms)]
        self._show()

    def _show(self) -> None:
        """Fills the table, the firms' speed boxes above it, and the prices and the speeds warning under it, from
        self.runs."""
        p = prices(self.days, self.runs, self.s)
        self.speeds = p.speeds
        self._speed_boxes(p.speeds)
        self._firm_speed_boxes()
        # "Prices for" and its "Each pays · <speed>" columns price the runs whose firms choose from their
        # invoices: hidden while every run is priced at the speeds its firms ordered
        choice = bool(p.runs) or not p.ordered
        self.speeds_label.setVisible(choice)
        for cb in self.speed_boxes.values():
            cb.setVisible(choice)
        shown = [sp for sp in p.speeds if sp not in self.s.excerpt_hidden_speeds] if choice else []
        firms = self.firms
        heads = ["Date", "Weekday", "Pages", "Place in the day"] + [a.label() for a in firms] + [
            "Pages billed"] + [f"Each pays · {sp}" for sp in shown]
        # a colour per set of firms ordering together (those at set speeds: per set of firms and speeds)
        sets = [keys for keys, _, _ in p.groups] + [pairs for pairs, _, _ in p.ordered_groups]
        colour = {keys: GROUP_COLORS[i % len(GROUP_COLORS)] for i, keys in enumerate(sets)}
        self._loading = True
        t = self.table
        keep = t.currentRow()
        t.clear()
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        for c, a in enumerate(firms):
            t.horizontalHeaderItem(FIRST_FIRM + c).setToolTip(" - ".join(x for x in (a.name, a.firm) if x))
        t.setRowCount(len(self.runs))
        first = FIRST_FIRM + len(firms)
        for r, run in enumerate(self.runs):
            day = run.day
            new_day = r == 0 or self.runs[r - 1].day is not day
            for c, text in ((DATE, day.label), (WEEKDAY, weekdays(day.label))):
                it = self._cell(r, c, text, False)
                if not new_day:  # a later run of the same day: its date, in italics
                    font = it.font()
                    font.setItalic(True)
                    it.setFont(font)
                    it.setToolTip("The same day as the run above")
            pages = self._cell(r, PAGES, day.span(run.start, run.end), day.splittable)
            pages.setToolTip(f"Type a run of {day.label}'s pages, e.g. {day.span(1, min(day.pages, 20))}"
                             if day.splittable else "This job covers several days: it is ordered whole")
            self._cell(r, PLACE, day.place(run.start, run.end), False)
            at = p.ordered.get(id(run))  # (key -> (speed, what it pays): firms at the speeds they ordered)
            group = tuple((k, at[k][0]) for k in run.keys) if at else tuple(run.keys)
            for c, a in enumerate(firms):
                it = QTableWidgetItem("")
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                on = a.key() in run.keys
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                if on and group in colour:
                    tint = QColor(colour[group])
                    tint.setAlpha(TINT)
                    it.setBackground(tint)
                    it.setToolTip(f"Ordered together by {len(run.keys)} firms: they share these pages"
                                  if len(run.keys) > 1 else "Ordered by this firm alone")
                if on and at and a.key() in at:
                    speed, pays = at[a.key()]
                    if run.speed(a.key()):
                        it.setText(f"{speed} · {money_exact(pays)}")
                    else:  # (it must commit to one: priced at the agreement form's until then)
                        it.setText(f"{speed}? · {money_exact(pays)}")
                        it.setForeground(QColor(AMBER))
                        it.setToolTip(f"No speed set: these pages are ordered with another firm, so this one "
                                      f"commits to a speed (Generate asks). Priced at {speed}, the agreement "
                                      "form's, until then. Right-click to set it, or set it above the table.")
                t.setItem(r, FIRST_FIRM + c, it)
            self._cell(r, first, str(billed_in(day, run)) if run.keys else "0 (nobody ordered it)", False)
            each = p.runs.get(id(run), {})
            for k, sp in enumerate(shown):
                text = money_exact(each[sp]) if sp in each else ""
                if text and len(run.keys) > 1:
                    text += f"  (÷ {len(run.keys)})"
                self._cell(r, first + 1 + k, text, False)
        t.setColumnHidden(PLACE, not self.s.excerpt_show_place)
        t.resizeColumnsToContents()
        for c in range(FIRST_FIRM, first):  # (a box and its text, "Immediate · $72.50", sized to the pixel came
            t.setColumnWidth(c, t.columnWidth(c) + z(10))  # out "Immediate · …": the box's margin isn't counted)
        t.horizontalHeader().setSectionResizeMode(PAGES, QHeaderView.Interactive)
        t.setColumnWidth(PAGES, max(t.columnWidth(PAGES), z(110)))
        if 0 <= keep < t.rowCount():
            t.selectRow(keep)
        self._loading = False
        self.summary.setText(self._summary(p, shown))
        held = [x for d in self.days for x in d.job.speed_problems(self.s)]
        warned, self._speeds_warning = self._speeds_warning, "⚠ " + held[0] if held else ""
        if held:  # (three speeds on the same pages: the invoice waits; a speed the rate sheet lacks: Generate stops)
            self.problem.setText(self._speeds_warning)
        elif warned and self.problem.text() == warned:  # (fixed since, by the strip or a right-click)
            self.problem.setText("")

    def _cell(self, r: int, c: int, text: str, editable: bool) -> QTableWidgetItem:
        """Puts a text cell in the table (only a run's Pages can be typed in) and returns it."""
        it = QTableWidgetItem(text)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        it.setFlags(flags | Qt.ItemIsEditable if editable else flags)
        self.table.setItem(r, c, it)
        return it

    def _summary(self, p, shown: list[str]) -> str:
        """The prices under the table: by the firms ordering together (at the speeds they ordered, when set:
        "Counsel & Counsel (Daily) + Smith Law (Immediate): 60 pp. - Counsel & Counsel pays $345.00, Smith Law
        $435.00 (one original, at Immediate)"), then each firm's invoice."""
        names = {a.key(): a.label() for a in self.firms}
        if not shown and not p.ordered:
            return "<i>Tick a speed above to see the prices.</i>"
        lines = ["<b>The pages by who ordered them together</b> (each with its colour in the table)"]
        for i, (keys, pages, each) in enumerate(p.groups):
            who = " + ".join(names.get(k, k) for k in keys)
            alone = len(keys) == 1
            costs = " · ".join(f"{sp} {money_exact(each[sp])}" for sp in shown if sp in each)
            swatch = f'<span style="color:{GROUP_COLORS[i % len(GROUP_COLORS)]}">■</span> '
            lines.append(f"{swatch}{who}{' only' if alone else ''}: {pages} pp. - "
                         + (f"costs {costs}" if alone else f"each of the {len(keys)} pays {costs}"))
        order = {sp.name: i for i, sp in enumerate(self.s.sheet().speeds)}  # (cheapest first: the last, fastest)
        for i, (pairs, pages, each) in enumerate(p.ordered_groups, len(p.groups)):
            who = " + ".join(f"{names.get(k, k)} ({sp})" for k, sp in pairs)
            swatch = f'<span style="color:{GROUP_COLORS[i % len(GROUP_COLORS)]}">■</span> '
            if len(pairs) == 1:
                lines.append(f"{swatch}{who} only: {pages} pp. - costs {money_exact(each[pairs[0][0]])}")
                continue
            pays = ", ".join(f"{names.get(k, k)} {'pays ' if n == 0 else ''}{money_exact(each[k])}"
                             for n, (k, _) in enumerate(pairs))
            kinds = list(dict.fromkeys(sp for _, sp in pairs))
            fastest = max(kinds, key=lambda sp: order.get(sp, -1))
            how = f" (one original, at {fastest})" if len(kinds) > 1 else ""
            lines.append(f"{swatch}{who}: {pages} pp. - {pays}{how}")
        nobody = sum(billed_in(r.day, r) for r in self.runs if not r.keys)
        if nobody:
            lines.append(f"Nobody: {nobody} pp. (not billed)")
        if p.firms:
            lines.append("<br><b>Each firm's invoice</b> (rounded up to the cent)")
            for key, amounts in p.firms:
                # (the speeds offered, as ticked above; an invoice of the speed the firm ordered: that one, ticked
                # or not)
                costs = " · ".join(f"{sp} {fmt(amounts[sp])}" for sp in amounts
                                   if key in p.committed or sp in shown or sp not in p.speeds)
                lines.append(f"{names.get(key, key)}: {costs}")
        return "<br>".join(lines)

    def _firm_speed_boxes(self) -> None:
        """The row above the table: a box per firm with the speed it ordered all its pages of the case at
        (Job.speeds of each day it ordered on; VARIES when they differ, or a run has its own), "Chooses on the
        invoice" for none. Built afresh with the table; choosing one sets it on every day (_firm_speed_chosen)."""
        while self.firm_speed_row.count():
            w = self.firm_speed_row.takeAt(0).widget()
            if w is not None:  # hidden at once: shown twice in a row, a box never laid out stayed over the window
                w.hide()       # (640 x 480 at its top left) until the event loop deleted it
                w.deleteLater()
        self.firm_speed_boxes = {}
        sheet = self.s.sheet()
        speeds = [sp.name for sp in sheet.speeds]
        for a in self.firms:
            k = a.key()
            mine = {r.speed(k) for r in self.runs if k in r.keys}
            if not mine:
                continue  # (it orders nothing of the case)
            box = QuietCombo()
            if len(mine) > 1:
                box.addItem(VARIES, None)
            box.addItem("Chooses on the invoice", "")
            for sp in speeds:
                box.addItem(sp, sp)
            now = next(iter(mine)) if len(mine) == 1 else None
            found = sheet.find(now) if now else None  # ("Expedited" is the sheet's "Expedite")
            if now and found is None:  # (a speed the rate sheet doesn't have: kept, and said, as in the card)
                box.addItem(f"{now} (not on the rate sheet)", now)
            box.setCurrentIndex(max(0, box.findData(found.name if found else now)) if now is not None else 0)
            box.setToolTip(f"The speed {a.label()} ordered all its pages of the case at")
            box.activated.connect(lambda _i, key=k, b=box: self._firm_speed_chosen(key, b.currentData()))
            label = QLabel(a.label())
            self.firm_speed_row.addWidget(label)
            self.firm_speed_row.addWidget(box)
            self.firm_speed_row.addSpacing(z(12))
            self.firm_speed_boxes[k] = box
        self.firm_speeds_label.setVisible(bool(self.firm_speed_boxes))

    def _firm_speed_chosen(self, key: str, speed) -> None:
        """A firm's speed for all its pages of the case (the row above the table): set on every day it ordered
        on, its runs' own speeds cleared (Job.set_speed); "" = chooses from its invoice. The table is shown again
        after the box's own signal (it is built afresh with the table)."""
        if speed is None:
            return
        for day in self.days:
            if any(key in r.keys for r in self._day_runs(day)):
                day.job.set_speed(key, speed)
        QTimer.singleShot(0, self._speeds_kept)

    def _speeds_kept(self) -> None:
        """After a speed is set: the days read again and shown, and the main window told."""
        self._fill(self.group)
        self.changed()

    def _speed_boxes(self, speeds: list[str]) -> None:
        """A box per speed offered: unticked hides its prices (kept in Settings.excerpt_hidden_speeds)."""
        if list(self.speed_boxes) == speeds:
            return
        while self.speed_row.count():
            w = self.speed_row.takeAt(0).widget()
            if w is not None:
                w.hide()  # (at once, as in _firm_speed_boxes)
                w.deleteLater()
        self.speed_boxes = {}
        for sp in speeds:
            cb = QCheckBox(sp)
            cb.setChecked(sp not in self.s.excerpt_hidden_speeds)
            cb.toggled.connect(self._speeds_toggled)
            self.speed_row.addWidget(cb)
            self.speed_boxes[sp] = cb

    def _speeds_toggled(self, _=None) -> None:
        """A speed's box ticked or unticked: its prices shown or hidden, and the choice saved."""
        hidden = [sp for sp, cb in self.speed_boxes.items() if not cb.isChecked()]
        # (a speed hidden that this case doesn't offer stays hidden for the next case that does)
        self.s.excerpt_hidden_speeds = hidden + [x for x in self.s.excerpt_hidden_speeds if x not in self.speed_boxes]
        self._save_settings()
        self._show()

    def _place_toggled(self, on: bool) -> None:
        """Show each run's place in the day: the column shown or hidden, and the choice saved."""
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
        store(day, runs, self.firms, self.s)
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
        key = self.firms[c - FIRST_FIRM].key() if FIRST_FIRM <= c < FIRST_FIRM + len(self.firms) else None
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
        r = self.table.currentRow()
        if not 0 <= r < len(self.runs):
            self.problem.setText("Choose a run in the table first.")
            return None
        return self.runs[r]

    def _select(self, job, page: int) -> None:
        """Chooses the row of the run that holds a page of a day (its job; the table may have been read afresh
        since, with new days and runs)."""
        row = next((k for k, r in enumerate(self.runs) if r.day.job is job and r.start <= page <= r.end), None)
        if row is not None:
            self.table.selectRow(row)

    def _split(self) -> None:
        """Split run: the run chosen becomes two at its middle ("1-20" -> "1-10", "11-20"), both with its firms."""
        run = self._chosen()
        if run is None:
            return
        if not run.day.splittable:
            self.problem.setText("This job covers several days: it is ordered whole.")
            return
        if run.end == run.start:
            self.problem.setText("This run can't be cut in two.")
            return
        runs = self._day_runs(run.day)
        mid = (run.start + run.end) // 2
        self._keep(run.day, carve(runs, runs.index(run), run.start, mid))

    def _remove(self) -> None:
        """Remove run (or Delete): the run chosen goes, its pages to the run above it (excerpts.remove: the run
        below for a day's first run), and the row now holding its first page is chosen. Nothing while a cell
        is being typed in (Delete there deletes the text)."""
        if self.table.state() == QAbstractItemView.EditingState:
            return
        run = self._chosen()
        if run is None:
            return
        day = run.day
        if not day.splittable:
            self.problem.setText("This job covers several days: it is ordered whole.")
            return
        runs = self._day_runs(day)
        try:
            runs = remove(runs, runs.index(run))
        except ValueError as e:  # (the day's only run)
            self.problem.setText(str(e))
            return
        job, page = day.job, run.start
        self._keep(day, runs)
        self._select(job, page)
        if not any(r.keys for r in runs):
            self.problem.setText("Nobody orders this day now: tick a firm.")

    def _whole_day(self) -> None:
        """Remove this day's excerpts: the day of the run chosen becomes one run of all its pages, ordered by
        every firm that ordered any of its runs, or by the firms ticked on its job when none did
        (excerpts.whole_day)."""
        run = self._chosen()
        if run is None:
            return
        day = run.day
        self._keep(day, whole_day(day, self._day_runs(day), day.job.ticked_keys(), self.firms))
        self._select(day.job, 1)

    def _remove_all(self) -> None:
        """Remove all excerpts, after asking: every day of the case with excerpts (Job.portions) becomes one
        run of all its pages, ordered by every firm that ordered any of it (excerpts.whole_day). The days are
        kept, then the table shown and the main window told once."""
        days = [d for d in self.days if d.splittable and d.job.portions is not None]
        if not days:
            self.problem.setText("There are no excerpts to remove: every day is one run.")
            return
        if QMessageBox.question(self, "Remove all excerpts", "Every day of the case becomes one run, ordered by "
                                "every firm that ordered any of it. Remove all excerpts?") != QMessageBox.Yes:
            return
        # Looked up again: a read or an AI answer that ended while the question was open shows the days afresh
        # (new Day objects), and the days found above would match none of them
        days = [d for d in self.days if d.splittable and d.job.portions is not None]
        if not days:
            self.problem.setText("The table changed meanwhile: there are no excerpts left to remove.")
            return
        r = self.table.currentRow()
        chosen = (self.runs[r].day.job, self.runs[r].start) if 0 <= r < len(self.runs) else None
        change = {id(d) for d in days}
        runs = []
        for day in self.days:
            own = self._day_runs(day)
            if id(day) in change:
                own = whole_day(day, own, day.job.ticked_keys(), self.firms)
                store(day, own, self.firms, self.s)
            runs += own
        self.runs = runs
        self.problem.setText("")
        self._show()
        self.changed()
        if chosen is not None:
            self._select(*chosen)

    def _run_menu(self, run: Run, key: str | None = None) -> tuple[QMenu, list]:
        """The right-click menu of a run, and the slot of each of its actions (by the action's data): Split run
        (not for a run that can't be cut), Remove run (not for a day's only run), Remove this day's excerpts and
        Remove all excerpts; on the box of a firm that ordered the run (key), "<firm> on this run at…" with the
        rate sheet's speeds (the run's own ticked), and "<firm>: same as its other pages" when the run's speed
        isn't the day's and the day has other runs."""
        menu = QMenu(self)
        slots = []
        for text, slot, on in (("Split run", self._split, run.day.splittable and run.end > run.start),
                               ("Remove run", self._remove, len(self._day_runs(run.day)) > 1),
                               ("Remove this day's excerpts", self._whole_day, True),
                               ("Remove all excerpts", self._remove_all, True)):
            act = menu.addAction(text)
            act.setEnabled(on)
            act.setData(len(slots))
            slots.append(slot)
        if key is not None and key in run.keys:
            name = next((a.label() for a in self.firms if a.key() == key), key)
            menu.addSeparator()
            sub = menu.addMenu(f"{name} on this run at…")
            for sp in self.s.sheet().speeds:
                act = sub.addAction(sp.name)
                act.setCheckable(True)
                act.setChecked(bool(run.speed(key)) and speed_key(run.speed(key)) == sp.key)  # ("Expedited")
                act.setData(len(slots))
                slots.append(lambda k=key, name=sp.name: self._set_run_speed(k, name))
            day_speed = run.day.job.speeds.get(key, "")
            if run.speed(key) != day_speed and len(self._day_runs(run.day)) > 1:
                act = menu.addAction(f"{name}: same as its other pages" + (f" ({day_speed})" if day_speed else ""))
                act.setData(len(slots))
                slots.append(lambda k=key: self._set_run_speed(k, None))
        return menu, slots

    def _set_run_speed(self, key: str, speed: str | None) -> None:
        """The speed firm `key` ordered the run chosen at (None: the same as its other pages of the day, Job.speeds),
        kept on its day (excerpts.store keeps a run's own speed only where it differs)."""
        run = self._chosen()
        if run is None or key not in run.keys:
            return
        day = run.day
        runs = self._day_runs(day)
        if speed is None:
            if day.job.speeds.get(key):
                run.speeds[key] = day.job.speeds[key]
            else:
                run.speeds.pop(key, None)
        else:
            run.speeds[key] = speed
        self._keep(day, runs)
        self._select(day.job, run.start)

    def _context_menu(self, pos) -> None:
        """A right-click on a run: its row is chosen, then its menu (_run_menu) shown, with the speeds of the firm
        whose box was clicked. The table can be read afresh while the menu is open (the main window changed the
        days): what is picked acts on the same run found again, or, when it is gone, says so."""
        row = self.table.rowAt(pos.y())
        if not 0 <= row < len(self.runs):
            return
        self.table.selectRow(row)
        run = self.runs[row]
        col = self.table.columnAt(pos.x())  # (a firm's box: its speed on this run too)
        key = self.firms[col - FIRST_FIRM].key() if FIRST_FIRM <= col < FIRST_FIRM + len(self.firms) else None
        menu, slots = self._run_menu(run, key)
        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if act is None or not act.isEnabled():
            return
        job, start, end = run.day.job, run.start, run.end
        row = next((k for k, r in enumerate(self.runs) if r.day.job is job and (r.start, r.end) == (start, end)),
                   None)
        if row is None:
            self.problem.setText("The table changed meanwhile: please choose the run again.")
            return
        self.table.selectRow(row)
        slots[act.data()]()
