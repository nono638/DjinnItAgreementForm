"""The invoice's own text (Settings -> Invoice -> Invoice text): rows placed on the page and shown only when
their condition holds (more than one party, an excerpt...), with {placeholders}; the default rows give the
invoice its old text; a payment text and footer from before are kept; the editor in Settings and its preview.
All names are made up."""
import json

import pytest

from minute_filler.invoice import InvoiceOpts, fill_text, invoice_layout, invoice_texts, job_quotes
from minute_filler.models import Attorney
from minute_filler.settings import Settings, default_invoice_texts

from helpers import ROE, make_case, pat_settings


@pytest.fixture
def s():
    s = pat_settings()
    s.invoice_speeds = ["Regular", "Expedited"]
    return s


def page_text(s, parties=1, days=None, excerpt="", speeds=None):
    """The invoice's HTML for Roe v. ..., 30 pages."""
    if speeds is not None:
        s.invoice_speeds = speeds
    case = make_case(ROE)
    opts = InvoiceOpts(30, parties, days=days or [], excerpt=excerpt)
    html, _ = invoice_layout(case, Attorney(name="Alex B. Counsel"), s, job_quotes(case, s, opts), "2026-0001",
                             opts=opts)
    return html


def test_the_default_rows_give_the_old_text(s):
    one = page_text(s)
    assert "Please choose one delivery option" in one and "The transcript is sent after payment is received." in one
    assert "every party has paid" not in one
    assert "Zelle: (555) 555-0100" in one and "I am in the courtroom" in one and "PAYMENT" in one
    two = page_text(s, parties=2, days=[("6/3/2026", 10), ("6/4/2026", 20)])
    assert "The transcripts are delivered once every party has paid." in two and "sent after payment" not in two
    single = page_text(s, speeds=["Regular"])
    assert "Please choose one" not in single


def test_rows_go_where_they_say_when_their_condition_holds(s):
    s.invoice_texts = [
        {"where": "top", "when": "always", "text": "Thank you, {bill_to}!"},
        {"where": "transcript", "when": "excerpt", "text": "Excerpt of {pages} of {total_pages} pages."},
        {"where": "amounts", "when": "parties", "text": "Shared by {parties} parties."},
        {"where": "footer", "when": "always", "text": "Questions? Call {name}. {fax}"},
    ]
    html = page_text(s)
    assert html.index("Thank you, Alex B. Counsel!") < html.index("BILL TO")
    assert "Excerpt of" not in html and "Shared by" not in html
    assert "Questions? Call Pat Reporter. {fax}" in html  # an unknown placeholder stays as typed
    assert "PAYMENT" not in html  # no payment rows: no heading
    html = page_text(s, parties=2, excerpt="6/3/2026 pp. 101–110")
    assert "Shared by 2 parties." in html
    assert html.index("TRANSCRIPT") < html.index("Excerpt of 30 of 30 pages.") < html.index("CHOOSE ONE")


def test_fill_text():
    assert fill_text("The {transcript} {is}", {"transcript": "transcripts", "is": "are"}) == "The transcripts are"
    assert fill_text("a { stray brace", {}) == "a { stray brace"
    assert fill_text("{0} and {x.y}", {}) == "{0} and {x.y}"
    # an index into a filled-in placeholder (a string) must not break the invoice
    assert fill_text("{case[x]} v.", {"case": "Roe"}) == "{case[x]} v."


def test_conditions(s):
    case = make_case(ROE)
    s.invoice_texts = [{"where": "amounts", "when": w, "text": w} for w in
                       ("email", "no_email", "index", "no_index", "shared", "one_day", "whole")]
    opts = InvoiceOpts(60, 1, reporters="PR 60, DS 40")
    got = invoice_texts(s, case, None, job_quotes(case, s, opts), "1", opts)["amounts"]
    assert got == ["email", "index", "shared", "one_day", "whole"]  # 60 pages: an index (from 50)


def test_old_payment_and_footer_are_kept():
    s = Settings()
    s.path.write_text(json.dumps({"settings_version": 7, "invoice_payment_text": "Check to Pat Reporter",
                                  "invoice_footer": ""}), encoding="utf-8")
    texts = Settings.load().invoice_texts
    assert {"where": "payment", "when": "always", "text": "Check to Pat Reporter"} in texts
    assert not any(r["where"] == "footer" for r in texts)  # an empty footer stays empty
    s.path.write_text(json.dumps({"invoice_texts": [{"where": "nowhere", "when": "always", "text": "x"},
                                                    {"where": "top", "when": "always", "text": "Hello"}]}),
                      encoding="utf-8")
    assert Settings.load().invoice_texts == [{"where": "top", "when": "always", "text": "Hello"}]
    assert Settings().invoice_texts == default_invoice_texts()


def test_the_editor_in_settings(qt, monkeypatch, tmp_path):
    from minute_filler.gui import dialogs
    s = pat_settings()
    dlg = dialogs.SettingsDialog(s)
    ed = dlg.i_texts
    assert ed.rows() == default_invoice_texts()
    ed._add()
    ed.where.setCurrentIndex(ed.where.findData("top"))
    ed.when.setCurrentIndex(ed.when.findData("parties"))
    ed.text.setPlainText("Shared transcript")
    ed._move(-1)
    rows = ed.rows()
    assert {"where": "top", "when": "parties", "text": "Shared transcript"} in rows
    ed._remove()
    ed.table.setCurrentRow(0)
    monkeypatch.setattr(dialogs.QMessageBox, "question", lambda *a: dialogs.QMessageBox.Yes)
    ed._defaults()
    assert ed.rows() == default_invoice_texts()
    ed.table.setCurrentRow(0)
    ed.text.setPlainText("Pick one, {bill_to}.")
    dlg.accept()
    assert s.invoice_texts[0]["text"] == "Pick one, {bill_to}." and Settings.load().invoice_texts == s.invoice_texts

    # the preview: a picture of a made-up invoice with the text in the editor
    from minute_filler.invoice import sample_invoice
    png = sample_invoice(s, tmp_path)
    assert png.exists() and png.stat().st_size > 1000


def test_the_split_note_of_granular_detail_is_a_row_too(s):
    """With granular detail showing the split, its note comes from the invoice text, so it can be changed;
    by default it is where it always was, between "choose one" and "delivered once every party has paid"."""
    case = make_case(ROE)
    opts = InvoiceOpts(30, 2, detail=True, show=["split"])
    html, _ = invoice_layout(case, None, s, job_quotes(case, s, opts), "1", opts=opts)
    assert html.index("Please choose one") < html.index("Amounts are per party (2 parties ordered).") \
        < html.index("delivered once every party has paid")
    opts.detail = False  # without the detail: no split note
    html, _ = invoice_layout(case, None, s, job_quotes(case, s, opts), "1", opts=opts)
    assert "Amounts are per party" not in html
    s.invoice_texts = [{"where": "amounts", "when": "split_even", "text": "Split {parties} ways."},
                       {"where": "amounts", "when": "split_share", "text": "Your share of {shared}."}]
    opts.detail = True
    html, _ = invoice_layout(case, None, s, job_quotes(case, s, opts), "1", opts=opts)
    assert "Split 2 ways." in html and "Your share" not in html
