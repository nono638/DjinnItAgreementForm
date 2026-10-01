"""Invoice pricing (same numbers as the reporter's spreadsheet) and the invoice PDF."""
from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from minute_filler.invoice import InvoiceOpts, make_invoices
from minute_filler.invoice_calc import fmt, money, offered, quote, quotes_for
from minute_filler.models import Attorney
from minute_filler.rates import FALLBACK
from minute_filler.records import Ledger

from helpers import ROE, make_case, pat_settings


@pytest.fixture
def s():
    return pat_settings(email="pat@example.com")  # with the bundled "Sample Rates" sheet


def amounts(qs):
    return {q.speed: (q.total, q.per_party, q.per_page) for q in qs}


def test_ten_pages_one_party_matches_the_spreadsheet(s):
    got = amounts(quotes_for(10, 1, s.sheet(), s, "Regular"))
    assert got == {"Regular": (Decimal("63.00"), Decimal("63.00"), Decimal("6.30")),
                   "Expedite": (Decimal("76.00"), Decimal("76.00"), Decimal("7.60")),
                   "Daily": (Decimal("91.00"), Decimal("91.00"), Decimal("9.10"))}


def test_index_from_fifty_pages_and_split_between_parties(s):
    sp = s.sheet().find("Regular")
    q49 = quote(49, sp, 2)
    assert [l.label for l in q49.lines] == ["Original", "Copy", "E-mailed copy"]
    q = quote(60, sp, 2)
    # 60 x (4.30 + 2x1.00 + 2x1.00 + 2x1.00 index + 1.00 judge's index) = 678.00
    assert [l.label for l in q.lines] == ["Original", "Copy", "E-mailed copy", "Index", "Judge's index"]
    assert q.total == Decimal("678.00") and q.per_party == Decimal("339.00") and q.per_page == Decimal("5.65")


def test_options_leave_out_email_and_index(s):
    sp = s.sheet().find("Regular")
    assert quote(60, sp, 1, include_email=False, include_index=False).total == Decimal("318.00")  # 60 x 5.30
    assert quote(60, sp, 1, threshold=61).total == Decimal("318.00") + Decimal("60.00")


def test_per_party_rounds_to_cents(s):
    q = quote(7, s.sheet().find("Regular"), 3)  # 7 x (4.30 + 3 + 3) = 72.10, / 3 = 24.0333
    assert q.total == Decimal("72.10") and q.per_party == Decimal("24.03")


def test_single_speed_and_missing_columns(s):
    assert [q.speed for q in quotes_for(10, 1, s.sheet(), s, "Expedited", choice=False)] == ["Expedite"]
    s.invoice_speeds = ["Regular", "Daily"]
    assert [q.speed for q in quotes_for(10, 1, s.sheet(), s, "Regular")] == ["Regular", "Daily"]
    # a sheet without Email/Index columns simply has no such lines
    q = quote(80, FALLBACK.find("Regular"), 1)
    assert [l.label for l in q.lines] == ["Original", "Copy"] and q.total == Decimal("344.00")
    assert offered(FALLBACK, [], "Nonsense", False) == FALLBACK.speeds[:1]


def test_money_helpers():
    assert money("$1,234.5") == Decimal("1234.50") and money("") == Decimal("0.00") and money("x") == 0
    assert fmt(Decimal("1234.5")) == "$1,234.50"


@pytest.fixture
def case():
    return make_case({**ROE, "delivery": "Regular"}, [Attorney(name="Alex Example", firm="Example Firm LLP", address="1 Main Street\nAnytown, NY 10000",
                            email="billing@examplefirm.com", checked=True),
                   Attorney(name="Sam Advocate", firm="Advocate & Partners", checked=True),
                   Attorney(name="Not Ordering", checked=False)])


def test_invoice_pdf_and_record(case, s, tmp_path):
    ledger = Ledger(tmp_path / "r.db")
    paths = make_invoices(case, s, tmp_path / "out", InvoiceOpts(pages=60, parties=2), ledger)
    assert len(paths) == 2  # one per ticked attorney
    doc = pymupdf.open(paths[0])
    text = doc[0].get_text()
    assert doc.page_count == 1 and doc.metadata["creator"] == "DjinnIt invoice"
    for want in ("INVOICE", f"{date.today().year}-0001", "Example Firm LLP", "Jane Roe v. X.Y.",
                 "712345/2021", "$339.00", "$393.00", "$468.00", "per party", "Pat Reporter"):
        assert want in text, want
    assert "Advocate" in pymupdf.open(paths[1])[0].get_text()
    invs = ledger.invoices()
    assert len(invs) == 2 and invs[0].invoice_no != invs[1].invoice_no
    first = next(i for i in invs if i.firm == "Example Firm LLP")
    assert first.amounts == {"Regular": "339.00", "Expedite": "393.00", "Daily": "468.00"}
    assert first.billed == Decimal("339.00") and first.pages == 60 and first.parties == 2
    assert paths[0].name.startswith("Invoice " + first.invoice_no)


def test_no_invoice_without_pages(case, s, tmp_path):
    with pytest.raises(ValueError):
        make_invoices(case, s, tmp_path, InvoiceOpts(pages=0), Ledger(tmp_path / "r.db"))
