"""A transcript written by several reporters (their initials alternate at the foot of the pages) is billed for
the user's own pages only: Pat Reporter ("pr") bills the pages with "pr", not Dana Smith's ("ds"). A transcript
whose pages are all someone else's, or that begins with pages nobody's initials are on, gets no invoice until
Whose pages... says whose pages to bill; a typed Pages field and a transcript without initials are billed as
they are. Excerpts (who ordered which pages) count the billed pages of the stretch each firm ordered. Also an
excerpt cut out of a day (excerpts.carve), Enter in the Excerpts table, the Whose pages window and the Invoice
panel. All names are made up."""
import os

import pytest

from minute_filler.batch import files_to_make, fill_jobs, group, read_docs
from minute_filler.deliver import ledger_for
from minute_filler.models import SRC_USER, Attorney, FieldState
from minute_filler.takes import my_initials, page_owners, PageMark

from helpers import pat_settings, transcript_pdf

PR, DS = "pr", "ds"


def alex():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def dana():
    return Attorney(name="Dana Smith", firm="Smith Law", checked=True)


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def job_with(tmp_path, s, initials, name="Roe 6-3-2026.pdf", attorneys=None):
    """The one job made from a transcript with these initials on its pages (as many pages as `initials`)."""
    path = transcript_pdf(tmp_path / name, len(initials), initials=initials)
    docs, errors = read_docs([str(path)], s)
    assert not errors
    job, = group(docs, s)
    if attorneys is not None:
        job.case.attorneys = attorneys
    return job


def test_page_owners_and_my_initials():
    marks = [PageMark(1, ""), PageMark(2, "pr"), PageMark(3, ""), PageMark(4, "ds"), PageMark(5, "")]
    assert page_owners(marks) == ["", "pr", "pr", "ds", "ds"]  # unmarked: the take it is in; at the start: ""
    assert my_initials("Pat Q. Reporter") == {"pr", "pqr"} and my_initials("Pat Reporter", "P.X.") == {"px"}


def test_only_my_pages_are_billed(tmp_path, s):
    job = job_with(tmp_path, s, [PR, PR, PR, DS, DS, DS, DS, PR, PR, DS])
    assert job.own == (PR,) and job.transcript_pages() == 10
    assert job.invoice_pages() == 5 and not job.ownership_problem()
    assert job.case.get("est_pages") == "5" and "10" in job.case.fields["est_pages"].alternatives
    assert [d.key() for d in job.shared_transcripts()] == [job.docs[0].key()]
    job.case.attorneys = [alex()]
    fill_jobs([job], s, outputs=["invoice"])
    assert not job.error and ledger_for(s).invoices()[0].pages == 5


def test_someone_elses_transcript_is_held_until_whose_pages_says(tmp_path, s):
    job = job_with(tmp_path, s, [DS] * 8, attorneys=[alex()])
    assert job.invoice_pages() == 0
    why = job.ownership_problem()
    assert why.startswith("none of the pages of Roe 6-3-2026.pdf carry your initials (PR)") and "DS's" in why
    assert job.makeable(["invoice"]) == ["invoice"]  # held with the reason, not dropped as "no pages"
    assert job.invoice_hold().startswith("Whose pages… needs choosing")
    assert files_to_make([job], ["invoice"], s) == 0
    fill_jobs([job], s, outputs=["invoice"])
    assert job.error.startswith("invoice not made: choose under Whose pages…") and not ledger_for(s).invoices()
    job.page_basis[job.docs[0].key()] = DS  # bill Dana's pages (e.g. made for her)
    assert job.invoice_pages() == 8 and not job.ownership_problem()
    job.page_basis[job.docs[0].key()] = "*"  # or the whole transcript
    assert job.invoice_pages() == 8 and not job.invoice_hold()
    job.page_basis[job.docs[0].key()] = "kl"  # initials no longer on its pages
    assert "KL's initials are no longer on its pages" in job.ownership_problem()


def test_front_pages_without_initials_are_asked_about(tmp_path, s):
    from test_runsheet import TALK, TITLE_1, TITLE_2, transcript
    pages = [(310, "", "", TITLE_1), (311, "", "", TITLE_2)] + [
        (312 + i, x, "Proceedings", TALK) for i, x in enumerate([PR, PR, DS, DS, DS, DS])]
    docs, errors = read_docs([str(transcript(tmp_path / "Roe front 6-3-2026.pdf", pages))], s)
    job, = group(docs, s)
    assert "the first 2 pages have no reporter's initials" in job.ownership_problem()
    key = job.docs[0].key()
    job.front_owner[key] = PR
    assert not job.ownership_problem() and job.invoice_pages() == 4
    job.front_owner[key] = "none"
    assert job.invoice_pages() == 2
    # one reporter alone: the unmarked first pages are theirs, nothing to ask
    alone = job_with(tmp_path, s, ["", PR, PR, PR], name="Roe alone 6-3-2026.pdf")
    assert not alone.ownership_problem() and alone.invoice_pages() == 4


def test_no_initials_or_a_typed_page_count_are_billed_as_they_are(tmp_path, s):
    plain = job_with(tmp_path, s, [""] * 6)
    assert plain.invoice_pages() == 6 and not plain.shared_transcripts() and not plain.ownership_problem()
    job = job_with(tmp_path, s, [DS] * 6, name="Roe ds 6-3-2026.pdf")
    job.case.fields["est_pages"] = FieldState("7", SRC_USER, 1.0, [])  # typed: billed whoever wrote them
    assert job.invoice_pages() == 7 and not job.ownership_problem()


def test_my_initials_unknown_on_a_shared_transcript(tmp_path):
    s = pat_settings()
    s.profile.name = "Pat"  # one name: no initials can be made from it
    job = job_with(tmp_path, s, [PR, DS, DS, PR])
    assert job.own == () and "your initials aren't known" in job.ownership_problem()
    lone = job_with(tmp_path, s, [DS, DS], name="Roe lone 6-3-2026.pdf")
    assert lone.invoice_pages() == 2 and not lone.ownership_problem()  # one reporter: as before


def test_an_excerpt_bills_the_firm_my_pages_of_its_stretch(tmp_path, s):
    # pages 1-3 Pat, 4-7 Dana Smith (the reporter), 8-9 Pat, 10 Dana: Pat bills 5 pages
    job = job_with(tmp_path, s, [PR, PR, PR, DS, DS, DS, DS, PR, PR, DS], attorneys=[alex(), dana()])
    A, B = alex().key(), dana().key()
    assert job.portion_pages() == 10  # the Excerpts window counts every page of the day
    job.portions = [(5, [A, B]), (10, [A])]  # the attorney Dana Smith ordered pages 1-5: Pat wrote 3 of them
    assert not job.portions_problem()
    order, = job.invoice_orders()
    assert [p.pages for p in order.portions] == [3, 2] and order.pages == 5
    fill_jobs([job], s, outputs=["invoice"])
    pages = {i.bill_to: i.pages for i in ledger_for(s).invoices()}
    assert pages == {"Alex B. Counsel": 5, "Dana Smith": 3}
    assert job.printed_pages()[:3] == [101, 102, 103]


@pytest.mark.parametrize("dana_day, reporters, shared", [(0, "PR 6, DS 4", True), (1, "PR 6", False)])
def test_a_firm_on_a_joint_invoice_names_the_reporters_of_its_own_days(tmp_path, s, dana_day, reporters, shared):
    from minute_filler.batch import invoice_groups, joint_invoice
    from minute_filler.invoice import firm_invoices, text_conditions
    # 6/3: Pat and Dana Smith (the reporter) wrote it; 6/4: Pat alone. The attorney Dana Smith orders one day.
    p1 = transcript_pdf(tmp_path / "Roe 6-3.pdf", 10, initials=[PR, PR, PR, PR, DS, DS, DS, PR, PR, DS])
    p2 = transcript_pdf(tmp_path / "Roe 6-4.pdf", 6, date="June 4, 2026", initials=[PR] * 6)
    docs, errors = read_docs([str(p1), str(p2)], s)
    jobs = group(docs, s)
    for i, job in enumerate(jobs):
        job.case.attorneys = [alex(), dana()] if i == dana_day else [alex()]
    case, opts = joint_invoice(*invoice_groups(jobs, s))
    assert opts.reporters == "PR 12, DS 4"  # the joint invoice: both days
    firm = next(f for f in firm_invoices(case, s, opts) if f.atty.name == "Dana Smith")
    assert firm.opts.reporters == reporters
    assert ("shared" in text_conditions(firm.quotes, firm.opts)) is shared


def test_initials_corrected_while_the_batch_runs_bill_the_day_again(tmp_path, s):
    from copy import deepcopy
    from dataclasses import replace
    from minute_filler.batch import remerge
    from minute_filler.gui.main_window import _billing_changed
    job = job_with(tmp_path, s, [PR] * 10 + [DS] * 10)
    copy = replace(job, case=deepcopy(job.case), docs=list(job.docs), page_basis=dict(job.page_basis),
                   front_owner=dict(job.front_owner), portions=deepcopy(job.portions))
    # My info: the initials corrected to DS while Generate all works on the copy: as many pages, other ones
    s.profile.initials = "D.S."
    remerge(job, s)
    assert job.invoice_pages() == copy.invoice_pages() == 10 and job.own != copy.own
    assert _billing_changed(job, copy)


def test_an_excerpt_span_without_printed_numbers_in_order(tmp_path, s):
    job = job_with(tmp_path, s, [""] * 10, attorneys=[alex(), dana()])
    A, B = alex().key(), dana().key()
    job.portions = [(5, [A, B]), (10, [A])]
    # Pages typed in: no printed numbers are known, so the stretch is named by its place in the day
    job.case.fields["est_pages"] = FieldState("10", SRC_USER, 1.0, [])
    assert job.printed_pages() == []
    assert [p.span for p in job.invoice_orders()[0].portions] == ["pages 1–5", "pages 6–10"]
    # a second volume that numbers its pages from 1 again: the stretch over the join runs backwards
    job.case.fields["est_pages"] = FieldState("", "", 0.0, [])
    job.printed_pages = lambda: [101, 102, 103, 104, 105, 106, 107, 1, 2, 3]
    assert [p.span for p in job.invoice_orders()[0].portions] == ["pp. 101–105", "pages 6–10"]


def test_an_excerpt_is_cut_out_of_the_day():
    """Excerpts… (excerpts.carve): a run typed in a day's one run is cut out of it ("377-397", printed
    numbers, is pages 20-40 of the day) and the pages around it keep their firms; typed past the day's last
    page, it is refused (ValueError)."""
    from minute_filler.excerpts import Day, Run, carve
    day = Day(None, "6/3/2026", 100, list(range(358, 458)))
    runs = carve([Run(day, 1, 100, ["a", "b"])], 0, *day.parse("377-397"))
    assert [(r.start, r.end, r.keys) for r in runs] == [(1, 19, ["a", "b"]), (20, 40, ["a", "b"]),
                                                       (41, 100, ["a", "b"])]
    assert day.span(20, 40) == "377–397"
    with pytest.raises(ValueError):
        carve(runs, 1, 90, 120)
    plain = Day(None, "6/4/2026", 30, [None] * 30)  # printed numbers not known: places in the day
    assert not plain.numbered and plain.parse("5-9") == (5, 9) and plain.span(5, 9) == "5–9"


# ------------------------------------------------------------------ the window

@pytest.fixture
def window(tmp_path, monkeypatch, make_window):
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return make_window(s)


def load(window, path):
    """Drops a file on the window and waits until it is read."""
    import time
    from PySide6 import QtWidgets
    window.add_files([str(path)])
    end = time.time() + 20
    while time.time() < end and (window.work or not window.cur.docs):
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def test_the_invoice_panel_shows_my_pages_and_asks_whose(window, tmp_path, monkeypatch):
    from minute_filler.gui.dialogs import PagesOwnerDialog
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    load(window, transcript_pdf(tmp_path / "Roe 6-3-2026.pdf", 6, initials=[PR, PR, DS, DS, DS, PR]))
    window.output_boxes["invoice"].setChecked(True)
    window._refresh_outputs()
    assert window.inv_pages_info.text() == "Your pages: 3 of 6"
    assert window.inv_form.isRowVisible(window.inv_pages_row)

    shown = []

    def choose(self):
        shown.append([i["key"] for i in self.items])
        self.items[0]["radios"]["*"].setChecked(True)  # the whole transcript
        return 1
    monkeypatch.setattr(PagesOwnerDialog, "exec", choose)
    assert window._whose_pages(window.cur)
    assert shown and window.cur.invoice_pages() == 6 and window.rows["est_pages"].text() == "6"
    assert window.inv_pages_info.text() == "Chosen pages: 6 of 6"


def test_a_held_transcript_with_a_long_name_does_not_widen_the_invoice_panel(window, tmp_path, qt):
    # the reason names the file: shown in full it made the Invoice panel (and the window) wider than the screen
    name = "Jane Roe v. X.Y. Holding Corporation 712345-2021 Trial Transcript Volume 3 June 3 2026 Dana.pdf"
    window.resize(1320, 860)
    window.show()
    load(window, transcript_pdf(tmp_path / name, 6, initials=[DS] * 6))  # none of the pages are Pat's
    window.output_boxes["invoice"].setChecked(True)
    window._refresh_outputs()
    for _ in range(5):
        qt.QApplication.processEvents()
    assert window.cur.ownership_problem() and name in window.cur.ownership_problem()
    page, panel = window.page_scroll, window._output_cols[1]  # (the Invoice panel)
    assert page.widget().minimumSizeHint().width() <= page.viewport().width()
    assert window.inv_info.minimumSizeHint().width() <= panel.width()
    assert panel.sizeHint().width() <= window.width() // 3  # (the panels are laid out by their sizeHint)
    assert window.inv_info.text() == "⚠ Choose whose pages to bill (Whose pages…)"
    assert name in window.inv_info.toolTip()
    # any other warning naming the file is broken into lines no longer than the prices'
    from minute_filler.gui.main_window import WARN_WIDTH
    window._warn(f"⚠ could not read {name.replace(' ', '_')}")
    assert all(len(line) <= WARN_WIDTH for line in window.inv_info.text().splitlines())


def test_whose_pages_dialog(qt):
    """Whose pages...: the first pages without initials must be given an owner; the user's and another
    reporter's pages can be ticked together, the last box ticked stays ticked, and the whole transcript goes
    alone."""
    from minute_filler.gui.dialogs import PagesOwnerDialog
    owners = ["", "", PR, PR, DS, DS]
    dlg = PagesOwnerDialog([("k", "Roe.pdf", owners)], {PR}, {}, {})
    item = dlg.items[0]
    assert item["radios"]["me"].isChecked() and "My pages (2)" == item["radios"]["me"].text()
    assert dlg.problem()  # whose the first 2 pages are must be chosen
    item["front"].setCurrentIndex(item["front"].findData(PR))
    assert not dlg.problem() and item["radios"]["me"].text() == "My pages (4)"
    item["radios"][DS].setChecked(True)  # DS's pages too, on invoices in DS's name
    dlg.accept()
    assert dlg.values() == ({"k": ["me", DS]}, {"k": PR})
    item["radios"]["me"].setChecked(False)
    assert dlg.values()[0] == {"k": DS}
    item["radios"][DS].setChecked(False)  # the last box ticked stays ticked
    assert item["radios"][DS].isChecked()
    item["radios"]["*"].setChecked(True)  # the whole transcript goes alone
    assert dlg.values()[0] == {"k": "*"} and not item["radios"][DS].isChecked()
    # without the user's initials, "My pages" can't be chosen
    unknown = PagesOwnerDialog([("k", "Roe.pdf", [PR, DS])], set(), {}, {})
    assert not unknown.items[0]["radios"]["me"].isEnabled() and unknown.items[0]["radios"]["*"].isChecked()


def test_whose_pages_still_counts_pages_without_initials_set_to_count_for_nobody(qt):
    from minute_filler.gui.dialogs import PagesOwnerDialog
    dlg = PagesOwnerDialog([("k", "Roe.pdf", ["", "", PR, PR, DS, DS])], {PR}, {}, {})
    item = dlg.items[0]
    assert "no initials: 2 pp." in item["count"].text()
    item["front"].setCurrentIndex(item["front"].findData("none"))  # Nobody (not billed)
    assert "no initials: 2 pp." in item["count"].text() and item["radios"]["me"].text() == "My pages (2)"
    item["front"].setCurrentIndex(item["front"].findData(PR))
    assert "no initials" not in item["count"].text()




def test_enter_in_the_excerpts_table_keeps_the_window_open(qt, tmp_path, s):
    """Enter after typing a run in the table keeps it ("105-110" -> pages 5-10) and the window stays open (in
    the old per-day Excerpts dialog it pressed OK and closed it without the excerpt)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from minute_filler.gui.excerpts import PAGES, ExcerptsWindow
    job = job_with(tmp_path, s, [PR] * 30, attorneys=[alex(), dana()])
    changed = []
    w = ExcerptsWindow(s, lambda: changed.append(1))
    w.load([job])
    w.show()
    QTest.keyClick(w, Qt.Key_Return)  # no button of the window takes Enter
    assert w.isVisible()
    w.table.item(0, PAGES).setText("105-110")  # (as typed in the cell)
    QTest.keyClick(w.table, Qt.Key_Return)
    assert w.isVisible() and changed
    assert [(r.start, r.end) for r in w.runs] == [(1, 4), (5, 10), (11, 30)]
    w.close()
