"""The Records window: the invoice ledger (totals, filters, mark paid, amounts corrected after the PDF was
changed) and the history of everything made."""
from __future__ import annotations

import calendar
import html
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..deliver import ledger_for
from ..fill import safe_filename
from ..invoice_calc import fmt, money
from ..log import error as log_error
from ..rates import parse_amount
from ..records import KINDS, Invoice, _month_name, summarize, us_date
from ..settings import Settings
from .widgets import open_path, plural, refill_combo, show_save_error

INV_COLS = ["Paid", "No.", "Date", "Case", "Index No.", "Bill to", "Pages", "Offered (each party)", "Billed",
            "Status", "Date paid"]
ACT_COLS = ["Date", "Time", "Made", "Case", "Index No.", "Attorney", "Firm", "Pages", "Invoice No.", "File"]
SUM_COLS = ["", "Invoices", "Billed", "Paid", "Outstanding"]
STATUS_FILTERS = [("All", ""), ("Unpaid", "open"), ("Paid", "paid"), ("Void", "void")]
STATUS_COLORS = {"open": "#d97706", "paid": "#16a34a", "void": "#8a8797"}  # readable on light and dark


def _item(text, align_right: bool = False, data=None) -> QTableWidgetItem:
    """A read-only table cell showing text (None shows as blank). data, when given, is kept under UserRole."""
    it =QTableWidgetItem("" if text is None else str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def _money_item(d) -> QTableWidgetItem:
    """A right-aligned cell showing an amount as "$1,234.50", with the plain number kept under UserRole + 1."""
    it =_item(fmt(d), True)
    it.setData(Qt.UserRole + 1, float(d))
    return it


def _table(cols: list[str]) -> QTableWidget:
    """An empty table with these column headings: whole-row selection, striped rows, no row numbers."""
    t =QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    t.setWordWrap(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
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
        d =self.when.date()
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
    Everything made (each file the app made). The window does not load anything until reload() is called."""

    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.ledger = ledger_for(s)
        self.shown: list[Invoice] = []
        self.setWindowTitle("Records")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(1180, 760)
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
            client.setMinimumWidth(220)
            lay.addWidget(QLabel("Firm:"))
            lay.addWidget(client)
        return year, month, client

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
        self.i_text = QLineEdit()
        self.i_text.setPlaceholderText("Search case, index no., attorney…")
        self.i_text.setClearButtonEnabled(True)
        fl.addWidget(self.i_text, 1)
        v.addLayout(fl)
        for c in (self.i_year, self.i_month, self.i_client, self.i_status):
            c.currentIndexChanged.connect(self.show_invoices)
        self.i_text.textChanged.connect(self.show_invoices)

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

        inner = QTabWidget()
        self.inv_table = _table(INV_COLS)
        self.inv_table.itemChanged.connect(self._paid_toggled)
        self.inv_table.itemDoubleClicked.connect(lambda it: self._open_invoice(self._inv_at(it.row())))
        self.inv_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.inv_table.customContextMenuRequested.connect(self._invoice_menu)
        self.inv_table.setToolTip("Tick Paid when an invoice is paid · double-click opens the PDF · "
                                  "right-click for more")
        self.firm_table = _table(["Firm / attorney"] + SUM_COLS[1:])
        self.month_table = _table(["Month"] + SUM_COLS[1:])
        inner.addTab(self.inv_table, "Invoices")
        inner.addTab(self.firm_table, "By firm")
        inner.addTab(self.month_table, "By month")
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
        self.a_text = QLineEdit()
        self.a_text.setPlaceholderText("Search case, index no., attorney, firm…")
        self.a_text.setClearButtonEnabled(True)
        fl.addWidget(self.a_text, 1)
        v.addLayout(fl)
        for c in (self.a_kind, self.a_year, self.a_month):
            c.currentIndexChanged.connect(self.show_activity)
        self.a_text.textChanged.connect(self.show_activity)
        self.a_count = QLabel("")
        self.a_count.setObjectName("muted")
        v.addWidget(self.a_count)
        self.act_table = _table(ACT_COLS)
        self.act_table.itemDoubleClicked.connect(
            lambda it: open_path(self.act_table.item(it.row(), len(ACT_COLS) - 1).text()))
        self.act_table.setToolTip("Double-click opens the file")
        v.addWidget(self.act_table, 1)
        return w

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

    def _invoice_filters(self) -> dict:
        """The Invoices tab's filters as keyword arguments for Ledger.invoices ("All" is None or "")."""
        return dict(year=self.i_year.currentData() or None, month=self.i_month.currentData() or None,
                    client=self.i_client.currentData() or "", status=self.i_status.currentData() or "",
                    text=self.i_text.text().strip())

    def show_invoices(self, _=None) -> None:
        """Lists the invoices that match the filters and updates the totals and the by-firm and by-month tabs."""
        self.shown = self.ledger.invoices(**self._invoice_filters())
        t = self.inv_table
        t.blockSignals(True)  # setting the Paid ticks must not look like the user clicking them
        t.setSortingEnabled(False)
        t.setRowCount(0)
        for inv in self.shown:
            r = t.rowCount()
            t.insertRow(r)
            paid = QTableWidgetItem()
            paid.setFlags((paid.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            if inv.status == "void":
                paid.setFlags(paid.flags() & ~Qt.ItemIsEnabled)
            paid.setCheckState(Qt.Checked if inv.status == "paid" else Qt.Unchecked)
            paid.setData(Qt.UserRole, inv.invoice_no)
            t.setItem(r, 0, paid)
            status = inv.status.title() if inv.status != "open" else "Unpaid"
            if inv.status == "paid" and inv.paid_speed:
                status += f" ({inv.paid_speed})"
            cells = [_item(inv.invoice_no), _item(us_date(inv.created)), _item(inv.case_name), _item(inv.index_no),
                     _item(inv.client if not inv.firm else f"{inv.firm} ({inv.bill_to})" if inv.bill_to else inv.firm),
                     _item(inv.pages, True), _item(inv.offered_text()), _money_item(inv.billed), _item(status),
                     _item(us_date(inv.paid_date))]
            cells[8].setForeground(QColor(STATUS_COLORS.get(inv.status, STATUS_COLORS["void"])))
            for c, it in enumerate(cells, 1):
                t.setItem(r, c, it)
        t.blockSignals(False)
        t.resizeColumnsToContents()
        t.setColumnWidth(3, min(t.columnWidth(3), 300))
        t.setColumnWidth(5, min(t.columnWidth(5), 260))

        total, by_client, by_month = summarize(self.shown)
        self.kpi["billed"].setText(fmt(total.billed))
        self.kpi["paid"].setText(fmt(total.paid))
        self.kpi["outstanding"].setText(fmt(total.outstanding))
        self.kpi["count"].setText(str(total.count))
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
        """Lists the files made that match the Everything made filters, with a count of each kind."""
        y, m = self.a_year.currentData() or 0, self.a_month.currentData() or 0
        since = until = ""
        if y:
            since, until = f"{y}-{m or 1:02}-01", f"{y}-{m or 12:02}-{calendar.monthrange(y, m or 12)[1]:02}"
        rows = self.ledger.activity(self.a_kind.currentData() or "", since, until, self.a_text.text().strip())
        # A month in every year ("March", All years) is not one date range, so it is filtered here.
        if m and not y:
            rows = [a for a in rows if a.ts[5:7] == f"{m:02}"]
        t = self.act_table
        t.setRowCount(0)
        for a in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c, it in enumerate([_item(us_date(a.ts)), _item(a.ts[11:16]), _item(KINDS.get(a.kind, a.kind)),
                                    _item(a.case_name), _item(a.index_no), _item(a.attorney), _item(a.firm),
                                    _item(a.pages or "", True), _item(a.invoice_no), _item(a.file_path)]):
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        t.setColumnWidth(3, min(t.columnWidth(3), 300))
        counts = {k: sum(1 for a in rows if a.kind == k) for k in KINDS}
        self.a_count.setText("  ·  ".join([plural(len(rows), "file")] +
                                          [plural(n, KINDS[k].lower()) for k, n in counts.items()]))

    # ------------------------------------------------------------ actions
    def _inv_at(self, row: int) -> Invoice | None:
        """The invoice shown on this table row, or None (row -1 is below the last row)."""
        it =self.inv_table.item(row, 0)
        no = it.data(Qt.UserRole) if it else None
        return next((i for i in self.shown if i.invoice_no == no), None)

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
            if inv is not None and paid and inv.status != "paid":
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
        amounts, notes."""
        row = self.inv_table.rowAt(pos.y())
        inv = self._inv_at(row)
        if inv is None:
            return
        m = QMenu(self)
        m.addAction("Open the PDF", lambda: self._open_invoice(inv))
        m.addAction("Show in folder", lambda: open_path(str(Path(inv.file_path).parent)) if inv.file_path else None)
        m.addSeparator()
        if inv.status != "paid":
            m.addAction("Mark paid…", lambda: self._set_paid(inv.invoice_no, True))
        else:
            m.addAction("Mark not paid", lambda: self._set_paid(inv.invoice_no, False))
        if inv.status != "void":
            m.addAction("Void (cancelled, not counted)", lambda: self._set_void(inv))
        else:
            m.addAction("Restore (not void)", lambda: (self.ledger.mark_unpaid(inv.invoice_no), self.show_invoices()))
        if inv.amounts:
            m.addAction("Change amounts…", lambda: self._change_amounts(inv))
        m.addAction("Notes…", lambda: self._notes(inv))
        m.exec(self.inv_table.viewport().mapToGlobal(pos))

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
        f =self._invoice_filters()
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

    def export_html(self) -> None:
        """Saves a report of the invoices shown as a web page and opens it."""
        title = self._filter_title()
        p = self._ask_path("Save the report", f"{title}.html", "Web page (*.html)")
        if p:
            self._try(lambda: open_path(str(self.ledger.export_html(p, self.shown, title))))

    def export_xlsx(self) -> None:
        """Saves the invoices shown (and everything made, and totals) as an Excel workbook and opens it."""
        p = self._ask_path("Export to Excel", f"{self._filter_title()}.xlsx", "Excel workbook (*.xlsx)")
        if p:
            self._try(lambda: open_path(str(self.ledger.export_xlsx(p, self.shown))))

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
