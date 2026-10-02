"""Invoice pricing (same numbers as the reporter's spreadsheet), the speeds an invoice offers
(Settings.invoice_speeds: several, one, or none for the job's own speed) and the invoice PDF: the amounts only
by default or with granular detail, its values as fields that can be changed, locked or flattened, and its
entry in the records. Invoices for several days are in test_joint_invoice.py."""
from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from minute_filler.fill import LOCK_BUTTON, lock_pdf
from minute_filler.invoice import InvoiceOpts, make_invoices
from minute_filler.invoice_calc import fmt, money, offered, quote, quotes_for
from minute_filler.models import Attorney
from minute_filler.rates import FALLBACK
from minute_filler.records import Ledger
from minute_filler.settings import Settings

from helpers import ROE, make_case, pat_settings


@pytest.fixture
def s():
    s = pat_settings(email="pat@example.com")  # with the bundled "Sample Rates" sheet
    s.invoice_speeds = ["Regular", "Expedited", "Daily"]
    return s


def test_default_speeds():
    assert Settings().invoice_speeds == ["Regular", "Expedited"]  # Daily and Immediate are offered when ticked


def amounts(qs):
    """{speed: (total, per party, per page)} of each quote."""
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
    assert quote(60, sp, 1, include_email=False, index=False).total == Decimal("318.00")  # 60 x 5.30
    assert quote(60, sp, 1, threshold=61).total == Decimal("318.00") + Decimal("60.00")


def test_per_party_rounds_to_cents(s):
    q = quote(7, s.sheet().find("Regular"), 3)  # 7 x (4.30 + 3 + 3) = 72.10, / 3 = 24.0333
    assert q.total == Decimal("72.10") and q.per_party == Decimal("24.03")


def test_single_speed_and_missing_columns(s):
    s.invoice_speeds = ["Expedited"]  # one speed ticked: a single-speed invoice
    assert [q.speed for q in quotes_for(10, 1, s.sheet(), s, "Regular")] == ["Expedite"]
    s.invoice_speeds = []  # none ticked: the job's own speed
    assert [q.speed for q in quotes_for(10, 1, s.sheet(), s, "Daily")] == ["Daily"]
    s.invoice_speeds = ["Regular", "Daily"]
    assert [q.speed for q in quotes_for(10, 1, s.sheet(), s, "Regular")] == ["Regular", "Daily"]
    # a sheet without Email/Index columns simply has no such lines
    q = quote(80, FALLBACK.find("Regular"), 1)
    assert [l.label for l in q.lines] == ["Original", "Copy"] and q.total == Decimal("344.00")
    assert offered(FALLBACK, [], "Nonsense") == FALLBACK.speeds[:1]


def test_money_helpers():
    assert money("$1,234.5") == Decimal("1234.50") and money("") == Decimal("0.00") and money("x") == 0
    assert fmt(Decimal("1234.5")) == "$1,234.50"


@pytest.fixture
def case():
    return make_case({**ROE, "delivery": "Regular"}, [Attorney(name="Alex Example", firm="Example Firm LLP", address="1 Main Street\nAnytown, NY 10000",
                            email="billing@examplefirm.com", checked=True),
                   Attorney(name="Sam Advocate", firm="Advocate & Partners", checked=True),
                   Attorney(name="Not Ordering", checked=False)])


def widget_values(path) -> dict[str, str]:
    """{field name: value} of a PDF's first page."""
    with pymupdf.open(path) as doc:
        return {w.field_name: w.field_value for w in doc[0].widgets()}


def page_text(path) -> str:
    """The text drawn on the page itself, without the fields."""
    with pymupdf.open(path) as doc:
        page = doc[0]
        for xref in [w.xref for w in page.widgets()]:
            page.delete_widget(page.load_widget(xref))
        return page.get_text()


def test_invoice_pdf_and_record(case, s, tmp_path):
    ledger = Ledger(tmp_path / "r.db")
    paths = make_invoices(case, s, tmp_path / "out", InvoiceOpts(pages=60, parties=2), ledger)
    assert len(paths) == 2  # one per ticked attorney
    doc = pymupdf.open(paths[0])
    assert doc.page_count == 1 and doc.metadata["creator"] == "DjinnIt invoice"
    text = page_text(paths[0])
    for want in ("INVOICE", "Pat Reporter", "Regular", "Expedite", "Daily", "2-4 weeks from receipt of payment",
                 "Please choose one"):
        assert want in text, want
    # the amounts only, as on a reporter's own invoice: no page count, rates per page or arithmetic
    for unwanted in ("Pages", "Per page", "pp.", "parties ordered", "$339.00"):
        assert unwanted not in text, unwanted
    # the values are fields, so they can be corrected in a PDF viewer
    f = widget_values(paths[0])
    assert f["invoice_no"] == f"{date.today().year}-0001" and f["index_no"] == "712345/2021"
    assert f["bill_to"].splitlines()[:2] == ["Alex Example", "Example Firm LLP"]
    assert f["case"].startswith("Jane Roe v. X.Y.")
    assert (f["amount Regular"], f["amount Expedite"], f["amount Daily"]) == ("$339.00", "$393.00", "$468.00")
    assert "pages" not in f and LOCK_BUTTON in f
    assert widget_values(paths[1])["bill_to"].startswith("Sam Advocate")
    invs = ledger.invoices()
    assert len(invs) == 2 and invs[0].invoice_no != invs[1].invoice_no
    first = next(i for i in invs if i.firm == "Example Firm LLP")
    assert first.amounts == {"Regular": "339.00", "Expedite": "393.00", "Daily": "468.00"}
    assert first.billed == Decimal("339.00") and first.pages == 60 and first.parties == 2
    assert paths[0].name.startswith("Invoice " + first.invoice_no)


def test_granular_detail(case, s, tmp_path):
    """"Show granular detail" adds the page count, the price per page and the charges in each amount."""
    path = make_invoices(case, s, tmp_path, InvoiceOpts(pages=60, parties=2, detail=True), Ledger(tmp_path / "r.db"))[0]
    text = page_text(path)
    for want in ("Pages", "Per page", "Original: 60 pp.", "split 2 ways", "2 parties ordered"):
        assert want in text, want
    f = widget_values(path)
    assert f["pages"] == "60" and f["per page Regular"] == "$5.65"
    s.invoice_detail = True  # the setting, when the job doesn't say
    assert "Per page" in page_text(make_invoices(case, s, tmp_path, InvoiceOpts(pages=60), Ledger(tmp_path / "r.db"))[0])


def test_one_speed_and_blank_values(case, s, tmp_path):
    """One speed ticked bills that speed alone. Blank values still get a field, to type into later."""
    s.invoice_speeds = ["Expedited"]
    case.set("judge", "")
    path = make_invoices(case, s, tmp_path, InvoiceOpts(pages=10), Ledger(tmp_path / "r.db"))[0]
    text = page_text(path)
    assert "DELIVERY" in text and "Regular" not in text and "Please choose" not in text
    f = widget_values(path)
    assert [k for k in f if k.startswith("amount")] == ["amount Expedite"]
    assert f["judge"] == ""


def test_fields_can_be_changed_and_flattened(case, s, tmp_path):
    path = make_invoices(case, s, tmp_path, InvoiceOpts(pages=10), Ledger(tmp_path / "r.db"))[0]
    with pymupdf.open(path) as doc:
        page = doc[0]
        w = page.load_widget(next(w.xref for w in page.widgets() if w.field_name == "judge"))
        w.field_value = "Lee"
        w.update()
        doc.save(tmp_path / "edited.pdf")
    assert widget_values(tmp_path / "edited.pdf")["judge"] == "Lee"
    locked = lock_pdf(tmp_path / "edited.pdf")
    assert locked.name == "edited (locked).pdf" and widget_values(locked) == {}
    assert "Lee" in pymupdf.open(locked)[0].get_text() and "Lock fields" not in pymupdf.open(locked)[0].get_text()
    assert widget_values(tmp_path / "edited.pdf")["judge"] == "Lee"  # the original is kept
    s.flatten = True
    flat = make_invoices(case, s, tmp_path / "flat", InvoiceOpts(pages=10), Ledger(tmp_path / "r2.db"))[0]
    assert widget_values(flat) == {} and "$63.00" in pymupdf.open(flat)[0].get_text()


def test_no_invoice_without_pages(case, s, tmp_path):
    with pytest.raises(ValueError):
        make_invoices(case, s, tmp_path, InvoiceOpts(pages=0), Ledger(tmp_path / "r.db"))
