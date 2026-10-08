"""Invoice numbers are taken from the records right before each invoice is made, and claimed there at once
(Ledger.reserve_invoice_no), so a second copy of the app making an invoice at the same moment gets the next number
instead of the same one; a number whose invoice can't be made is given back. All names are made up."""
import sqlite3
import threading
import time
from datetime import date

import pytest

from minute_filler import invoice
from minute_filler.invoice import InvoiceOpts, make_invoice
from minute_filler.models import Attorney
from minute_filler.records import Ledger

from helpers import ROE, make_case, pat_settings

ALEX = Attorney(name="Alex B. Counsel", firm="Counsel & Counsel")
DAY = date(2026, 5, 1)


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.records_dir = str(tmp_path / "records")
    return s


def test_two_copies_of_the_app_get_two_numbers(tmp_path):
    """Two Ledgers on one records file, as two copies of the app have: each reserves a number of its own, and the
    next number is the one after both."""
    one, two = Ledger(tmp_path / "r.db"), Ledger(tmp_path / "r.db")
    assert one.reserve_invoice_no(today=DAY)[0] == "2026-0001"
    assert two.reserve_invoice_no(today=DAY)[0] == "2026-0002"  # (next_invoice_no alone said 0001 to both)
    assert one.next_invoice_no(today=DAY)[0] == "2026-0003"


def test_a_number_being_taken_elsewhere_is_waited_for(tmp_path):
    """Another copy of the app holds the records while it takes 2026-0001: this one waits, then takes 0002."""
    ledger = Ledger(tmp_path / "r.db")
    other = sqlite3.connect(tmp_path / "r.db", isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO used_numbers (invoice_no, year, seq, reporter) VALUES ('2026-0001', 2026, 1, '')")
    done = threading.Timer(0.3, lambda: other.execute("COMMIT"))
    done.start()
    started = time.monotonic()
    try:
        assert ledger.reserve_invoice_no(today=DAY)[0] == "2026-0002"
        assert time.monotonic() - started >= 0.25
    finally:
        done.join()
        other.close()


def test_an_invoice_made_meanwhile_by_another_copy_gets_the_next_number(s, tmp_path, monkeypatch):
    """While this invoice is drawn, another copy of the app makes one (the same records file): each keeps its own
    number (before: both drew the same, and this one was refused by the records)."""
    case = make_case({**ROE, "est_pages": "30"}, attorneys=[ALEX])
    real = invoice.render
    other: list = []

    def render(*args, **kwargs):
        if not other:
            other.append(None)  # (the other copy's own invoice is drawn as usual)
            other[0] = make_invoice(case, ALEX, s, tmp_path / "other", InvoiceOpts(30), Ledger(tmp_path / "r.db"))
        return real(*args, **kwargs)
    monkeypatch.setattr(invoice, "render", render)
    path, number = make_invoice(case, ALEX, s, tmp_path / "out", InvoiceOpts(30), Ledger(tmp_path / "r.db"))
    year = date.today().year
    assert other[0][1] == f"{year}-0002" and number == f"{year}-0001" and path.is_file()
    assert sorted(i.invoice_no for i in Ledger(tmp_path / "r.db").invoices()) == [f"{year}-0001", f"{year}-0002"]


def test_a_number_whose_invoice_fails_is_given_back(s, tmp_path, monkeypatch):
    """An invoice that can't be drawn gives its number back (release_invoice_no): the next invoice gets it."""
    case = make_case({**ROE, "est_pages": "30"}, attorneys=[ALEX])
    ledger = Ledger(tmp_path / "r.db")

    def broken(*args, **kwargs):
        raise OSError("the invoice template could not be read")
    monkeypatch.setattr(invoice, "render", broken)
    with pytest.raises(OSError):
        make_invoice(case, ALEX, s, tmp_path / "out", InvoiceOpts(30), ledger)
    assert ledger.next_invoice_no()[0] == f"{date.today().year}-0001"
