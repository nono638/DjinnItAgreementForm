"""Regressions found by the bug sweep before 2.7.0, each failing on the code before its fix. The money first: the
judge's index of firms at different speeds, the index price of the speeds firms committed to, the days a firm's
speed is asked for, a speed kept when a later document renames its firm, a speed the rate sheet lacks stopping
Generate before anything is made; then the case folders, the math PDFs, the rate sheet's fingerprint, the math's
words (each line's speed, a line in parts, the invoice's new text row); then the window (a day held for three
speeds, the Speed boxes, the questions Generate asks while documents still arrive or firms are renamed, Go back at
the preview and at the rate sheet warning, Generate all's count of the math, the job list). All names and numbers
are made up."""
import os
import time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from minute_filler import courthouses
from minute_filler.batch import CaseFolder, Job, fill_jobs, joint_invoice, remerge, speeds_to_ask
from minute_filler.courthouses.base import Party
from minute_filler.courthouses.queens_supreme_civil.billing import split
from minute_filler.invoice import firm_invoices, index_priced
from minute_filler.invoice_calc import Share, quote_ordered
from minute_filler.models import Attorney
from minute_filler.pdfout import case_folder_name

from helpers import ROE, invoice_text, make_case, pat_settings, text_doc, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def counsel():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def smith():
    return Attorney(name="Dana Smith", firm="Smith Law", checked=True)


def poe():
    return Attorney(name="Sam Poe", firm="Poe LLP", checked=True)


A, B, C = counsel().key(), smith().key(), poe().key()


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.invoice_include_index = False
    s.save_math = "off"
    return s


def typed_day(pages=120, date="6/3/2026", attorneys=None) -> Job:
    """A day of Roe v. X.Y. whose pages were typed in, Counsel & Counsel and Smith Law ticked."""
    job = Job()
    job.case = make_case({**ROE, "dates": date, "est_pages": str(pages)},
                         attorneys if attorneys is not None else [counsel(), smith()])
    return job


def own_sheet(s, tmp_path, text, name="Court Rates"):
    """Settings `s` using a rate sheet of this text of its own."""
    folder = tmp_path / "sheets"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.csv").write_text(text, encoding="utf-8")
    s.rate_sheets_dir, s.rate_sheet = str(folder), name
    s.reload_rates()
    assert s.sheet().name == name
    return s


# ------------------------------------------------------------------ the money


def test_a_faster_speeds_lower_index_price_bills_nobody_less_than_nothing():
    """A sheet can price the faster speed's index lower (Daily $1.50, Immediate $1.00): the judge's index, billed
    once at Immediate, was $0.75 a page to the Daily firm and $0.25 to the Immediate one, and with three Daily
    firms -$0.125. A slower firm's share is now never above the price billed."""
    daily, immediate = Party("Counsel & Counsel", "Daily", Fraction("1.50"), 2), \
        Party("Smith Law", "Immediate", Fraction("1.00"), 3)
    slow, fast = split([daily, immediate], 0), split([daily, immediate], 1)
    assert slow.per_page == fast.per_page == Fraction("0.50")
    assert "billed at Immediate, for less: $1.00 ÷ 2 = $0.50 a page" in slow.steps[0][0]
    four = [daily, daily, daily, immediate]
    parts = [split(four, i).per_page for i in range(4)]
    assert min(parts) >= 0 and sum(parts) == Fraction("1.00")
    # (the usual sheet, dearer at the faster speed: unchanged)
    usual = [Party("A", "Daily", Fraction("6.50"), 2), Party("B", "Immediate", Fraction("7.60"), 3)]
    assert [split(usual, i).per_page for i in range(2)] == [Fraction("3.25"), Fraction("4.35")]


INDEX_ON_DAILY = ("Rate,Original,Copy,Email,Index,Days\n"
                  "Regular,$4.30,$1.00,$1.00,$0.00,21\n"
                  "Expedite,$5.40,$1.10,$1.10,$0.00,7\n"
                  "Daily,$6.50,$1.25,$1.25,$1.25,1\n"
                  "Immediate,$7.60,$1.45,$1.45,$0.00,0\n")


def test_the_index_price_of_the_speeds_firms_committed_to_counts(s, tmp_path):
    """Only Daily has an index price; the invoice offers Regular and Expedite, but the firms committed to Daily
    and Immediate: an index is charged, so the window must not say "the rate sheet has no index price"."""
    own_sheet(s, tmp_path, INDEX_ON_DAILY)
    s.invoice_include_index = True
    job = typed_day()
    case, opts = joint_invoice([job])
    assert not index_priced(case, s, opts)  # (both firms at the form's speed, Expedite, for now: no index price)
    job.speeds = {A: "Daily", B: "Immediate"}
    case, opts = joint_invoice([job])
    assert index_priced(case, s, opts)
    charged = [l.charge for f in firm_invoices(case, s, opts) for q in f.quotes for l in q.lines if l.amount > 0]
    assert "Index" in charged
    assert not index_priced(case, s)  # (without the orders: the speeds offered only, as before)


def test_a_firm_alone_on_one_day_and_sharing_another_is_asked_both(s):
    """Counsel & Counsel alone on 6/3 and with Smith Law on 6/4: one invoice, so its speed is asked for both
    days at once (it was asked for 6/4 only, and 6/3 a second time after the answer)."""
    first, second = typed_day(60, attorneys=[counsel()]), typed_day(40, "6/4/2026")
    asked = speeds_to_ask([first, second], s)
    assert {(a.job is first, a.key) for a in asked} == {(True, A), (False, A), (False, B)}
    for a in asked:
        a.job.speeds.setdefault(a.key, "Daily")
    assert not speeds_to_ask([first, second], s)


ONE = """From: Dana Smith <dsmith@example.com>
Sent: Wednesday, June 3, 2026 4:12 PM
To: Pat Reporter <preporter@example.com>
Subject: minutes - Roe v. X.Y. Holding

Please send the minutes for Jane Roe v. X.Y. Holding Corporation, Index 712345/2021, of 6/3/2026, daily copy.
"""
TWO = """From: Dana Smith <dsmith@smithlaw.example>
Sent: Wednesday, June 3, 2026 5:12 PM
To: Pat Reporter <preporter@example.com>
Subject: RE: minutes - Roe v. X.Y. Holding

Thank you.

Dana Smith
Smith Law PLLC
100 Main Street, Suite 5
Kew Gardens, NY 11415
"""


def test_a_speed_follows_its_firm_when_a_later_document_names_the_firm():
    """Dana Smith's speed was set (Who ordered what, or Generate's question); a second e-mail signs "Smith Law
    PLLC" and the row's key becomes the firm's: the speed went with the old key, and the firm chose again."""
    s = pat_settings()
    job = Job(docs=[text_doc(s, ONE, "one.txt")])
    remerge(job, s)
    (before,) = [a.key() for a in job.case.attorneys]
    job.speeds = {before: "Daily"}
    job.docs.append(text_doc(s, TWO, "two.txt"))
    remerge(job, s)
    (after,) = [a.key() for a in job.case.attorneys]
    assert after != before and job.speeds == {after: "Daily"}


def test_a_run_speed_on_a_day_that_cant_be_split_is_kept(s):
    """A job of several days is ordered whole (its one run can't be cut): a speed set on that run in Excerpts...
    showed in the prices but was never kept."""
    from minute_filler.excerpts import Day, Run, store
    job = typed_day(100, "6/3/2026, 6/4/2026")
    day = Day(job, "6/3/2026, 6/4/2026", 100, [], False)
    store(day, [Run(day, 1, 100, [A, B], {A: "Daily"})], [counsel(), smith()])
    assert job.speeds == {A: "Daily"}


def test_a_speed_not_on_the_rate_sheet_stops_generate_before_anything_is_made(s, tmp_path):
    """Counsel & Counsel's speed was set to Immediate, then a sheet without Immediate chosen: the agreements and
    the MOFR were made and recorded, then the invoice failed (and Generate again made them twice)."""
    from minute_filler.deliver import generate, ledger_for
    own_sheet(s, tmp_path, "Rate,Original,Copy,Email,Index\nRegular,$4.30,$1.00,$1.00,$1.00\n"
                           "Expedite,$5.40,$1.10,$1.10,$1.10\n")
    job = typed_day()
    job.speeds = {A: "Immediate", B: "Expedite"}
    out = tmp_path / "made"
    with pytest.raises(ValueError, match="Immediate"):
        generate(job.case, s, out, ["agreement", "mofr", "invoice"], job.invoice_sets(),
                 ordered=job.ordered_pages(), speeds=job.ordered_speeds())
    assert not out.exists() or not any(out.rglob("*.pdf"))
    assert ledger_for(s).invoices() == [] and not ledger_for(s).activity()


def test_generate_all_says_why_a_day_of_three_speeds_isnt_invoiced(s):
    job = typed_day(attorneys=[counsel(), smith(), poe()])
    job.speeds = {A: "Daily", B: "Immediate", C: "Regular"}
    fill_jobs([job], s, outputs=["agreement", "invoice"])
    assert "invoice not made: the speeds need checking (6/3/2026: 3 different speeds" in job.error
    assert "Whose pages" not in job.error
    assert job.saved  # (the agreements are made)


# ------------------------------------------------------------------ files and folders


def test_a_file_in_the_way_of_the_case_folder(tmp_path):
    (tmp_path / "712345-2021").write_text("not a folder")
    job = typed_day()
    CaseFolder(tmp_path, "712345-2021", [job]).use()
    assert job.folder == "712345-2021 (2)"  # (no folder can be made where a file is: FileExistsError every time)


def test_case_folder_names_of_short_index_numbers_and_device_words():
    roe = make_case({**ROE, "index_no": "712222/24"})
    assert case_folder_name(roe, "{index}") == "712222-2024"  # (the same case as "712222/2024": one folder)
    assert case_folder_name(make_case(ROE), "{index}") == "712345-2021"
    assert case_folder_name(roe, "nul.txt") == "nul_.txt"  # (Windows reads "nul.txt_" as the device "nul")
    assert case_folder_name(roe, "CON") == "CON_"


def test_one_math_pdf_that_cant_be_saved_doesnt_lose_the_others(s, tmp_path, monkeypatch):
    from minute_filler import invoice_math
    job = typed_day()
    case, opts = joint_invoice([job])
    firms = firm_invoices(case, s, opts)
    made = [(f, f"2026-000{i}", tmp_path / f"Invoice 2026-000{i}.pdf") for i, f in enumerate(firms, 1)]
    real, calls = invoice_math.to_pdf, []

    def to_pdf(html, path):
        calls.append(path)
        if len(calls) == 2:
            raise PermissionError("the folder went away")
        return real(html, path)

    monkeypatch.setattr(invoice_math, "to_pdf", to_pdf)
    saved = invoice_math.save_math(made, "both")
    assert len(calls) == 3 and [p.name for p in saved] == [
        "Invoice 2026-0001 - the math.pdf",
        "Invoices 2026-0001 to 2026-0002 - Jane Roe v. X.Y. Holding Corporation - the math.pdf"]


def test_the_rate_sheet_fingerprint_is_of_the_file_as_it_was_read(tmp_path):
    from minute_filler.rates import load_sheet, sheet_fingerprint
    p = tmp_path / "Court Rates.csv"
    p.write_text("Rate,Original,Copy,Email,Index\nRegular,$4.30,$1.00,,\n", encoding="utf-8")
    sheet = load_sheet(p)
    seen = sheet_fingerprint(sheet)
    p.write_text("Rate,Original,Copy,Email,Index\nRegular,$4.30,$1.00,,\nHourly,$9.00,,,\n", encoding="utf-8")
    assert sheet_fingerprint(sheet) == seen  # (Use it anyway: the sheet the user was shown, not the file now)
    assert sheet_fingerprint(load_sheet(p)) != seen


# ------------------------------------------------------------------ the math's words


def test_the_math_names_the_speed_of_each_line(s):
    """A firm at Daily and Regular: a run it shared with a firm at the same speed says "at Daily", not at both."""
    from minute_filler.invoice_math import rows
    shares = [Share(20, 2, False, "Daily", (("Smith Law", "Daily"),)),
              Share(10, 2, False, "Daily", (("Smith Law", "Immediate"),)),
              Share(30, 1, False, "Regular")]
    q = quote_ordered(shares, s.sheet(), courthouses.split_rule(), False)
    labels = [r.label for r in rows(q)]
    assert "20 pages ordered by 2 firms at Daily" in labels
    assert not any("at Daily + Regular" in x for x in labels)


def test_a_line_in_parts_side_by_side_is_billed_once(s):
    """Smith Law's original of 60 pages ordered with a Daily firm and 60 alone is shown "in parts"; side by side
    it said "Each firm splits this cost"."""
    from minute_filler.invoice_math import ONCE, SPLITS, explain
    job = typed_day()
    job.portions = [(60, [A, B]), (120, [B])]
    job.speeds = {A: "Daily", B: "Immediate"}
    case, opts = joint_invoice([job])
    mine = [(f, "") for f in firm_invoices(case, s, opts) if f.atty.key() == B]
    html, _ = explain(mine, "firms")
    assert ONCE in html and SPLITS not in html


def test_the_default_invoice_texts_have_a_copy_of_the_mixed_speeds_row():
    from minute_filler.settings import MIXED_SPEEDS_ROW, default_invoice_texts
    assert any(r == MIXED_SPEEDS_ROW for r in default_invoice_texts())
    assert all(r is not MIXED_SPEEDS_ROW for r in default_invoice_texts())


# ------------------------------------------------------------------ the window


@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.dialogs import ClarifyDialog
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(ClarifyDialog, "exec", lambda self: qt.QDialog.Accepted)
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.outputs = ["agreement", "invoice"]
    s.save_math = "off"
    s.invoice_include_index = False
    return make_window(s)


def wait(win, qt, until, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        qt.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def answer(monkeypatch, qt, button: str) -> list:
    """Every QMessageBox is answered with the button whose text starts with `button`; returns their texts."""
    shown = []

    def exec_(box):
        shown.append(box.text())
        box._picked = next((b for b in box.buttons() if b.text().startswith(button)), None)
        return 0
    monkeypatch.setattr(qt.QMessageBox, "exec", exec_)
    monkeypatch.setattr(qt.QMessageBox, "clickedButton", lambda box: getattr(box, "_picked", None))
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: qt.QMessageBox.Yes)
    monkeypatch.setattr(qt.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    return shown


def one_day(win, qt, tmp_path, attorneys) -> Job:
    """A 30-page transcript of 6/3/2026 with these attorneys, shown in the window."""
    win.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026"))])
    wait(win, qt, lambda: len(win.jobs) == 1 and win.jobs[0].docs)
    job = win.jobs[0]
    job.case.attorneys = attorneys
    win._show_job()
    return job


def speed_box(win, name):
    from minute_filler.gui.main_window import ORDER_SPEED
    row = next(r for r in range(win.orders.rowCount()) if win.orders.item(r, 1).text() == name)
    return win.orders.cellWidget(row, ORDER_SPEED)


def test_a_day_held_for_three_speeds_is_said_not_crashed(window, qt, tmp_path, monkeypatch):
    """Three firms answered three speeds: Generate raised IndexError (it took the hold for Whose pages...), the
    Outputs box said "until Whose pages… is chosen" and the Invoice panel showed prices for an invoice it won't
    make."""
    job = one_day(window, qt, tmp_path, [counsel(), smith(), poe()])
    job.speeds = {A: "Daily", B: "Immediate", C: "Regular"}
    window._update_status()
    assert window.inv_info.text().startswith("⚠ The speeds need checking")
    assert "the speeds are checked" in window.output_counts["invoice"].text()
    shown = answer(monkeypatch, qt, "Yes")
    assert window._without_unchecked_invoice(job, ["agreement", "invoice"]) == ["agreement"]
    assert window._without_unchecked_invoice(job, ["invoice"]) == []
    assert any("The speeds need checking" in t for t in shown)
    window.fill()
    wait(window, qt, lambda: job.saved)
    assert [p.name.startswith("Minute") for p in job.saved] and not any("Invoice" in p.name for p in job.saved)


def test_the_card_shows_a_speed_set_on_a_firms_only_run(window, qt, tmp_path):
    job = one_day(window, qt, tmp_path, [counsel(), smith()])
    job.portions = [(10, [A, B], {A: "Daily"}), (30, [B])]  # (Counsel & Counsel's one run, at Daily)
    window._update_status()
    assert speed_box(window, "Alex B. Counsel").currentText() == "Daily"


def test_a_speed_spelt_otherwise_is_found_in_the_boxes(window, qt, tmp_path):
    from minute_filler.batch import SpeedAsk
    from minute_filler.gui.dialogs import SplitSpeedsDialog
    job = one_day(window, qt, tmp_path, [counsel(), smith()])
    job.speeds = {A: "Expedited", B: "Expedited"}  # (the sheet says "Expedite")
    window._update_status()
    window._who_ordered()
    assert window.excerpts.firm_speed_boxes[A].currentText() == "Expedite"
    speeds = [(sp.name, sp.name) for sp in window.s.sheet().speeds]
    dlg = SplitSpeedsDialog([SpeedAsk(job, A, "Alex B. Counsel", "Dana Smith", "Expedited")], speeds)
    assert dlg.combos[0].currentData() == "Expedite"


def test_the_excerpts_speed_warning_goes_once_fixed(window, qt, tmp_path):
    job = one_day(window, qt, tmp_path, [counsel(), smith(), poe()])
    job.speeds = {A: "Daily", B: "Immediate", C: "Regular"}
    window._update_status()
    window._who_ordered()
    w = window.excerpts
    assert "3 different speeds" in w.problem.text()
    w._firm_speed_chosen(C, "Daily")
    wait(window, qt, lambda: not job.invoice_hold())
    qt.QApplication.processEvents()
    assert w.problem.text() == ""


def test_firms_ticked_at_generate_are_asked_their_speeds(window, qt, tmp_path, monkeypatch):
    """Nobody ticked: Generate asks who ordered (both firms ticked), then which speed each ordered; the split
    order was billed at the form's speed without a question."""
    from minute_filler.gui.dialogs import ClarifyDialog, SplitSpeedsDialog
    a, b = counsel(), smith()
    a.checked = b.checked = False
    job = one_day(window, qt, tmp_path, [a, b])
    window.s.outputs = ["invoice"]
    monkeypatch.setattr(ClarifyDialog, "checked_attorneys", lambda self: [0, 1])
    asked = []

    def daily(dlg):
        asked.append([x.key for x in dlg.asks] if hasattr(dlg, "asks") else len(dlg.combos))
        for cb in dlg.combos:
            cb.setCurrentIndex(cb.findData("Daily"))
        return SplitSpeedsDialog.Accepted

    monkeypatch.setattr(SplitSpeedsDialog, "exec", daily)
    window.fill()
    wait(window, qt, lambda: job.saved)
    assert asked and job.speeds == {A: "Daily", B: "Daily"}


def test_index_numbers_approved_are_those_the_box_named(window, qt, tmp_path, monkeypatch):
    """A third document arrives while "Different index numbers" is on screen: "Generate anyway" covered it too,
    unseen. It is asked about again, and the list's ⚠ follow the answer."""
    from minute_filler.gui.main_window import MainWindow
    window.s.outputs = ["agreement"]
    folder = tmp_path / "in"
    folder.mkdir()
    for name, index in (("a.txt", "712222-2024"), ("b.txt", "700001-2025")):
        (folder / name).write_text(invoice_text(index=index))
        window.add_files([str(folder / name)])
        wait(window, qt, lambda n=name: any(d.ing.name == n for d in window.cur.docs))
    job = window.cur
    late = text_doc(window.s, invoice_text(index="733333-2022"), "c.txt")
    texts = []

    def exec_(box):
        texts.append(box.text())
        if len(texts) == 1:
            job.docs.append(late)  # (a read that ended while the box was open)
        box._picked = next(b for b in box.buttons() if b.text().startswith("Generate anyway"))
        return 0

    monkeypatch.setattr(qt.QMessageBox, "exec", exec_)
    monkeypatch.setattr(qt.QMessageBox, "clickedButton", lambda box: getattr(box, "_picked", None))
    assert MainWindow._ask_index_numbers(window, [job])
    assert len(texts) == 2 and "c.txt" not in texts[0] and "c.txt" in texts[1]
    assert sorted(job.index_numbers_ok) == sorted(d.key() for d in job.docs) and not job.index_number_asks()


def test_a_documents_index_warning_is_against_the_jobs_own_number(window, qt, tmp_path):
    """b.txt 700001/2025 (the job's), a.txt and c.txt 712222/2024: a.txt and c.txt were said to have a number "on
    no other document of this job", though they share it."""
    folder = tmp_path / "in"
    folder.mkdir()
    for name, index in (("b.txt", "700001-2025"), ("a.txt", "712222-2024"), ("c.txt", "712222-2024")):
        (folder / name).write_text(invoice_text(title="Roe v Doe" if name == "b.txt" else "Smith v Jones",
                                                index=index))
        window.add_files([str(folder / name)])
        wait(window, qt, lambda n=name: any(d.ing.name == n for d in window.cur.docs))
    assert window.cur.case.get("index_no") == "700001/2025"
    rows = {window.job_list.topLevelItem(0).child(i).data(0, window._DOC).ing.name:
            window.job_list.topLevelItem(0).child(i) for i in range(3)}
    assert not rows["b.txt"].text(0).startswith("⚠")
    for name in ("a.txt", "c.txt"):
        assert rows[name].text(0).startswith("⚠")
        assert "Its index number (712222/2024) isn't this job's (700001/2025)" in rows[name].toolTip(0)
    # the index number typed in is the job's: the ⚠ goes to the document that doesn't have it, wherever it is
    from minute_filler.models import SRC_USER, FieldState
    window.cur.case.fields["index_no"] = FieldState("712222/2024", SRC_USER, 1.0)
    window._refresh_job_labels()
    assert rows["b.txt"].text(0).startswith("⚠") and not rows["a.txt"].text(0).startswith("⚠")
    assert "Its index number (700001/2025) isn't this job's (712222/2024)" in rows["b.txt"].toolTip(0)


def test_a_case_folder_chosen_isnt_kept_after_go_back_at_the_preview(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import PreviewDialog
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["agreement"]
    s.case_folder_existing = "new"
    window = make_window(s, preview=True)
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "earlier.pdf").write_bytes(b"")
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.txt").write_text(invoice_text())
    window.add_files([str(tmp_path / "in" / "a.txt")])
    wait(window, qt, lambda: window.cur.docs)
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(PreviewDialog, "exec", lambda self: qt.QDialog.Rejected)
    window.fill()
    assert not window.cur.saved and not window.cur.folder  # (nothing saved: chosen again next time)
    monkeypatch.setattr(PreviewDialog, "exec", lambda self: qt.QDialog.Accepted)
    window.fill()
    wait(window, qt, lambda: window.cur.saved)
    assert window.cur.folder == "712222-2024 (2)" and {p.parent.name for p in window.cur.saved} == {"712222-2024 (2)"}


def test_generate_all_keeps_no_case_folder_after_go_back_at_the_preview(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import PreviewDialog
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["agreement"]
    s.case_folder_existing = "new"
    window = make_window(s, preview=True)
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "earlier.pdf").write_bytes(b"")
    (tmp_path / "in").mkdir()
    for name, date in (("a.txt", "5-22-2026"), ("b.txt", "5-26-2026")):
        (tmp_path / "in" / name).write_text(invoice_text(date=date))
    window.add_files([str(tmp_path / "in" / n) for n in ("a.txt", "b.txt")], batch=True)
    wait(window, qt, lambda: len(window.jobs) == 2)
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(PreviewDialog, "exec", lambda self: qt.QDialog.Rejected)
    window.fill_all_jobs()
    wait(window, qt, lambda: not window.filling)
    assert not any(j.saved or j.folder for j in window.jobs)  # (Go back: nothing saved, nothing chosen)
    monkeypatch.setattr(PreviewDialog, "exec", lambda self: qt.QDialog.Accepted)
    window.fill_all_jobs()
    wait(window, qt, lambda: all(j.saved for j in window.jobs), 60)
    assert {j.folder for j in window.jobs} == {"712222-2024 (2)"}  # (kept: the next Generate uses it)


def test_generate_all_saves_the_math_in_the_layout_last_shown(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.preview import PreviewDialog
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["invoice"]
    s.records_dir = str(tmp_path / "records")
    s.save_math, s.math_layout, s.case_folders = "each", "invoice", False
    window = make_window(s, preview=True)
    window.add_files([str(transcript_pdf(tmp_path / "d1.pdf", pages=30)),
                      str(transcript_pdf(tmp_path / "d2.pdf", pages=24, date="June 4, 2026"))], batch=True)
    wait(window, qt, lambda: len(window.jobs) == 2)
    for j in window.jobs:
        j.case.attorneys = [counsel()]
    window._show_job()  # (the table on screen is read back first)
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    used = []
    from minute_filler import invoice_math
    real = invoice_math.save_math
    monkeypatch.setattr(invoice_math, "save_math", lambda made, save, layout: (
        save != "off" and used.append(layout)) or real(made, save, layout))  # (the preview's own: off)

    def switched(dlg):
        window.s.math_layout = "firms"  # (the switch on the preview's math tab)
        return qt.QDialog.Accepted

    monkeypatch.setattr(PreviewDialog, "exec", switched)
    window.fill_all_jobs()
    wait(window, qt, lambda: used, 60)
    assert set(used) == {"firms"}


def test_the_generate_all_count_follows_save_the_math(window, qt, tmp_path):
    window.s.outputs, window.s.save_math = ["invoice"], "both"
    window.add_files([str(transcript_pdf(tmp_path / "d1.pdf", pages=30)),
                      str(transcript_pdf(tmp_path / "d2.pdf", pages=24, date="June 4, 2026"))], batch=True)
    wait(window, qt, lambda: len(window.jobs) == 2)
    for j in window.jobs:
        j.case.attorneys = [counsel(), smith()]
    window._update_status()
    before = window._all_counts["math"]
    window.s.save_math = "off"  # (chosen under the math, in the preview or the math window)
    window._math_saves_kept("both")
    assert before and window._all_counts["math"] == 0


def test_go_back_to_the_sheet_before_leaves_the_jobs_speeds(window, qt, tmp_path, monkeypatch):
    """Regular chosen for the job (Settings would pick Expedite); a sheet of Expedite alone, incomplete, picked:
    the job was priced from it before the warning (Regular reset to Expedite), and Go back didn't bring it back."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    import shutil
    from minute_filler.rates import BUNDLED_DIR
    shutil.copy(BUNDLED_DIR / "Sample Rates.csv", folder / "Sample Rates.csv")
    (folder / "Court Rates.csv").write_text("Rate,Original,Copy,Email,Index\nExpedite,$5.40,$1.10,,\n",
                                            encoding="utf-8")
    window.s.rate_sheets_dir = str(folder)
    window.s.reload_rates()
    window._fill_sheet_box()
    job = one_day(window, qt, tmp_path, [counsel()])
    assert job.case.get("delivery") == "Expedite"  # (the Settings rule's)
    window.delivery.setCurrentIndex(window.delivery.findData("Regular"))
    window._delivery_changed(window.delivery.currentIndex())  # (picked by the user)
    assert job.case.get("delivery") == "Regular"
    answer(monkeypatch, qt, "Go back to")
    window.sheet_box.setCurrentIndex(window.sheet_box.findData("Court Rates"))
    assert window.s.rate_sheet == "Sample Rates"
    assert job.case.get("delivery") == "Regular" and job.case.get("rate") == "4.30"


def test_go_back_is_offered_when_the_sheet_named_in_settings_is_gone(window, qt, tmp_path, monkeypatch):
    folder = tmp_path / "sheets"
    folder.mkdir()
    import shutil
    from minute_filler.rates import BUNDLED_DIR
    shutil.copy(BUNDLED_DIR / "Sample Rates.csv", folder / "Sample Rates.csv")
    (folder / "Court Rates.csv").write_text("Rate,Original,Copy,Email,Index\nRegular,$4.30,$1.00,,\n",
                                            encoding="utf-8")
    window.s.rate_sheets_dir, window.s.rate_sheet = str(folder), "Gone Rates"  # (deleted: Sample Rates in use)
    window.s.reload_rates()
    window._fill_sheet_box()
    assert window.s.sheet().name == "Sample Rates"
    buttons = []

    def exec_(box):
        buttons.extend(b.text() for b in box.buttons())
        return 0

    monkeypatch.setattr(qt.QMessageBox, "exec", exec_)
    window.sheet_box.setCurrentIndex(window.sheet_box.findData("Court Rates"))
    assert "Go back to Sample Rates" in buttons


def test_a_split_speed_answer_follows_a_firm_renamed_meanwhile(window, qt, tmp_path, monkeypatch):
    from minute_filler.gui.dialogs import SplitSpeedsDialog
    a, b = Attorney(name="Alex B. Counsel", checked=True), smith()
    job = one_day(window, qt, tmp_path, [a, b])
    old = a.key()

    def renamed(dlg):
        job.case.attorneys = [counsel(), smith()]  # (an AI answer filled in its firm while the box was open)
        for cb in dlg.combos:
            cb.setCurrentIndex(cb.findData("Daily"))
        return SplitSpeedsDialog.Accepted

    monkeypatch.setattr(SplitSpeedsDialog, "exec", renamed)
    assert window._ask_split_speeds([job])
    assert old != A and job.speeds == {A: "Daily", B: "Daily"}


def test_removing_the_last_document_of_a_job_in_a_batch_removes_the_job(window, qt, tmp_path):
    window.add_files([str(transcript_pdf(tmp_path / "d1.pdf", pages=30)),
                      str(transcript_pdf(tmp_path / "d2.pdf", pages=24, date="June 4, 2026"))], batch=True)
    wait(window, qt, lambda: len(window.jobs) == 2)
    first = window.jobs[0]
    window._remove_doc(first, first.docs[0])  # (a menu opened on the job alone, a read made it a batch meanwhile)
    assert first not in window.jobs and len(window.jobs) == 1 and window.cur is window.jobs[0]


# ------------------------------------------------------------------ found by the review of the fixes above


def boxes(monkeypatch, qt, rules, hook=None) -> list:
    """Every QMessageBox answered: rules = [(title or text start, button text start)]; hook(box) runs first (a
    read ending while the box is open). Returns the (title, text) of each box shown."""
    shown = []

    def exec_(box):
        shown.append((box.windowTitle(), box.text()))
        if hook is not None:
            hook(box)
        want = next((b for t, b in rules if box.windowTitle().startswith(t) or box.text().startswith(t)), None)
        box._picked = next((b for b in box.buttons() if want and b.text().startswith(want)), None)
        return 0

    monkeypatch.setattr(qt.QMessageBox, "exec", exec_)
    monkeypatch.setattr(qt.QMessageBox, "clickedButton", lambda box: getattr(box, "_picked", None))
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: qt.QMessageBox.Yes)
    return shown


def court_rates(window, tmp_path, text="Rate,Original,Copy,Email,Index\nExpedite,$9.90,$1.10,,\n"):
    """Sample Rates and an incomplete "Court Rates" (Expedite alone, at $9.90) in the window's rate sheet folder."""
    import shutil
    from minute_filler.rates import BUNDLED_DIR
    folder = tmp_path / "sheets"
    folder.mkdir()
    shutil.copy(BUNDLED_DIR / "Sample Rates.csv", folder / "Sample Rates.csv")
    (folder / "Court Rates.csv").write_text(text, encoding="utf-8")
    window.s.rate_sheets_dir = str(folder)
    window.s.reload_rates()
    window._fill_sheet_box()


def test_a_read_ending_while_the_sheet_warning_is_open_keeps_the_sheet_gone_back_to(window, qt, tmp_path,
                                                                                     monkeypatch):
    """An AI answer (or a read) arriving while "Your rate sheet is incomplete" is open merged the job with the sheet
    just picked (Settings named it already); Go back: the job kept that sheet's $9.90."""
    court_rates(window, tmp_path)
    job = one_day(window, qt, tmp_path, [counsel()])
    assert (job.case.get("delivery"), job.case.get("rate")) == ("Expedite", "5.40")

    def meanwhile(box):
        if box.windowTitle().startswith("Your rate sheet is incomplete"):
            window._remerge(job)

    boxes(monkeypatch, qt, [("Your rate sheet is incomplete", "Go back to")], meanwhile)
    window.sheet_box.setCurrentIndex(window.sheet_box.findData("Court Rates"))
    assert window.s.rate_sheet == "Sample Rates" and job.case.get("rate") == "5.40"


def test_go_back_after_settings_chose_an_incomplete_sheet_keeps_the_speed_chosen(window, qt, tmp_path, monkeypatch):
    """U12's case through Settings (or an import): every job was priced from the new sheet before the warning."""
    court_rates(window, tmp_path, "Rate,Original,Copy,Email,Index\nExpedite,$5.40,$1.10,,\n")
    job = one_day(window, qt, tmp_path, [counsel()])
    window.delivery.setCurrentIndex(window.delivery.findData("Regular"))
    window._delivery_changed(window.delivery.currentIndex())
    shown = boxes(monkeypatch, qt, [("Your rate sheet is incomplete", "Go back to")])
    window.s.rate_sheet = "Court Rates"  # (what the Settings window's OK did)
    window._settings_changed(sheet="Sample Rates")
    assert [t for t, _ in shown] == ["Your rate sheet is incomplete"]
    assert window.s.rate_sheet == "Sample Rates"
    assert (job.case.get("delivery"), job.case.get("rate")) == ("Regular", "4.30")
    from minute_filler.settings import Settings
    assert Settings.load().rate_sheet == "Sample Rates"  # (saved: Settings had saved the other)


def test_generate_all_go_back_at_the_later_index_question_puts_the_folders_back(tmp_path, monkeypatch, make_window,
                                                                               qt):
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["agreement"]
    s.records_dir = str(tmp_path / "records")
    window = make_window(s)
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "earlier.pdf").write_bytes(b"")
    (tmp_path / "in").mkdir()
    for name, date in (("a.txt", "5-22-2026"), ("b.txt", "5-26-2026")):
        (tmp_path / "in" / name).write_text(invoice_text(date=date))
    window.add_files([str(tmp_path / "in" / n) for n in ("a.txt", "b.txt")], batch=True)
    wait(window, qt, lambda: len(window.jobs) == 2)
    late = text_doc(window.s, invoice_text(index="733333-2022", date="5-22-2026"), "c.txt")

    def meanwhile(box):
        if box.windowTitle().startswith("The case folder"):
            window.jobs[0].docs.append(late)  # (a read that ended while the case folder question was open)

    shown = boxes(monkeypatch, qt, [("The case folder", "New folder"), ("Different index numbers", "Go back")],
                  meanwhile)
    window.fill_all_jobs()
    wait(window, qt, lambda: not window.filling)
    assert "Different index numbers" in [t for t, _ in shown] and not any(j.saved for j in window.jobs)
    assert not any(j.folder for j in window.jobs)


def test_every_day_of_a_case_goes_beside_a_file_in_the_way(tmp_path):
    """A file "712345-2021" in Save to: day 1 went to "712345-2021 (2)", day 2 (Generated later) to "(3)", and
    the folder's files were never counted, so neither asked nor added to."""
    from minute_filler.batch import case_folders
    s = pat_settings()
    s.output_dir = str(tmp_path)
    (tmp_path / "712345-2021").write_text("not a folder")
    day1, day2 = typed_day(), typed_day(date="6/4/2026")
    (first,) = case_folders([day1], s, ["agreement"])
    first.use()
    assert (day1.folder, day1.folder_named) == ("712345-2021 (2)", "712345-2021")
    (tmp_path / "712345-2021 (2)").mkdir()
    (tmp_path / "712345-2021 (2)" / "Minute Agreement.pdf").write_bytes(b"")  # (day 1's file, saved)
    (second,) = case_folders([day2], s, ["agreement"])
    assert second.files == 1  # (asked about, as Settings says)
    second.use()
    assert (day2.folder, day2.folder_named) == ("712345-2021 (2)", "712345-2021")
    assert not case_folders([day1, day2], s, ["agreement"])  # (both chosen: not listed again)
    second.use(new=True)
    assert day2.folder == "712345-2021 (3)"


def test_a_short_index_number_alone_is_written_in_full():
    for typed, folder in (("712222/24", "712222-2024"), ("712345/99", "712345-99"), ("712345/2021E", "712345-2021E"),
                          ("LT-012345-21/QU", "LT-012345-21-QU")):
        assert case_folder_name(make_case({**ROE, "index_no": typed}), "{index}") == folder


INDEX_ON_DAILY_ONLY = ("Rate,Original,Copy,Email,Index,Days\nRegular,$4.30,$1.00,$1.00,$0.00,21\n"
                       "Expedite,$5.40,$1.10,$1.10,$0.00,7\nDaily,$6.50,$1.25,$1.25,$1.25,1\n"
                       "Immediate,$7.60,$1.45,$1.45,$0.00,0\n")


def test_the_index_is_asked_about_once_the_firms_and_their_speeds_are_known(window, qt, tmp_path, monkeypatch):
    """40 pages typed of an 83-page transcript, an index price only at Daily, nobody ticked: Generate said "No index
    price" (judged at the form's speed) before it asked who ordered and at which speed (both firms, Daily), and
    the invoice then charged an index anyway."""
    from minute_filler.gui.dialogs import ClarifyDialog, SplitSpeedsDialog
    s = window.s
    folder = tmp_path / "sheets"
    folder.mkdir()
    (folder / "Court Rates.csv").write_text(INDEX_ON_DAILY_ONLY, encoding="utf-8")
    s.rate_sheets_dir, s.rate_sheet, s.outputs, s.invoice_include_index = str(folder), "Court Rates", ["invoice"], True
    s.reload_rates()
    window._fill_sheet_box()
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=83, date="June 3, 2026"))])
    wait(window, qt, lambda: window.cur.docs)
    a, b = counsel(), smith()
    a.checked = b.checked = False
    window.cur.case.attorneys = [a, b]
    window._show_job()
    window.rows["est_pages"].choose("40")
    monkeypatch.setattr(ClarifyDialog, "checked_attorneys", lambda self: [0, 1])

    def daily(dlg):
        for cb in dlg.combos:
            cb.setCurrentIndex(cb.findData("Daily"))
        return SplitSpeedsDialog.Accepted

    monkeypatch.setattr(SplitSpeedsDialog, "exec", daily)
    shown = boxes(monkeypatch, qt, [("Index for this invoice?", "Go back")])
    window.fill()
    titles = [t for t, _ in shown]
    assert "Index for this invoice?" in titles and "No index price" not in titles

