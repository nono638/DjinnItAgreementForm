"""The records' newer columns and the trash: a database made by an older version gets the new columns, a real
invoice records the user's pages, the transcript's, the reporters, the excerpt and what was charged; deleted
records leave every list and copy and can be restored for 30 days, then are gone for good, while their
invoice numbers stay taken. Also the Records window: the columns chosen, kept for next time, and Delete,
Trash and Restore. All names and numbers are made up."""
import csv
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from minute_filler.records import Invoice, Ledger

OLD_SCHEMA = """
CREATE TABLE activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL,
    case_name TEXT, index_no TEXT, dates TEXT, judge TEXT, part TEXT, attorney TEXT, firm TEXT, pages INTEGER,
    file_path TEXT, invoice_no TEXT);
CREATE TABLE invoices (invoice_no TEXT PRIMARY KEY, year INTEGER, seq INTEGER, created TEXT NOT NULL,
    case_name TEXT, index_no TEXT, dates TEXT, judge TEXT, bill_to TEXT, firm TEXT, email TEXT, pages INTEGER,
    parties INTEGER, amounts TEXT NOT NULL, billed_speed TEXT, status TEXT NOT NULL DEFAULT 'open',
    paid_speed TEXT, amount_paid TEXT, paid_date TEXT, file_path TEXT, notes TEXT);
INSERT INTO invoices (invoice_no, year, seq, created, case_name, firm, pages, parties, amounts, billed_speed)
    VALUES ('2026-0007', 2026, 7, '2026-03-01', 'Roe v. Poe', 'Counsel & Counsel', 30, 1, '{"Regular": "129.00"}',
            'Regular');
INSERT INTO activity (ts, kind, case_name, pages) VALUES ('2026-03-01T10:00:00', 'agreement', 'Roe v. Poe', 30);
"""


def inv(no, created="2026-03-02", **kw):
    """A made-up 10-page invoice of Jane Roe v. X.Y. Holding Corporation; kw: other Invoice fields."""
    return Invoice(invoice_no=no, created=created, case_name="Jane Roe v. X.Y. Holding Corporation",
                   firm="Counsel & Counsel", bill_to="Alex B. Counsel", pages=10, amounts={"Regular": "43.00"},
                   billed_speed="Regular", **kw)


def test_an_older_database_gets_the_new_columns(tmp_path):
    path = tmp_path / "records.db"
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA)
    lg = Ledger(path, mirror_dir=tmp_path / "mirror")
    old, = lg.invoices()
    assert old.invoice_no == "2026-0007" and old.my_pages == 0 and old.excerpt == "" and old.deleted == ""
    assert lg.activity()[0].transcript_pages == 0
    from datetime import date
    assert lg.next_invoice_no(today=date(2026, 5, 1))[0] == "2026-0008"
    lg.add_invoice(inv("2026-0008", my_pages=5, transcript_pages=10, reporters="PR 5, DS 5", index="No",
                       email_copy="Yes", excerpt="6/3/2026 pp. 101–105"))
    new = lg.invoice("2026-0008")
    assert (new.my_pages, new.transcript_pages, new.reporters, new.index, new.email_copy) == (
        5, 10, "PR 5, DS 5", "No", "Yes")
    with open(tmp_path / "mirror" / "invoices.csv", encoding="utf-8-sig") as f:
        row = next(r for r in csv.DictReader(f) if r["Invoice No."] == "2026-0008")
    assert row["My pages"] == "5" and row["Transcript pages"] == "10" and row["Excerpt"].startswith("6/3/2026")


def test_two_threads_opening_an_older_database_at_once(tmp_path):
    # the batch and the window open the records together: both used to add the same column ("duplicate column")
    import threading
    for trial in range(10):
        path = tmp_path / f"records{trial}.db"
        with sqlite3.connect(path) as db:
            db.executescript(OLD_SCHEMA)
        start, errors = threading.Barrier(2), []

        def open_it():
            start.wait()
            try:
                Ledger(path)
            except Exception as e:  # noqa: BLE001 - any failure is the bug
                errors.append(e)

        threads = [threading.Thread(target=open_it) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert Ledger(path).invoices()[0].my_pages == 0


class _StaleColumns:
    """A connection that sees the invoices table as it was before "court" was added: as a second program
    would, when it looked just before the first one added the column."""

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db, name)

    def __enter__(self):
        return self.db.__enter__()

    def __exit__(self, *exc):
        return self.db.__exit__(*exc)

    def execute(self, sql, *args):
        if sql == "PRAGMA table_info(invoices)":
            return [r for r in self.db.execute(sql) if r[1] != "court"]
        return self.db.execute(sql, *args)


def test_a_column_another_program_just_added_is_no_error(tmp_path, monkeypatch):
    Ledger(tmp_path / "records.db")  # made, with every column
    real = Ledger._db
    monkeypatch.setattr(Ledger, "_db", lambda self: _StaleColumns(real(self)))
    Ledger(tmp_path / "records.db")  # "court" looks missing, and adding it finds it there: not an error


def test_trash_restore_and_gone_for_good(tmp_path):
    lg = Ledger(tmp_path / "records.db", mirror_dir=tmp_path / "mirror")
    lg.add_invoice(inv("2026-0001"))
    lg.add_invoice(inv("2026-0002"))
    lg.log_activity("invoice", case_name="Roe", invoice_no="2026-0001")
    lg.log_activity("agreement", case_name="Roe")
    agreement = next(a for a in lg.activity() if a.kind == "agreement")

    lg.delete(invoices=["2026-0001"], activity=[agreement.id])
    assert [i.invoice_no for i in lg.invoices()] == ["2026-0002"]
    assert [i.invoice_no for i in lg.invoices(trash=True)] == ["2026-0001"]
    assert lg.activity() == [] and len(lg.activity(trash=True)) == 2  # the invoice's row went with it
    assert lg.invoice("2026-0001").deleted  # still found by its number
    with open(tmp_path / "mirror" / "invoices.csv", encoding="utf-8-sig") as f:
        assert [r["Invoice No."] for r in csv.DictReader(f)] == ["2026-0002"]

    lg.restore(invoices=["2026-0001"])
    assert len(lg.invoices()) == 2 and [a.kind for a in lg.activity()] == ["invoice"]

    # 31 days in the trash: gone for good, but the number is never given again
    lg.delete(invoices=["2026-0002"])
    assert lg.purge(now=datetime.now() + timedelta(days=29)) == 0
    assert lg.purge(now=datetime.now() + timedelta(days=31)) == 2  # the invoice and the agreement row
    assert lg.invoice("2026-0002") is None and len(lg.activity(trash=True)) == 0
    from datetime import date
    assert lg.next_invoice_no(today=date(2026, 5, 1))[0] == "2026-0003"
    # deleting for good, from the trash only
    lg.delete_forever(invoices=["2026-0001"])
    assert lg.invoice("2026-0001") is not None  # not in the trash: left alone
    lg.delete(invoices=["2026-0001"])
    lg.delete_forever(invoices=["2026-0001"])
    assert lg.invoice("2026-0001") is None and lg.next_invoice_no(today=date(2026, 5, 1))[0] == "2026-0003"


def test_a_real_invoice_records_the_details(tmp_path):
    from helpers import pat_settings, transcript_pdf
    from minute_filler.batch import fill_jobs, group, read_docs
    from minute_filler.deliver import ledger_for
    from minute_filler.models import Attorney
    s = pat_settings()
    s.output_dir, s.records_dir = str(tmp_path / "out"), str(tmp_path / "records")
    path = transcript_pdf(tmp_path / "Roe 6-3-2026.pdf", 10, initials=["pr"] * 6 + ["ds"] * 4)
    docs, _ = read_docs([str(path)], s)
    job, = group(docs, s)
    alex = Attorney(name="Alex B. Counsel", firm="Counsel & Counsel")
    dana = Attorney(name="Dana Smith", firm="Smith Law")
    job.case.attorneys = [alex, dana]
    job.portions = [(3, [alex.key(), dana.key()]), (10, [alex.key()])]  # Dana Smith ordered pages 1-3
    fill_jobs([job], s, outputs=["invoice"])
    lg = ledger_for(s)
    got = {i.bill_to: i for i in lg.invoices()}
    assert got["Dana Smith"].pages == 3 and got["Dana Smith"].excerpt == "6/3/2026 pp. 101–103"
    assert got["Alex B. Counsel"].excerpt == "" and got["Alex B. Counsel"].pages == 6
    for i in got.values():
        assert (i.my_pages, i.transcript_pages, i.reporters) == (6, 10, "PR 6, DS 4")
        assert i.email_copy == "Yes" and i.index == "No" and i.court
    acts = lg.activity(kind="invoice")
    assert {(a.my_pages, a.transcript_pages) for a in acts} == {(6, 10)}


# ------------------------------------------------------------------ the window

@pytest.fixture
def records(qt, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from helpers import pat_settings
    from minute_filler.deliver import ledger_for
    from minute_filler.gui.records_window import RecordsWindow
    s = pat_settings()
    s.records_dir = str(tmp_path / "records")
    lg = ledger_for(s)
    lg.add_invoice(inv("2026-0001", my_pages=5, transcript_pages=10))
    lg.add_invoice(inv("2026-0002", created="2026-03-05", my_pages=12, transcript_pages=12))
    lg.log_activity("invoice", case_name="Roe", invoice_no="2026-0001", my_pages=5, transcript_pages=10)
    win = RecordsWindow(s)
    win.reload()
    win.i_year.setCurrentIndex(0)
    win.a_year.setCurrentIndex(0)
    yield win, s, lg
    win.close()
    win.deleteLater()


def test_columns_are_chosen_and_kept(records):
    from minute_filler.gui.records_window import INVOICE_COLS, RecordsWindow, col_index
    win, s, _ = records
    t = win.inv_table
    mine, judge = col_index(INVOICE_COLS, "my_pages"), col_index(INVOICE_COLS, "judge")
    assert not t.isColumnHidden(mine) and t.isColumnHidden(judge)
    assert t.item(0, mine).text() == "12"
    win.set_columns("invoices", win.columns("invoices") + ["judge"])
    assert not t.isColumnHidden(judge) and "judge" in s.records_columns["invoices"]
    again = RecordsWindow(s)  # next time: the same columns
    assert not again.inv_table.isColumnHidden(judge)
    win.set_columns("invoices", None)  # back to the usual ones
    assert t.isColumnHidden(judge) and "invoices" not in s.records_columns
    again.deleteLater()
    # sorting by a number column sorts numbers, not text
    t.sortItems(mine)
    assert [t.item(r, mine).text() for r in range(2)] == ["5", "12"]


def test_delete_trash_and_restore_in_the_window(records):
    win, _, lg = records
    win.inv_table.selectRow(0)
    first = win._selected("invoices")[0].invoice_no
    win.delete("invoices", win._selected("invoices"), ask=False)
    assert win.inv_table.rowCount() == 1 and lg.invoice(first).deleted
    win.i_trash.setChecked(True)  # the trash
    from minute_filler.gui.records_window import INVOICE_COLS, col_index
    assert win.inv_table.rowCount() == 1 and not win.inv_table.isColumnHidden(col_index(INVOICE_COLS, "gone"))
    assert win.inv_table.isColumnHidden(col_index(INVOICE_COLS, "paid"))
    win.restore("invoices", win.shown)
    assert win.inv_table.rowCount() == 0
    win.i_trash.setChecked(False)
    assert win.inv_table.rowCount() == 2
    # everything made: a row deleted on its own
    win.act_table.selectRow(0)
    win.delete("activity", win._selected("activity"), ask=False)
    assert win.act_table.rowCount() == 0 and len(lg.activity(trash=True)) == 1


def test_enter_in_the_search_box_presses_no_button(records, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog
    win, _, _ = records
    menus = []
    monkeypatch.setattr(win, "_columns_menu", lambda *a: menus.append("columns"))
    # (no button may open a file box: one that did would wait for an answer)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (menus.append("save"), ("", ""))[1])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: (menus.append("folder"), "")[1])
    win.show()
    win.i_text.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(win.i_text, "Roe")
    QTest.keyClick(win.i_text, Qt.Key_Return)  # used to open the Columns menu (the first button)
    QApplication.processEvents()
    assert not menus and win.isVisible() and not win.i_trash.isChecked()


def test_the_trash_shows_every_year_without_totals_and_reports_keep_to_the_records(records, monkeypatch):
    from minute_filler.gui import records_window
    win, _, lg = records
    lg.add_invoice(inv("2024-0003", created="2024-05-01"))
    win.reload()
    win.i_year.setCurrentIndex(win.i_year.findData(2026))
    win.delete("invoices", [lg.invoice("2024-0003")], ask=False)
    assert win.kpi["count"].text() == "2"
    win.i_trash.setChecked(True)
    assert win.i_year.currentData() == 0  # all years: the 2024 invoice deleted is there
    assert [i.invoice_no for i in win.shown] == ["2024-0003"]
    assert {k: v.text() for k, v in win.kpi.items()} == dict.fromkeys(win.kpi, "—")
    reported = []
    monkeypatch.setattr(win, "_ask_path", lambda *a: records_window.Path("report.html"))
    monkeypatch.setattr(records_window, "open_path", lambda p: None)
    monkeypatch.setattr(win.ledger, "export_html", lambda p, invoices, title: reported.append(invoices) or p)
    monkeypatch.setattr(win.ledger, "export_xlsx", lambda p, invoices: reported.append(invoices) or p)
    win.export_html()
    win.export_xlsx()
    assert [sorted(i.invoice_no for i in r) for r in reported] == [["2026-0001", "2026-0002"]] * 2
    win.i_trash.setChecked(False)
    assert win.kpi["count"].text() == "2"
