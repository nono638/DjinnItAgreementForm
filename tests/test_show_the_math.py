"""Show the math: the window that spells out how the amounts of the invoices just made were reached (copied,
saved as a PDF, turned off), and the detailed copy of each invoice. Also the recap's jokes (April Fools, "But
who's counting?"), the New Year yin-yang on the first day of the year the app is used, and "The math" as a tab
of the preview before saving. All names and numbers are made up; prices are from the bundled "Sample Rates"
sheet (Regular: original $4.30, copy, e-mailed copy and index $1.00 a page)."""
import os
from datetime import date, timedelta
from pathlib import Path

import pymupdf
import pytest

from helpers import ROE, make_case, pat_settings, transcript_pdf
from minute_filler.deliver import generate, ledger_for
from minute_filler.invoice import FirmInvoice, InvoiceOpts, firm_invoices
from minute_filler.invoice_calc import Share, quote_shares
from minute_filler.invoice_math import explain, to_pdf
from minute_filler.models import Attorney
from minute_filler.records import Invoice, april_fools, recap
from minute_filler.settings import Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def settled(win):
    """Runs the event loop until the window's start-up (_startup, 50 ms after it is made) is done: run later, it
    would save this test's settings into the next test's folder."""
    import time

    from PySide6.QtWidgets import QApplication
    end = time.time() + 0.5
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return win


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.invoice_speeds = ["Regular"]
    return s


def two_firms():
    """Jane Roe v. X.Y. Holding Corporation with two firms, and the options of a 60-page day shared by 2."""
    case =make_case({**ROE, "case_name": "Jane Roe v. X.Y. Holding Corporation", "index_no": "712345/2021",
                      "dates": "6/3/2026"},
                     [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel"),
                      Attorney(name="Dana Smith", firm="Smith Law")])
    return case, InvoiceOpts(60, 2, days=[("6/3/2026", 60)])


# ------------------------------------------------------------------ the math spelled out
def test_the_math_of_an_even_split(s):
    case, opts = two_firms()
    f = firm_invoices(case, s, opts)[0]
    html, plain = explain([(f, "2026-0007")])
    # 60 pp.: original 60 x 4.30 = 258.00; copy and e-mailed copy 2 x 60 x 1.00 = 120.00 each; index and the
    # judge's 60.00 each: 618.00, 309.00 each
    assert plain.splitlines()[0] == "Invoice 2026-0007 · Bill to Alex B. Counsel (Counsel & Counsel) · 60 pages, 2 parties"
    for line in ("Original: 60 pages × $4.30 = $258.00", "Copy: 2 × 60 pages × $1.00 = $120.00",
                 "E-mailed copy: 2 × 60 pages × $1.00 = $120.00", "Index: 60 pages × $1.00 = $60.00",
                 "Judge's index: 60 pages × $1.00 = $60.00", "Total: $618.00", "÷ 2 parties = $309.00 each"):
        assert f"    {line}\n" in plain
    assert "rounded" not in plain  # it splits evenly
    assert "<h2>Invoice 2026-0007" in html and "Bill to Alex B. Counsel (Counsel &amp; Counsel)" in html


def test_the_math_says_when_it_was_rounded_up(s):
    case, opts = two_firms()
    opts = InvoiceOpts(61, 3, days=[("6/3/2026", 61)])
    f = firm_invoices(case, s, opts)[0]
    _, plain = explain([(f, "2026-0008")])
    # 61 x 4.30 + 3 x 61 x 2 + 61 x 2 = 262.30 + 366 + 122 = 750.30; three ways: 250.10 each, even
    assert "÷ 3 parties = $250.10 each\n" in plain
    opts = InvoiceOpts(10, 3, days=[("6/3/2026", 10)])  # 43 + 60 + 0 (no index under 50 pages) = 103.00
    _, plain = explain([(firm_invoices(case, s, opts)[0], "2026-0009")])
    assert "÷ 3 parties = $34.34 each (rounded up to the cent)" in plain


def test_the_math_of_a_firms_own_share(s):
    sp = s.sheet().find("Regular")
    q = quote_shares([Share(10, 3)], sp)  # 10 x 4.30 / 3 + 10 + 10 = 34.3333...
    f = FirmInvoice(Attorney(name="Dana Smith"), make_case({}), InvoiceOpts(10, 3), [q])
    _, plain = explain([(f, "2026-0010")])
    assert plain.splitlines()[0] == "Invoice 2026-0010 · Bill to Dana Smith · 10 pages"
    # a third of 10 pages' price, as worked out: the lines add up to the total under them
    assert "Original: 10 pages × $4.30 = $43.00 ÷ 3 firms = $14.3333…" in plain
    assert "Copy: 10 pages × $1.00 = $10.00 (each firm pays for its own)" in plain
    assert "This firm's charges together: $34.3333…\n" in plain
    assert "This firm pays: $34.34 (rounded up to the cent)" in plain


def test_the_lines_of_a_share_add_up_to_the_total_shown():
    """Bug: each line of a firm's share was rounded to the cent on its own, so they came to $12.54 while the
    window said "Your share: $12.53 (rounded up)". Now a part of a cent is shown as it is."""
    from minute_filler.rates import Speed
    sp = Speed("Regular", "5.45", "0.55", extras={"Index": "0.35", "Email": "0.55"})
    q = quote_shares([Share(3, 2, True)], sp)  # 1.5 x 5.45 + 3 x 0.55 x 2 + 1.5 x 0.35 x 2 = 12.525
    f = FirmInvoice(Attorney(name="Dana Smith"), make_case({}), InvoiceOpts(3, 2), [q])
    _, plain = explain([(f, "2026-0011")])
    for line in ("Original: 3 pages × $5.45 = $16.35 ÷ 2 firms = $8.175",
                 "Copy: 3 pages × $0.55 = $1.65 (each firm pays for its own)",
                 "E-mailed copy: 3 pages × $0.55 = $1.65 (each firm pays for its own)",
                 "Index: 3 pages × $0.35 = $1.05 ÷ 2 firms = $0.525",
                 "This firm's charges together: $12.525", "This firm pays: $12.53 (rounded up to the cent)"):
        assert f"    {line}\n" in plain
    # whole cents: no "together" line, nothing said about rounding
    q = quote_shares([Share(10, 2)], sp)  # 5 x 5.45 + 10 x 0.55 x 2 = 38.25
    _, plain = explain([(FirmInvoice(None, make_case({}), InvoiceOpts(10, 2), [q]), "2026-0012")])
    assert "together" not in plain and "    This firm pays: $38.25\n" in plain


def test_the_math_spells_out_an_excerpt_stretch_by_stretch(s):
    """One firm ordered pages 1-90, another only 41-90 (Excerpts...): the first pays the 40 pages it ordered
    alone in full and half of the 50 ordered by both; the check at the end shows the two cover the whole
    price."""
    from minute_filler.invoice import DayOrder, Portion
    a, b = Attorney(name="Alex B. Counsel"), Attorney(name="Dana Smith")
    case = make_case(ROE, [a, b])
    opts = InvoiceOpts(90, 2, days=[("6/3/2026", 90)], orders=[DayOrder("6/3/2026", 90, [
        Portion(40, [a.key()], 1, "pp. 1-40"), Portion(50, [a.key(), b.key()], 2, "pp. 41-90")])])
    made = [(f, f"2026-00{i + 20}") for i, f in enumerate(firm_invoices(case, s, opts))]
    html, plain = explain(made)
    for line in ("Original ($4.30 a page, divided between the firms that ordered each page): $279.50",
                 "      40 pages ordered by this firm alone: 40 × $4.30 = $172.00",
                 "      50 pages ordered by 2 firms: 50 × $4.30 = $215.00 ÷ 2 = $107.50",
                 "Copy: 90 pages × $1.00 = $90.00 (each firm pays for its own)",
                 "This firm pays: $589.50"):
        assert f"    {line}\n" in plain
    assert "Bill to Dana Smith · 50 pages (6/3/2026 pp. 41-90)" in plain  # the excerpt it ordered
    assert "Original: 50 pages × $4.30 = $215.00 ÷ 2 firms = $107.50" in plain
    assert plain.rstrip().endswith("Regular: the 2 firms together pay $847.00 for work that costs $847.00")
    assert "margin-left" in html and "All the firms together" in html


def test_all_the_firms_together_counts_each_set_of_invoices(s):
    """A firm that ordered one of two days (its case copied, to name its own day) fell out of the last line,
    and the invoices of another reporter of the case were added in with the user's."""
    from minute_filler.invoice import DayOrder, Portion
    from minute_filler.invoice_math import together
    s.invoice_include_index = s.invoice_include_email = False
    a, b = Attorney(name="Pat Lawyer", checked=True), Attorney(name="Dana Smith", checked=True)
    case = make_case({**ROE, "dates": "6/3/2026, 6/4/2026"}, [a, b])
    both = [a.key(), b.key()]
    opts = InvoiceOpts(30, 2, days=[("6/3/2026", 10), ("6/4/2026", 20)], orders=[
        DayOrder("6/3/2026", 10, [Portion(10, both)]), DayOrder("6/4/2026", 20, [Portion(20, [a.key()])])])
    made = [(f, str(i)) for i, f in enumerate(firm_invoices(case, s, opts))]
    assert together(made) == ["Regular: the 2 firms together pay $169.00 for work that costs $169.00"]
    one_day = InvoiceOpts(10, 2, days=[("6/3/2026", 10)], orders=[DayOrder("6/3/2026", 10, [Portion(10, both)])])
    theirs = InvoiceOpts(5, 2, days=[("6/3/2026", 5)], orders=[DayOrder("6/3/2026", 5, [Portion(5, both)])],
                         reporter="ds")
    made = [(f, "x") for o in (one_day, theirs) for f in firm_invoices(case, s, o)]
    assert together(made) == ["Regular: the 2 firms together pay $63.00 for work that costs $63.00",
                              "Regular (DS invoices): the 2 firms together pay $31.50 for work that costs $31.50"]


def test_three_firms_together_cost_whole_cents(s):
    """Bug: three shares of $24.333… added up to $72.999…. The last line now says the work "costs $73.00" and
    that rounding each share up "adds $0.02"."""
    from minute_filler.invoice import DayOrder, Portion
    from minute_filler.invoice_math import together
    s.invoice_include_index = s.invoice_include_email = False
    firms = [Attorney(name=n, checked=True) for n in ("Pat Lawyer", "Dana Smith", "Sam Poe")]
    case = make_case(ROE, firms)
    opts = InvoiceOpts(10, 3, days=[("6/3/2026", 10)],
                       orders=[DayOrder("6/3/2026", 10, [Portion(10, [a.key() for a in firms])])])
    made = [(f, "x") for f in firm_invoices(case, s, opts)]
    assert together(made) == ["Regular: the 3 firms together pay $73.02 for work that costs $73.00 (rounding "
                              "each one up to the cent adds $0.02)"]


def test_the_math_saved_as_a_pdf(s, tmp_path):
    case, opts = two_firms()
    made = [(f, f"2026-00{i + 1:02}") for i, f in enumerate(firm_invoices(case, s, opts))]
    html, _ = explain(made * 20)  # long: more than one page
    out = to_pdf(html, tmp_path / "math.pdf")
    with pymupdf.open(out) as doc:
        assert doc.page_count > 1 and "Original: 60 pages" in doc[0].get_text()
        assert doc.metadata["creator"] == "YinIt math"
    assert to_pdf(html, out) == out  # saved over (the Save box asked)


# ------------------------------------------------------------------ the detailed copy
def test_generate_notes_the_math_and_makes_a_detailed_copy(s, tmp_path):
    case, opts = two_firms()
    math = []
    paths = generate(case, s, tmp_path, ["invoice"], opts, math=math)
    assert len(paths) == 2 and [n for _, n in math] == sorted(i.invoice_no for i in ledger_for(s).invoices())
    s.invoice_detailed_copy = True
    math = []
    paths = generate(case, s, tmp_path, ["invoice"], opts, math=math)
    assert len(paths) == 4 and len(math) == 2
    plain, detailed = paths[0], paths[1]
    assert detailed.name == f"{plain.stem} (detailed).pdf"
    with pymupdf.open(plain) as a, pymupdf.open(detailed) as b:
        assert "4.30" not in a[0].get_text() and "$4.30" in b[0].get_text()  # the charges, spelled out
        number = math[0][1]
        assert number in a[0].get_text() + str([w.field_value for w in a[0].widgets()])
        assert number in b[0].get_text() + str([w.field_value for w in b[0].widgets()])
    assert len(ledger_for(s).invoices()) == 4  # the copies are not invoices of their own
    # a job whose invoice already shows the detail gets no copy
    opts.detail = True
    assert len(generate(case, s, tmp_path, ["invoice"], opts)) == 2


def test_the_detailed_copies_are_named_apart_and_a_failed_one_stops_nothing(s, tmp_path, monkeypatch):
    """Bug: a detailed copy that couldn't be made stopped the run, so the other attorneys' invoices were never
    made. Now it is logged and left out; `detailed` names the copies made (not opened or printed)."""
    from minute_filler import deliver
    case, opts = two_firms()
    s.invoice_detailed_copy = True
    detailed = []
    paths = generate(case, s, tmp_path, ["invoice"], opts, detailed=detailed)
    assert len(paths) == 4 and detailed == [paths[1], paths[3]]
    real = deliver.render

    def broken(*a, **k):
        if a[-1].detail:
            raise OSError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(deliver, "render", broken)
    detailed = []
    paths = generate(case, s, tmp_path, ["invoice"], opts, detailed=detailed)
    assert len(paths) == 2 and detailed == [] and len(ledger_for(s).invoices()) == 4  # both invoices made


def test_generate_all_counts_the_detailed_copies(s, tmp_path):
    """Bug: "Generate all (N files)" left the detailed copies out of N."""
    from minute_filler.batch import expand_paths, files_to_make, group, read_docs
    d = tmp_path / "in"
    d.mkdir()
    transcript_pdf(d / "Roe.pdf", 12)
    docs, errors = read_docs(expand_paths([str(d)]), s)
    jobs = group(docs, s)
    assert not errors and len(jobs) == 1
    for a in jobs[0].case.attorneys:
        a.checked = a is jobs[0].case.attorneys[0]
    plain = files_to_make(jobs, ["invoice"], s)
    s.invoice_detailed_copy = True
    assert plain >= 1 and files_to_make(jobs, ["invoice"], s) == 2 * plain
    jobs[0].invoice_detail = True  # granular already: no copies
    assert files_to_make(jobs, ["invoice"], s) == plain


# ------------------------------------------------------------------ the window
@pytest.fixture
def made(s):
    case, opts = two_firms()
    return [(f, "2026-0001") for f in firm_invoices(case, s, opts)[:1]]


def test_the_math_window_copies_saves_and_turns_off(made, s, qt, tmp_path, monkeypatch):
    from minute_filler.gui.preview import MATH_OFF_NOTE, MathDialog
    dlg = MathDialog(made, s, None, tmp_path)
    assert "Total: $618.00" in dlg.text.toPlainText()
    dlg._copy()
    assert qt.QApplication.clipboard().text() == dlg.plain and dlg.plain.startswith("Invoice 2026-0001")
    asked = []

    def where(parent, title, path, filt):
        asked.append(path)
        return str(tmp_path / "chosen.pdf"), filt
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", where)
    dlg._save()
    assert asked == [str(tmp_path / "Invoice 2026-0001 - the math.pdf")]
    assert (tmp_path / "chosen.pdf").exists() and "chosen.pdf" in dlg.note.text()
    assert s.show_math is True
    dlg._turn_off()
    assert s.show_math is False and Settings.load().show_math is False and dlg.note.text() == MATH_OFF_NOTE


def test_generate_shows_the_math_after_an_invoice(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import MathDialog
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s = pat_settings(initials="pr")
    s.use_ai = s.open_after = False
    s.welcomed = True
    s.output_dir, s.records_dir = str(tmp_path / "out"), str(tmp_path / "records")
    s.outputs = ["agreement", "invoice"]
    win = make_window(s)
    shown = []
    monkeypatch.setattr(MathDialog, "exec", lambda dlg: shown.append(dlg.plain) or 0)
    from test_extras import wait
    win.add_files([str(transcript_pdf(tmp_path / "Transcript.pdf", 12))])
    wait(win, lambda: win.cur.docs)
    for a in win.cur.case.attorneys:
        a.checked = a is win.cur.case.attorneys[0]
    win.cur.att_touched = True
    win._show_case()
    win.fill()
    assert len(shown) == 1 and "Original: 12 pages × $4.30" in shown[0]
    win.s.show_math = False
    win.fill()
    assert len(shown) == 1 and len(ledger_for(win.s).invoices()) == 2
    # the detailed copy is saved but not opened or printed with the invoice
    from minute_filler.gui import main_window
    opened, printed = [], []
    monkeypatch.setattr(main_window, "open_path", lambda p: opened.append(Path(p).name))
    monkeypatch.setattr(main_window.MainWindow, "_saved_box",
                        lambda self, *a, files=None, **k: printed.extend(Path(p).name for p in files or []))
    win.s.open_after = win.s.invoice_detailed_copy = True
    win.fill()
    assert any(Path(p).name.endswith(" (detailed).pdf") for p in win.cur.saved)
    assert opened and printed and not [n for n in opened + printed if "(detailed)" in n]


def test_the_settings_save_the_math_choices(qt):
    from minute_filler.gui.dialogs import SettingsDialog
    s = Settings()
    assert s.show_math is True and s.invoice_detailed_copy is False
    dlg = SettingsDialog(s)
    assert dlg.o_math.isChecked() and not dlg.i_detailed_copy.isChecked()
    dlg.o_math.setChecked(False)
    dlg.i_detailed_copy.setChecked(True)
    dlg.accept()
    again = Settings.load()
    assert (again.show_math, again.invoice_detailed_copy) == (False, True)


# ------------------------------------------------------------------ the recap's jokes
def inv(no, created, pages=243, amount="870.00"):
    """A made-up Regular invoice to Alex B. Counsel, for the recaps."""
    return Invoice(invoice_no=no, created=created, case_name="Jane Roe v. X.Y. Holding Corporation",
                   bill_to="Alex B. Counsel", firm="Counsel & Counsel", pages=pages, amounts={"Regular": amount},
                   billed_speed="Regular")


def test_but_whos_counting_once_in_twelve():
    invoices = [inv("2026-0001", "2026-09-03")]
    lines, _, _ = recap(invoices, date(2026, 10, 3), rng=lambda: 1 / 12 - 0.001)
    assert lines == ["Last month (September 2026) you made $870.00 with 243 pages (1 invoice). $0.00 of it is paid so far. But who's counting?"]
    lines, _, _ = recap(invoices, date(2026, 10, 3), rng=lambda: 1 / 12)
    assert lines == ["Last month (September 2026) you made $870.00 with 243 pages (1 invoice). $0.00 of it is paid so far."]
    # only the monthly line: last year's never ends with it
    lines, _, _ = recap([inv("2026-0001", "2026-03-03")], date(2027, 1, 4), "2027-01", rng=lambda: 0)
    assert lines == ["Last year (2026) you made $870.00 with 243 pages (1 invoice). $0.00 of it is paid so far."]


def test_april_fools():
    assert april_fools(date(2027, 4, 1)) == "Last month (March 2027) you made $65,000.00!"
    assert april_fools(date(2027, 4, 2)) is None and april_fools(date(2027, 1, 1)) is None


def test_the_april_fools_recap_pops_up_first(tmp_path, monkeypatch, make_window, qt):
    s = pat_settings()
    s.use_ai = False  # (no look for Ollama finishing after the test)
    s.welcomed = True
    s.records_dir = str(tmp_path / "records")
    win = settled(make_window(s))
    ledger_for(s).add_invoice(inv("2027-0001", "2027-03-10"))
    shown = []
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda box: shown.append(box.text()) or 0)
    win.s.recaps = False
    win.open_records()  # (opened without a recap: _recap is called below with the day)
    win.s.recaps = True
    win._recap(win._records_win, date(2027, 4, 1))
    assert shown[0] == "Last month (March 2027) you made $65,000.00!"
    assert shown[1].startswith("April Fools! Here's what really happened:\n\nLast month (March 2027) you made $870.00")
    win.s.recap_month = ""
    win._recap(win._records_win, date(2027, 4, 2))  # the next day: no joke
    assert len(shown) == 3 and shown[2].startswith("Last month (March 2027) you made $870.00")


# ------------------------------------------------------------------ the New Year yin-yang
def test_the_first_day_of_a_new_year():
    from minute_filler.gui.main_window import note_opened
    s = Settings()
    assert note_opened(s, date(2026, 10, 4)) is False and s.opened_year == "2026"  # first ever: no picture
    assert note_opened(s, date(2026, 12, 31)) is False
    assert note_opened(s, date(2027, 1, 7)) is True and Settings.load().new_year_day == "2027-01-07"
    assert note_opened(s, date(2027, 1, 7)) is True  # opened again that day: still
    assert note_opened(s, date(2027, 1, 8)) is False  # the next day: back to usual
    # a year that goes back (a clock set wrong) is no new year, nor is one typed oddly in the file
    assert note_opened(s, date(2026, 5, 12)) is False and s.opened_year == "2026"
    s.opened_year = "26"
    assert note_opened(s, date(2026, 5, 13)) is False


def test_the_new_year_doesnt_write_over_settings_that_couldnt_be_read():
    """A settings file that can't be read (locked by a sync, damaged) loads as the defaults; noting the year
    must not save those over it."""
    from minute_filler.gui.main_window import note_opened
    s = Settings()
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text("{ not json", encoding="utf-8")
    loaded = Settings.load()
    assert loaded.unreadable is True
    note_opened(loaded, date(2027, 1, 7))
    assert s.path.read_text(encoding="utf-8") == "{ not json"
    s.path.unlink()
    assert Settings.load().unreadable is False  # no file at all: a new user, saved as usual
    assert "opened_year" in Settings.__dataclass_fields__ and "new_year_day" in __import__(
        "minute_filler.settings", fromlist=["LOCAL"]).LOCAL


def test_the_new_year_yin_is_shown_when_ready(make_window, qt, monkeypatch):
    from minute_filler.gui import main_window
    s = pat_settings()
    s.use_ai = False  # (no look for Ollama finishing after the test)
    s.welcomed = True
    s.opened_year, s.new_year_day = str(date.today().year), date.today().isoformat()
    win = settled(make_window(s))
    assert win.drop.new_year_day == date.today().isoformat()
    loaded = []
    real = main_window.rounded
    monkeypatch.setattr(main_window, "rounded", lambda path, *a: loaded.append(path.name) or real(path, *a))
    win.drop._pixmaps.clear()
    win.drop.set_mood("done")
    win.drop.set_mood("working")
    assert loaded == ["yin_newyear.jpg", "yin_working.jpg"]
    assert (main_window.ASSETS / "yin_newyear.jpg").exists()
    # left open overnight: the next day it is the usual yin-yang again
    win.drop.new_year_day = (date.today() - timedelta(days=1)).isoformat()
    win.drop.set_mood("done")
    assert loaded[-1] == "yin_done.jpg"


# ------------------------------------------------------------------ the math with the preview (2.0.0)
def math_window(tmp_path, monkeypatch, make_window, qt, preview=True, days=1):
    """A window with the preview on and a 12-page transcript read (and an 8-page one of the next day, with
    days=2), one attorney ticked on each day."""
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s = pat_settings(initials="pr")
    s.use_ai = s.open_after = False
    s.welcomed = True
    s.output_dir, s.records_dir = str(tmp_path / "out"), str(tmp_path / "records")
    s.outputs = ["agreement", "invoice"]
    win = make_window(s, preview=preview)
    from test_extras import wait
    files = [str(transcript_pdf(tmp_path / "Transcript.pdf", 12))]
    if days == 2:
        files.append(str(transcript_pdf(tmp_path / "Day 2.pdf", 8, date="June 4, 2026")))
    win.add_files(files)
    wait(win, lambda: len(win.jobs) == days and all(j.docs for j in win.jobs))
    for j in win.jobs:
        for a in j.case.attorneys:
            a.checked = a is j.case.attorneys[0]
        j.att_touched = True
    win._show_case()
    return win


def test_the_preview_shows_the_math_with_the_files(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import MathDialog, PreviewDialog
    win = math_window(tmp_path, monkeypatch, make_window, qt)
    seen, after = [], []

    def look(dlg):
        seen.append((dlg.tabs.tabText(0), dlg.math.plain if dlg.math else ""))
        return PreviewDialog.Accepted
    monkeypatch.setattr(PreviewDialog, "exec", look)
    monkeypatch.setattr(MathDialog, "exec", lambda dlg: after.append(dlg.plain) or 0)
    win.fill()
    (tab, plain), = seen
    year = date.today().year
    assert tab == "The math" and plain.startswith(f"Invoice {year}-0001") and "12 pages × $4.30" in plain
    assert after == []  # shown with the files: not again once saved
    win.s.show_math = False
    seen.clear()
    win.fill()
    assert seen[0][1] == "" and after == []


def test_generate_all_previews_the_files_and_the_math(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import MathDialog, PreviewDialog
    from test_extras import wait
    win = math_window(tmp_path, monkeypatch, make_window, qt, days=2)
    seen, after = [], []
    monkeypatch.setattr(PreviewDialog, "exec",
                        lambda dlg: seen.append([dlg.tabs.tabText(i) for i in range(dlg.tabs.count())])
                        or PreviewDialog.Rejected)
    monkeypatch.setattr(MathDialog, "exec", lambda dlg: after.append(dlg.plain) or 0)
    win.fill_all_jobs()
    wait(win, lambda: seen and not win.filling)
    tabs, = seen
    assert tabs[0] == "The math" and any(t.startswith("Invoice") for t in tabs)
    assert not (tmp_path / "out").exists() and ledger_for(win.s).invoices() == []  # Go back: nothing saved
    monkeypatch.setattr(PreviewDialog, "exec", lambda dlg: PreviewDialog.Accepted)
    win.fill_all_jobs()
    wait(win, lambda: not win.filling and ledger_for(win.s).invoices())
    year = date.today().year
    assert [i.invoice_no for i in ledger_for(win.s).invoices()] == [f"{year}-0001"]  # the number previewed
    assert after == []


def test_generate_all_without_the_preview_shows_the_math_after(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import MathDialog
    from test_extras import wait
    win = math_window(tmp_path, monkeypatch, make_window, qt, preview=False, days=2)
    after = []
    monkeypatch.setattr(MathDialog, "exec", lambda dlg: after.append(dlg.plain) or 0)
    monkeypatch.setattr(type(win), "_saved_box", lambda self, *a, **k: None)
    win.fill_all_jobs()
    wait(win, lambda: not win.filling and ledger_for(win.s).invoices())
    assert len(after) == 1 and "Original:" in after[0]
