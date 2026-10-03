"""The Records window: the invoice ledger (totals, filters, mark paid, amounts corrected after the PDF was
changed) and the history of everything made. Each table has many columns (INVOICE_COLS, ACTIVITY_COLS); the
user picks which are shown (Columns..., or a right-click on the headings), and the choice is kept in
Settings.records_columns. Records can be deleted to the trash (restored for 30 days; the files are not
touched); the Trash button shows what is in it."""
from __future__ import annotations

import calendar
import html
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..deliver import ledger_for
from ..fill import safe_filename
from ..invoice_calc import fmt, money
from ..log import error as log_error
from ..rates import parse_amount
from ..records import KINDS, TRASH_DAYS, Activity, Invoice, _month_name, gone_for_good, summarize, us_date
from ..settings import Settings
from .widgets import open_path, plural, refill_combo, show_save_error
from .zoom import z

SUM_COLS = ["", "Invoices", "Billed", "Paid", "Outstanding"]
STATUS_FILTERS = [("All", ""), ("Unpaid", "open"), ("Paid", "paid"), ("Void", "void")]
SEARCH_TIP = ("Words found anywhere in a record, in any order: a firm, an attorney, a case,\n"
              "an index number (712345/2021 or 712345-2021), an invoice number, a judge…")
STATUS_COLORS = {"open": "#d97706", "paid": "#16a34a", "void": "#8a8797"}  # readable on light and dark


@dataclass(frozen=True)
class Col:
    """A column of a Records table: its key (kept in Settings.records_columns), heading, the value shown for a
    record, whether it is shown until the user chooses, how it sorts ("text", "num", "money" or "date") and,
    for "date", the ISO date it sorts by. trash: shown only while the trash is."""
    key: str
    head: str
    get: Callable
    shown: bool = False
    kind: str = "text"
    sort: Callable | None = None
    trash: bool = False


def _status(i: Invoice) -> str:
    """'Unpaid', 'Paid (Expedite)' or 'Void'."""
    text = i.status.title() if i.status != "open" else "Unpaid"
    return text + (f" ({i.paid_speed})" if i.status == "paid" and i.paid_speed else "")


def _ordered(i: Invoice) -> str:
    """The speed ordered: the one paid for, or the only one offered; "" while the attorney hasn't chosen."""
    return i.paid_speed if i.status == "paid" and i.paid_speed else next(iter(i.amounts)) if len(i.amounts) == 1 else ""


def _bill_to(i: Invoice) -> str:
    """'Counsel & Counsel (Alex B. Counsel)', or whichever of the two there is."""
    return i.client if not i.firm else f"{i.firm} ({i.bill_to})" if i.bill_to else i.firm


INVOICE_COLS = [
    Col("paid", "Paid", lambda i: "", True),
    Col("no", "No.", lambda i: i.invoice_no, True),
    Col("date", "Date", lambda i: us_date(i.created), True, "date", lambda i: i.created),
    Col("case", "Case", lambda i: i.case_name, True),
    Col("index_no", "Index No.", lambda i: i.index_no),
    Col("court", "Court", lambda i: i.court),
    Col("part", "Part", lambda i: i.part),
    Col("judge", "Judge", lambda i: i.judge),
    Col("dates", "Date(s) of proceeding", lambda i: i.dates),
    Col("bill_to", "Bill to", _bill_to, True),
    Col("firm", "Firm", lambda i: i.firm),
    Col("email", "E-mail", lambda i: i.email),
    Col("pages", "Pages billed", lambda i: i.pages, True, "num"),
    Col("my_pages", "My pages", lambda i: i.my_pages or "", True, "num"),
    Col("transcript_pages", "Transcript pages", lambda i: i.transcript_pages or "", True, "num"),
    Col("reporters", "Reporters", lambda i: i.reporters),
    Col("excerpt", "Excerpt", lambda i: i.excerpt or ("Whole" if i.transcript_pages else ""), True),
    Col("parties", "Parties", lambda i: i.parties, False, "num"),
    Col("speeds", "Speeds offered", lambda i: ", ".join(i.amounts), True),
    Col("offered", "Offered (each party)", Invoice.offered_text),
    Col("email_copy", "E-mailed copy", lambda i: i.email_copy),
    Col("index", "Index", lambda i: i.index),
    Col("billed", "Billed", lambda i: i.billed, True, "money"),
    Col("status", "Status", _status, True),
    Col("ordered", "Speed ordered", _ordered, True),
    Col("amount_paid", "Amount paid", lambda i: i.paid if i.status == "paid" else "", False, "money"),
    Col("paid_date", "Date paid", lambda i: us_date(i.paid_date), True, "date", lambda i: i.paid_date),
    Col("notes", "Notes", lambda i: i.notes),
    Col("file", "File", lambda i: i.file_path),
    Col("deleted", "Deleted on", lambda i: us_date(i.deleted[:10]), True, "date", lambda i: i.deleted, True),
    Col("gone", "Gone for good on", lambda i: gone_for_good(i.deleted), True, "date", lambda i: i.deleted, True),
]
ACTIVITY_COLS = [
    Col("date", "Date", lambda a: us_date(a.ts), True, "date", lambda a: a.ts),
    Col("time", "Time", lambda a: a.ts[11:16], True),
    Col("made", "Made", lambda a: KINDS.get(a.kind, a.kind), True),
    Col("case", "Case", lambda a: a.case_name, True),
    Col("index_no", "Index No.", lambda a: a.index_no, True),
    Col("dates", "Date(s) of proceeding", lambda a: a.dates),
    Col("judge", "Judge", lambda a: a.judge),
    Col("part", "Part", lambda a: a.part),
    Col("attorney", "Attorney", lambda a: a.attorney, True),
    Col("firm", "Firm", lambda a: a.firm, True),
    Col("pages", "Pages", lambda a: a.pages or "", True, "num"),
    Col("my_pages", "My pages", lambda a: a.my_pages or "", True, "num"),
    Col("transcript_pages", "Transcript pages", lambda a: a.transcript_pages or "", True, "num"),
    Col("invoice_no", "Invoice No.", lambda a: a.invoice_no, True),
    Col("file", "File", lambda a: a.file_path, True),
    Col("deleted", "Deleted on", lambda a: us_date(a.deleted[:10]), True, "date", lambda a: a.deleted, True),
    Col("gone", "Gone for good on", lambda a: gone_for_good(a.deleted), True, "date", lambda a: a.deleted, True),
]
TABLES = {"invoices": INVOICE_COLS, "activity": ACTIVITY_COLS}


def col_index(cols: list[Col], key: str) -> int:
    """The position of a column by its key (the tests and the window find columns this way, not by number)."""
    return next(i for i, c in enumerate(cols) if c.key == key)


class _Item(QTableWidgetItem):
    """A cell that sorts by the value kept under UserRole + 1 (a number, or an ISO date) when there is one."""

    def __lt__(self, other):
        a, b = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


def _item(text, align_right: bool = False, data=None, sort=None) -> QTableWidgetItem:
    """A read-only table cell showing text (None shows as blank). data, when given, is kept under UserRole;
    sort (a number or an ISO date) under UserRole + 1, to sort by."""
    it = _Item("" if text is None else str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if data is not None:
        it.setData(Qt.UserRole, data)
    if sort is not None:
        it.setData(Qt.UserRole + 1, sort)
    return it


def _money_item(d) -> QTableWidgetItem:
    """A right-aligned cell showing an amount as "$1,234.50", with the plain number kept under UserRole + 1."""
    return _item(fmt(d), True, sort=float(d))


def _cell(col: Col, record) -> QTableWidgetItem:
    """The cell of one column for one record, sorting as the column says."""
    value = col.get(record)
    if col.kind == "money":
        return _money_item(value) if value != "" else _item("", True, sort=-1.0)
    if col.kind == "num":
        return _item(value, True, sort=float(value) if value != "" else -1.0)
    if col.kind == "date":
        return _item(value, sort=(col.sort(record) if col.sort else "") or "")
    return _item(value)


def _table(cols: list[str]) -> QTableWidget:
    """An empty table with these column headings: whole-row selection (several rows with Ctrl or Shift),
    striped rows, no row numbers, sorted by clicking a heading (not sorted until then)."""
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection)
    t.setAlternatingRowColors(True)
    t.setWordWrap(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
    return t


class PaidDialog(QDialog):
    """Asks which speed was paid for, how much and when. The amount starts at the price of the chosen speed."""

    def __init__(self, inv: Invoice, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Invoice {inv.invoice_no} paid")
        self.inv = inv
        f = QFormLayout(self)
        f.addRow(QLabel(f"<b>{html.escape(inv.case_name or inv.invoice_no)}</b><br>{html.escape(inv.client)}"))
        self.speed = QComboBox()
        for sp, amt in inv.amounts.items():
            self.speed.addItem(f"{sp}  —  {fmt(money(amt))}", sp)
        self.speed.setCurrentIndex(max(0, self.speed.findData(inv.billed_speed)))
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.when = QDateEdit(QDate.currentDate())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("M/d/yyyy")
        f.addRow("Paid for:", self.speed)
        f.addRow("Amount received:", self.amount)
        f.addRow("Date paid:", self.when)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._speed_changed()

    def _speed_changed(self, _=None):
        """Puts the chosen speed's price in the amount box."""
        self.amount.setText(str(money(self.inv.amounts.get(self.speed.currentData(), "0"))))

    def values(self) -> tuple[str, str, str]:
        """(speed, amount, date paid), e.g. ("Expedite", "125.00", "2026-09-30"), as Ledger.mark_paid takes them."""
        d = self.when.date()
        return self.speed.currentData() or "", str(money(self.amount.text())), \
            date(d.year(), d.month(), d.day()).isoformat()


class AmountsDialog(QDialog):
    """Change amounts…: a money box per speed of the invoice, starting at what the records have. OK is
    refused while a box is blank or not a number."""

    def __init__(self, inv: Invoice, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Invoice {inv.invoice_no}: amounts")
        f = QFormLayout(self)
        f.addRow(QLabel(f"<b>{html.escape(inv.case_name or inv.invoice_no)}</b><br>{html.escape(inv.client)}"))
        note = QLabel("Change these when you corrected an amount in the invoice PDF.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        f.addRow(note)
        self.boxes: dict[str, QLineEdit] = {}
        for sp, amt in inv.amounts.items():
            box = QLineEdit(str(money(amt)))
            box.setPlaceholderText("0.00")
            self.boxes[sp] = box
            f.addRow(f"{sp}:", box)
        self.error = QLabel("")
        self.error.setObjectName("problem")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        f.addRow(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def problem(self) -> str:
        """What is wrong with the amounts typed ("" when nothing is): each must be an amount and nothing else
        (see rates.parse_amount)."""
        bad = [sp for sp, box in self.boxes.items() if parse_amount(box.text()) is None]
        return f"Type an amount for {', '.join(bad)}, such as 63.00 or $1,250.00." if bad else ""

    def accept(self):
        """Closes with OK only when every box holds an amount; otherwise says which one doesn't."""
        why = self.problem()
        self.error.setText(why)
        self.error.setVisible(bool(why))
        if not why:
            super().accept()

    def values(self) -> dict[str, str]:
        """{speed: "63.00"}, as Ledger.set_amounts takes them."""
        return {sp: str(parse_amount(box.text()) or money(box.text())) for sp, box in self.boxes.items()}


class RecordsWindow(QDialog):
    """The Records window. Two tabs: Invoices (filters, totals, paid ticks, by-firm and by-month sums) and
    Everything made (each file the app made). Each has the Columns... choice and the Trash. The window does not
    load anything until reload() is called."""

    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.ledger = ledger_for(s)
        self.shown: list[Invoice] = []
        self.act_shown: dict[int, Activity] = {}  # the rows of everything made shown, by id
        self.setWindowTitle("Records")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(z(1180), z(760))
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_invoices(), "Invoices")
        self.tabs.addTab(self._build_activity(), "Everything made")

        bottom = QHBoxLayout()
        for label, slot, tip in (
                ("Report (HTML)…", self.export_html, "A report of the invoices shown, opened in your browser"),
                ("Excel…", self.export_xlsx, "Invoices, everything made, and totals by firm and month"),
                ("CSV…", self.export_csv, "invoices.csv and activity.csv"),
                ("Open records folder", self.open_folder, "Where the CSV copies are kept up to date")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        root.addLayout(bottom)
        # In a dialog every button presses on Enter by default: Enter in a search box opened the Columns menu
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        self.apply_columns("invoices")
        self.apply_columns("activity")

    # ------------------------------------------------------------ building
    def _filters(self, lay: QHBoxLayout, with_client: bool) -> tuple[QComboBox, QComboBox, QComboBox | None]:
        """Adds Year and Month boxes (and a Firm box when with_client) to lay. The year and firm lists stay
        empty until reload() fills them from the records."""
        year, month = QComboBox(), QComboBox()
        month.addItem("All months", 0)
        for m in range(1, 13):
            month.addItem(calendar.month_name[m], m)
        lay.addWidget(QLabel("Year:"))
        lay.addWidget(year)
        lay.addWidget(QLabel("Month:"))
        lay.addWidget(month)
        client = None
        if with_client:
            client = QComboBox()
            client.setMinimumWidth(z(220))
            lay.addWidget(QLabel("Firm:"))
            lay.addWidget(client)
        return year, month, client

    def _search(self, lay: QHBoxLayout, placeholder: str, on_change) -> tuple[QLineEdit, QCheckBox, QCheckBox]:
        """Adds a search box with its Regex and Fuzzy boxes (both unticked: plain words) to a filter row;
        on_change runs when any of them changes. The two boxes exclude each other."""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        edit.setToolTip(SEARCH_TIP)
        lay.addWidget(edit, 1)
        regex, fuzzy = QCheckBox("Regex"), QCheckBox("Fuzzy")
        regex.setToolTip("Search with a regular expression, e.g.  ^Smith  or  2026-00(1|2)  (any case)")
        fuzzy.setToolTip("Also find near misses and misspellings: \"Counsle\" finds \"Counsel & Counsel\"")
        for box, other in ((regex, fuzzy), (fuzzy, regex)):
            box.toggled.connect(lambda on, o=other: o.setChecked(False) if on else None)
            box.toggled.connect(on_change)
            lay.addWidget(box)
        edit.textChanged.connect(on_change)
        return edit, regex, fuzzy

    @staticmethod
    def _how(regex: QCheckBox, fuzzy: QCheckBox) -> str:
        """The search mode the boxes ask for: "regex", "fuzzy" or plain "words" (see records.matcher)."""
        return "regex" if regex.isChecked() else "fuzzy" if fuzzy.isChecked() else "words"

    @staticmethod
    def _search_problem(edit: QLineEdit, problem: str) -> None:
        """Marks a search box whose regular expression can't be read (amber, with the reason as its tooltip)."""
        from .widgets import repolish
        edit.setProperty("review", bool(problem))
        edit.setToolTip(problem or SEARCH_TIP)
        repolish(edit)

    def _table_buttons(self, lay: QHBoxLayout, which: str) -> QPushButton:
        """Adds Columns… and the Trash toggle to a filter row; returns the Trash button."""
        cols = QPushButton("Columns…")
        cols.setToolTip("Choose the columns shown (also: right-click a column heading)")
        cols.clicked.connect(lambda: self._columns_menu(which, cols.mapToGlobal(cols.rect().bottomLeft())))
        lay.addWidget(cols)
        trash = QPushButton("🗑 Trash")
        trash.setCheckable(True)
        trash.setToolTip(f"Show what was deleted: it can be restored for {TRASH_DAYS} days, then it is gone for good")
        lay.addWidget(trash)
        return trash

    def _build_invoices(self) -> QWidget:
        """The Invoices tab: filters, the four total tiles, and the Invoices / By firm / By month tables."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        fl = QHBoxLayout()
        self.i_year, self.i_month, self.i_client = self._filters(fl, True)
        self.i_status = QComboBox()
        for label, key in STATUS_FILTERS:
            self.i_status.addItem(label, key)
        fl.addWidget(QLabel("Status:"))
        fl.addWidget(self.i_status)
        self.i_text, self.i_regex, self.i_fuzzy = self._search(
            fl, "Search: firm, attorney, case, index no., invoice no.…", self.show_invoices)
        self.i_trash = self._table_buttons(fl, "invoices")
        v.addLayout(fl)
        for c in (self.i_year, self.i_month, self.i_client, self.i_status):
            c.currentIndexChanged.connect(self.show_invoices)
        self.i_trash.toggled.connect(lambda on: self._trash_toggled(on, self.i_year))

        tiles = QHBoxLayout()
        self.kpi: dict[str, QLabel] = {}
        for key, label in (("billed", "Billed"), ("paid", "Paid"), ("outstanding", "Outstanding"),
                           ("count", "Invoices")):
            f = QFrame()
            f.setObjectName("card")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(14, 8, 14, 8)
            cap = QLabel(label)
            cap.setObjectName("muted")
            val = QLabel("—")
            val.setObjectName("kpiValue")
            if key in ("paid", "outstanding"):
                val.setProperty("tone", "ok" if key == "paid" else "warn")
            fv.addWidget(cap)
            fv.addWidget(val)
            self.kpi[key] = val
            tiles.addWidget(f)
        v.addLayout(tiles)
        self.trash_note = QLabel(f"The trash: deleted records, kept {TRASH_DAYS} days in case you want them back. "
                                 "Right-click to restore one (the PDF files were never deleted).")
        self.trash_note.setObjectName("muted")
        self.trash_note.setWordWrap(True)
        self.trash_note.setVisible(False)
        v.addWidget(self.trash_note)

        inner = QTabWidget()
        self.inv_table = _table([c.head for c in INVOICE_COLS])
        self.inv_table.itemChanged.connect(self._paid_toggled)
        self.inv_table.itemDoubleClicked.connect(lambda it: self._open_invoice(self._inv_at(it.row())))
        self.inv_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.inv_table.customContextMenuRequested.connect(self._invoice_menu)
        self._header_menu(self.inv_table, "invoices")
        self.inv_table.setToolTip("Tick Paid when an invoice is paid · double-click opens the PDF · "
                                  "right-click for more · Delete moves it to the trash")
        QShortcut(QKeySequence.Delete, self.inv_table, activated=lambda: self._delete_selected("invoices"))
        self.firm_table = _table(["Firm / attorney"] + SUM_COLS[1:])
        self.month_table = _table(["Month"] + SUM_COLS[1:])
        inner.addTab(self.inv_table, "Invoices")
        inner.addTab(self.firm_table, "By firm")
        inner.addTab(self.month_table, "By month")
        self.inv_inner = inner
        v.addWidget(inner, 1)
        return w

    def _build_activity(self) -> QWidget:
        """The Everything made tab: kind, year and month filters, a search box and the table of files."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        fl = QHBoxLayout()
        self.a_kind = QComboBox()
        self.a_kind.addItem("Everything", "")
        for k, label in KINDS.items():
            self.a_kind.addItem(label + "s", k)
        fl.addWidget(QLabel("Show:"))
        fl.addWidget(self.a_kind)
        self.a_year, self.a_month, _ = self._filters(fl, False)
        self.a_text, self.a_regex, self.a_fuzzy = self._search(
            fl, "Search: firm, attorney, case, index no., file…", self.show_activity)
        self.a_trash = self._table_buttons(fl, "activity")
        v.addLayout(fl)
        for c in (self.a_kind, self.a_year, self.a_month):
            c.currentIndexChanged.connect(self.show_activity)
        self.a_trash.toggled.connect(lambda on: self._trash_toggled(on, self.a_year))
        self.a_count = QLabel("")
        self.a_count.setObjectName("muted")
        v.addWidget(self.a_count)
        self.act_table = _table([c.head for c in ACTIVITY_COLS])
        self.act_table.itemDoubleClicked.connect(lambda it: self._open_activity(it.row()))
        self.act_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.act_table.customContextMenuRequested.connect(self._activity_menu)
        self._header_menu(self.act_table, "activity")
        self.act_table.setToolTip("Double-click opens the file · right-click for more · Delete moves it to the trash")
        QShortcut(QKeySequence.Delete, self.act_table, activated=lambda: self._delete_selected("activity"))
        v.addWidget(self.act_table, 1)
        return w

    # ------------------------------------------------------------ columns
    def _table_of(self, which: str) -> QTableWidget:
        return self.inv_table if which == "invoices" else self.act_table

    def _in_trash(self, which: str) -> bool:
        return (self.i_trash if which == "invoices" else self.a_trash).isChecked()

    def columns(self, which: str) -> list[str]:
        """The keys of the columns chosen for a table ("invoices" or "activity"): the user's choice
        (Settings.records_columns), else those shown by default."""
        known = {c.key for c in TABLES[which] if not c.trash}
        chosen = [k for k in self.s.records_columns.get(which, []) if k in known]
        return chosen or [c.key for c in TABLES[which] if c.shown and not c.trash]

    def apply_columns(self, which: str) -> None:
        """Shows the chosen columns of a table (and, in the trash, when each record was deleted)."""
        keep, trash = set(self.columns(which)), self._in_trash(which)
        t = self._table_of(which)
        for i, c in enumerate(TABLES[which]):
            t.setColumnHidden(i, not (c.key in keep or (c.trash and trash)) or (c.key == "paid" and trash))

    def set_columns(self, which: str, keys: list[str] | None) -> None:
        """Keeps a new choice of columns (None: back to the default ones) and shows it."""
        cols = dict(self.s.records_columns)
        if keys is None:
            cols.pop(which, None)
        else:
            cols[which] = [c.key for c in TABLES[which] if c.key in keys and not c.trash]
        self.s.records_columns = cols
        try:
            self.s.save()
        except OSError as e:
            log_error("could not save the columns chosen", e)
        self.apply_columns(which)

    def _columns_menu(self, which: str, at) -> None:
        """The menu of a table's columns, ticked when shown; clicking one shows or hides it."""
        m = QMenu(self)
        chosen = self.columns(which)
        for c in TABLES[which]:
            if c.trash:
                continue
            act = m.addAction(c.head)
            act.setCheckable(True)
            act.setChecked(c.key in chosen)
            act.toggled.connect(lambda on, k=c.key: self.set_columns(
                which, [x for x in self.columns(which) if x != k] + ([k] if on else [])))
        m.addSeparator()
        m.addAction("Back to the usual columns", lambda: self.set_columns(which, None))
        m.exec(at)

    def _header_menu(self, t: QTableWidget, which: str) -> None:
        """Right-click on a table's headings: the columns menu."""
        h = t.horizontalHeader()
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(lambda pos: self._columns_menu(which, h.mapToGlobal(pos)))

    # ------------------------------------------------------------ data
    def reload(self) -> None:
        """Re-reads the records (after new files were made) and keeps the filters chosen."""
        self.ledger = ledger_for(self.s)  # the records folder may have been changed in Settings
        years = self.ledger.years()
        this_year = date.today().year
        year_items = [("All years", 0)] + [(str(y), y) for y in sorted(set(years) | {this_year}, reverse=True)]
        for combo in (self.i_year, self.a_year):
            keep = combo.currentData()
            refill_combo(combo, year_items, this_year if keep is None else keep)
        refill_combo(self.i_client, [("All firms", "")] + [(c, c) for c in self.ledger.clients()],
                     self.i_client.currentData() or "")
        self.show_invoices()
        self.show_activity()

    def _trash_toggled(self, on: bool = False, year: QComboBox | None = None) -> None:
        """The Trash button: lists what was deleted instead of the records, and back. The trash shows every
        year (year: that tab's Year box), as what was deleted may be from an older one."""
        if on and year is not None:
            year.blockSignals(True)  # (listed again below)
            year.setCurrentIndex(max(0, year.findData(0)))  # "All years"
            year.blockSignals(False)
        trash = self.i_trash.isChecked()
        self.trash_note.setVisible(trash)
        self.inv_inner.setTabEnabled(1, not trash)
        self.inv_inner.setTabEnabled(2, not trash)
        for which in TABLES:
            self.apply_columns(which)
        self.show_invoices()
        self.show_activity()

    def _invoice_filters(self) -> dict:
        """The Invoices tab's filters as keyword arguments for Ledger.invoices ("All" is None or "")."""
        return dict(year=self.i_year.currentData() or None, month=self.i_month.currentData() or None,
                    client=self.i_client.currentData() or "", status=self.i_status.currentData() or "",
                    text=self.i_text.text().strip(), how=self._how(self.i_regex, self.i_fuzzy))

    def show_invoices(self, _=None) -> None:
        """Lists the invoices that match the filters (those in the trash with the Trash button) and updates the
        totals and the by-firm and by-month tabs."""
        trash = self.i_trash.isChecked()
        try:
            self.shown = self.ledger.invoices(**self._invoice_filters(), trash=trash)
            self._search_problem(self.i_text, "")
        except ValueError as e:  # a regular expression still being typed ("(smith")
            self.shown = []
            self._search_problem(self.i_text, str(e))
        t = self.inv_table
        t.blockSignals(True)  # setting the Paid ticks must not look like the user clicking them
        t.setSortingEnabled(False)
        t.setRowCount(0)
        status_col = col_index(INVOICE_COLS, "status")
        for inv in self.shown:
            r = t.rowCount()
            t.insertRow(r)
            paid = _Item()
            paid.setFlags((paid.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            if inv.status == "void" or trash:
                paid.setFlags(paid.flags() & ~Qt.ItemIsEnabled)
            paid.setCheckState(Qt.Checked if inv.status == "paid" else Qt.Unchecked)
            paid.setData(Qt.UserRole, inv.invoice_no)
            paid.setData(Qt.UserRole + 1, 1 if inv.status == "paid" else 0)
            t.setItem(r, 0, paid)
            for c, col in enumerate(INVOICE_COLS[1:], 1):
                t.setItem(r, c, _cell(col, inv))
            t.item(r, status_col).setForeground(QColor(STATUS_COLORS.get(inv.status, STATUS_COLORS["void"])))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()
        for key, widest in (("case", 300), ("bill_to", 260), ("file", 360), ("reporters", 200), ("excerpt", 240)):
            i = col_index(INVOICE_COLS, key)
            t.setColumnWidth(i, min(t.columnWidth(i), z(widest)))

        total, by_client, by_month = summarize(self.shown)
        # (no totals of deleted invoices: they would read as money billed)
        self.kpi["billed"].setText("—" if trash else fmt(total.billed))
        self.kpi["paid"].setText("—" if trash else fmt(total.paid))
        self.kpi["outstanding"].setText("—" if trash else fmt(total.outstanding))
        self.kpi["count"].setText("—" if trash else str(total.count))
        self._fill_summary(self.firm_table, [(k, v) for k, v in by_client.items()], total)
        self._fill_summary(self.month_table, [(_month_name(k), v) for k, v in reversed(list(by_month.items()))],
                           total)

    @staticmethod
    def _fill_summary(t: QTableWidget, rows, total) -> None:
        """Fills a by-firm or by-month table from (name, Summary) rows, with a bold Total row last."""
        t.setRowCount(0)
        for name, sm in rows + [("Total", total)]:
            r = t.rowCount()
            t.insertRow(r)
            for c, it in enumerate([_item(name), _item(sm.count, True), _money_item(sm.billed),
                                    _money_item(sm.paid), _money_item(sm.outstanding)]):
                if name == "Total" and r == len(rows):
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()

    def show_activity(self, _=None) -> None:
        """Lists the files made that match the Everything made filters (those in the trash with the Trash
        button), with a count of each kind."""
        y, m = self.a_year.currentData() or 0, self.a_month.currentData() or 0
        since = until = ""
        if y:
            since, until = f"{y}-{m or 1:02}-01", f"{y}-{m or 12:02}-{calendar.monthrange(y, m or 12)[1]:02}"
        try:
            rows = self.ledger.activity(self.a_kind.currentData() or "", since, until, self.a_text.text().strip(),
                                        trash=self.a_trash.isChecked(), how=self._how(self.a_regex, self.a_fuzzy))
            self._search_problem(self.a_text, "")
        except ValueError as e:  # a regular expression still being typed
            rows = []
            self._search_problem(self.a_text, str(e))
        # A month in every year ("March", All years) is not one date range, so it is filtered here.
        if m and not y:
            rows = [a for a in rows if a.ts[5:7] == f"{m:02}"]
        self.act_shown = {a.id: a for a in rows}
        t = self.act_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        for a in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c, col in enumerate(ACTIVITY_COLS):
                t.setItem(r, c, _cell(col, a))
            t.item(r, 0).setData(Qt.UserRole, a.id)
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()
        for key, widest in (("case", 300), ("file", 420)):
            i = col_index(ACTIVITY_COLS, key)
            t.setColumnWidth(i, min(t.columnWidth(i), z(widest)))
        counts = {k: sum(1 for a in rows if a.kind == k) for k in KINDS}
        self.a_count.setText("  ·  ".join([plural(len(rows), "file")] +
                                          [plural(n, KINDS[k].lower()) for k, n in counts.items()]))

    # ------------------------------------------------------------ actions
    def _inv_at(self, row: int) -> Invoice | None:
        """The invoice shown on this table row, or None (row -1 is below the last row)."""
        it = self.inv_table.item(row, 0)
        no = it.data(Qt.UserRole) if it else None
        return next((i for i in self.shown if i.invoice_no == no), None)

    def _act_at(self, row: int) -> Activity | None:
        """The row of everything made shown on this table row, or None."""
        it = self.act_table.item(row, 0)
        return self.act_shown.get(it.data(Qt.UserRole)) if it else None

    def _selected(self, which: str) -> list:
        """The invoices (or rows of everything made) selected, in table order."""
        t = self._table_of(which)
        rows = sorted({i.row() for i in t.selectedIndexes()})
        get = self._inv_at if which == "invoices" else self._act_at
        return [x for x in (get(r) for r in rows) if x is not None]

    def _open_activity(self, row: int) -> None:
        """Opens the file of a row of everything made."""
        a = self._act_at(row)
        if a is not None and a.file_path:
            open_path(a.file_path)

    def _paid_toggled(self, item: QTableWidgetItem) -> None:
        """A Paid tick was clicked: ask about it once the click is over."""
        if item.column() != 0:
            return
        no, paid = item.data(Qt.UserRole), item.checkState() == Qt.Checked
        # After the click has been dealt with, not in the middle of it: the answer rebuilds this table.
        QTimer.singleShot(0, lambda: self._set_paid(no, paid))

    def _set_paid(self, invoice_no: str, paid: bool) -> None:
        """Marks an invoice paid (asking at which speed) or not paid, then shows the list afresh."""
        try:
            inv = self.ledger.invoice(invoice_no)  # as it is now, whatever the table showed
            if inv is not None and inv.deleted:
                pass  # (in the trash: restore it first)
            elif inv is not None and paid and inv.status != "paid":
                dlg = PaidDialog(inv, self)
                if dlg.exec() == QDialog.Accepted:
                    self.ledger.mark_paid(inv.invoice_no, *dlg.values())
            elif inv is not None and not paid and inv.status == "paid":
                if QMessageBox.question(self, "Not paid", f"Mark invoice {inv.invoice_no} as not paid?") \
                        == QMessageBox.Yes:
                    self.ledger.mark_unpaid(inv.invoice_no)
        except Exception as e:
            log_error("could not update an invoice", e)
            QMessageBox.warning(self, "Could not save", f"{type(e).__name__}: {e}")
        self.show_invoices()

    def _open_invoice(self, inv: Invoice | None) -> None:
        """Opens the invoice's PDF, or says where it was if it has been moved or deleted."""
        if inv is None:
            return
        if inv.file_path and Path(inv.file_path).exists():
            open_path(inv.file_path)
        else:
            QMessageBox.information(self, "File not found", f"The PDF of invoice {inv.invoice_no} is no longer at\n"
                                    f"{inv.file_path or '(unknown)'}")

    def _invoice_menu(self, pos) -> None:
        """The right-click menu on an invoice: open, show in folder, paid / not paid, void / restore, change the
        amounts, notes, delete; in the trash: restore or delete for good."""
        row = self.inv_table.rowAt(pos.y())
        inv = self._inv_at(row)
        if inv is None:
            return
        if not any(i.row() == row for i in self.inv_table.selectedIndexes()):
            self.inv_table.selectRow(row)  # the menu is about the row clicked
        many = self._selected("invoices")
        m = QMenu(self)
        m.addAction("Open the PDF", lambda: self._open_invoice(inv))
        m.addAction("Show in folder", lambda: open_path(str(Path(inv.file_path).parent)) if inv.file_path else None)
        m.addSeparator()
        if inv.deleted:
            self._trash_actions(m, "invoices", many)
        else:
            if inv.status != "paid":
                m.addAction("Mark paid…", lambda: self._set_paid(inv.invoice_no, True))
            else:
                m.addAction("Mark not paid", lambda: self._set_paid(inv.invoice_no, False))
            if inv.status != "void":
                m.addAction("Void (cancelled, not counted)", lambda: self._set_void(inv))
            else:
                m.addAction("Restore (not void)",
                            lambda: (self.ledger.mark_unpaid(inv.invoice_no), self.show_invoices()))
            if inv.amounts:
                m.addAction("Change amounts…", lambda: self._change_amounts(inv))
            m.addAction("Notes…", lambda: self._notes(inv))
            m.addSeparator()
            m.addAction(f"Delete {plural(len(many), 'record')} (to the trash)…",
                        lambda: self._delete_selected("invoices"))
        m.exec(self.inv_table.viewport().mapToGlobal(pos))

    def _activity_menu(self, pos) -> None:
        """The right-click menu on a row of everything made: open the file, show in folder, delete; in the
        trash: restore or delete for good."""
        row = self.act_table.rowAt(pos.y())
        a = self._act_at(row)
        if a is None:
            return
        if not any(i.row() == row for i in self.act_table.selectedIndexes()):
            self.act_table.selectRow(row)
        many = self._selected("activity")
        m = QMenu(self)
        m.addAction("Open the file", lambda: open_path(a.file_path) if a.file_path else None)
        m.addAction("Show in folder", lambda: open_path(str(Path(a.file_path).parent)) if a.file_path else None)
        m.addSeparator()
        if a.deleted:
            self._trash_actions(m, "activity", many)
        else:
            m.addAction(f"Delete {plural(len(many), 'record')} (to the trash)…",
                        lambda: self._delete_selected("activity"))
        m.exec(self.act_table.viewport().mapToGlobal(pos))

    def _trash_actions(self, m: QMenu, which: str, records: list) -> None:
        """Restore and Delete for good, for records in the trash."""
        m.addAction(f"Restore {plural(len(records), 'record')}", lambda: self.restore(which, records))
        m.addAction("Delete for good…", lambda: self.delete_forever(which, records))

    @staticmethod
    def _keys(which: str, records: list) -> dict:
        """The keyword arguments of Ledger.delete / restore / delete_forever for these records."""
        if which == "invoices":
            return {"invoices": [i.invoice_no for i in records]}
        return {"activity": [a.id for a in records]}

    def _delete_selected(self, which: str) -> None:
        """Delete (or the Delete key): the records selected go to the trash, after asking."""
        if self._in_trash(which):
            return
        records = self._selected(which)
        if records:
            self.delete(which, records)

    def delete(self, which: str, records: list, ask: bool = True) -> None:
        """Moves records to the trash (asking first unless ask is False). The PDF files stay where they are."""
        what = plural(len(records), "invoice" if which == "invoices" else "record")
        if ask and QMessageBox.question(
                self, "Delete", f"Move {what} to the trash?\n\nThey can be restored for {TRASH_DAYS} days "
                "(Trash button). The files themselves are not deleted.") != QMessageBox.Yes:
            return
        self._try_change(lambda: self.ledger.delete(**self._keys(which, records)))

    def restore(self, which: str, records: list) -> None:
        """Takes records out of the trash."""
        self._try_change(lambda: self.ledger.restore(**self._keys(which, records)))

    def delete_forever(self, which: str, records: list, ask: bool = True) -> None:
        """Deletes records in the trash for good, after asking."""
        if ask and QMessageBox.question(
                self, "Delete for good", f"Delete {plural(len(records), 'record')} for good? This can't be undone "
                "(the files themselves are not deleted).") != QMessageBox.Yes:
            return
        self._try_change(lambda: self.ledger.delete_forever(**self._keys(which, records)))

    def _try_change(self, fn) -> None:
        """Makes a change to the records, says so if it fails, and shows both lists afresh."""
        try:
            fn()
        except Exception as e:
            log_error("could not change the records", e)
            QMessageBox.warning(self, "Could not save", f"{type(e).__name__}: {e}")
        self.reload()

    def _change_amounts(self, inv: Invoice) -> None:
        """Change amounts…: what each speed of the invoice costs, as corrected in its PDF; the list and the
        totals are shown afresh."""
        inv = self.ledger.invoice(inv.invoice_no) or inv  # as it is now, whatever the table showed
        dlg = AmountsDialog(inv, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.ledger.set_amounts(inv.invoice_no, dlg.values())
        except Exception as e:
            log_error("could not change an invoice's amounts", e)
            QMessageBox.warning(self, "Could not save", f"{type(e).__name__}: {e}")
        self.show_invoices()

    def _set_void(self, inv: Invoice) -> None:
        """Voids the invoice after asking: it stays listed but leaves the totals."""
        if QMessageBox.question(self, "Void invoice", f"Void invoice {inv.invoice_no}? It stays in the list but is "
                                "no longer counted in the totals.") == QMessageBox.Yes:
            self.ledger.void(inv.invoice_no)
            self.show_invoices()

    def _notes(self, inv: Invoice) -> None:
        """Edits the invoice's free-text notes."""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, f"Invoice {inv.invoice_no}", "Notes:", text=inv.notes)
        if ok:
            self.ledger.set_notes(inv.invoice_no, text)
            self.show_invoices()

    # ------------------------------------------------------------ exports
    def _filter_title(self) -> str:
        """A title for an export that names the filters, e.g. "Invoices - March 2026 - Dana Smith (unpaid)"."""
        f = self._invoice_filters()
        bits = []
        if f["month"]:
            bits.append(calendar.month_name[f["month"]])
        if f["year"]:
            bits.append(str(f["year"]))
        title = "Invoices" + (" - " + " ".join(bits) if bits else "")
        if f["client"]:
            title += f" - {f['client']}"
        if f["status"]:
            title += f" ({dict((k, l) for l, k in STATUS_FILTERS)[f['status']].lower()})"
        return title

    def _ask_path(self, title: str, name: str, filt: str) -> Path | None:
        """A Save As box that starts in the records folder with name filled in. None when cancelled."""
        folder = self.s.records_folder()
        folder.mkdir(parents=True, exist_ok=True)
        p, _ = QFileDialog.getSaveFileName(self, title, str(folder / safe_filename(name)), filt)
        return Path(p) if p else None

    def _to_export(self) -> list[Invoice]:
        """The invoices a report or workbook lists: those shown, or with the Trash on, the invoices (not the
        deleted ones) the same filters match: a report never passes deleted invoices off as billed."""
        return self.ledger.invoices(**self._invoice_filters()) if self.i_trash.isChecked() else self.shown

    def _search_blocked(self) -> bool:
        """True (after saying so) when the Invoices tab's search can't be read (a regular expression half
        typed, Fuzzy without its library: the box is amber, see _search_problem): a report of it would list
        nothing, or fail."""
        if not self.i_text.property("review"):
            return False
        QMessageBox.information(self, "Fix the search first", f"Fix the search first: {self.i_text.toolTip()}")
        return True

    def export_html(self) -> None:
        """Saves a report of the invoices shown (see _to_export) as a web page and opens it."""
        if self._search_blocked():
            return
        title = self._filter_title()
        p = self._ask_path("Save the report", f"{title}.html", "Web page (*.html)")
        if p:
            self._try(lambda: open_path(str(self.ledger.export_html(p, self._to_export(), title))))

    def export_xlsx(self) -> None:
        """Saves the invoices shown (see _to_export; and everything made, and totals) as an Excel workbook and
        opens it."""
        if self._search_blocked():
            return
        p = self._ask_path("Export to Excel", f"{self._filter_title()}.xlsx", "Excel workbook (*.xlsx)")
        if p:
            self._try(lambda: open_path(str(self.ledger.export_xlsx(p, self._to_export()))))

    def export_csv(self) -> None:
        """Writes invoices.csv and activity.csv to a folder the user picks and opens it."""
        folder = self.s.records_folder()
        folder.mkdir(parents=True, exist_ok=True)
        d = QFileDialog.getExistingDirectory(self, "Folder for invoices.csv and activity.csv", str(folder))
        if d:
            self._try(lambda: (self.ledger.export_csv(Path(d)), open_path(d)))

    def open_folder(self) -> None:
        """Brings the CSV copies in the records folder up to date, then opens the folder."""
        folder = self.s.records_folder()
        self._try(lambda: (self.ledger.export_csv(folder), open_path(str(folder))))

    def _try(self, fn) -> None:
        """Runs an export and shows why it failed, if it did."""
        try:
            fn()
        except Exception as e:
            show_save_error(self, e, "Could not export the records")
