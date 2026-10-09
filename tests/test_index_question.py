"""A page count typed below the transcript's own count, when it changes whether there is an index (the user,
2026-10-08: "if they change read-in data like that, they are doing their own thing and they'd want more granular
control"): the index is decided on the whole transcript, so 40 typed of an 83-page PDF would get one, while 40
pages alone would not. Generate and Generate all ask, the Invoice panel's Includes line says they will, and the
answer is kept on the typed day (Job.index_on_typed: judged on the number typed, or on the transcript), which
Peripherals... shows and changes. Not asked when both ways agree, when the count typed is above the PDF's (a
caption page only), when the day is answered already or has a Yes or No under Peripherals... Under the rule "each"
day on its own, the other days keep their own index; other reporters' invoices bill their own pages, not the
number typed, and keep theirs. All names are made up."""
import os
import time

import pytest

from minute_filler.batch import (expand_paths, group, index_question, job_from_origin, job_origin,
                                 joint_invoice, joint_invoice_sets, read_docs)
from minute_filler.invoice import index_reason, indexed_days
from minute_filler.models import SRC_USER, Attorney, FieldState

from helpers import pat_settings, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def days(tmp_path, s, pages: dict[str, int], initials=None):
    """One job per day of Roe v. X.Y. (a transcript each, {"June 3, 2026": 83, ...}), earliest first."""
    d = tmp_path / "in"
    d.mkdir()
    for day, n in pages.items():
        transcript_pdf(d / f"Roe {day}.pdf", n, date=day, initials=initials)
    docs, errors = read_docs(expand_paths([str(d)]), s)
    assert not errors
    return sorted(group(docs, s), key=lambda j: j.invoice_days()[0][0])


def typed(job, pages: str) -> None:
    job.case.fields["est_pages"] = FieldState(pages, SRC_USER, 1.0, [])


def indexed(jobs, s):
    return indexed_days(joint_invoice(jobs)[1], s)


def test_a_typed_count_is_asked_about_only_when_it_changes_the_index(tmp_path, s):
    job, = days(tmp_path, s, {"June 3, 2026": 83})
    assert index_question([job], s) is None  # (nothing typed: the transcript decides)
    typed(job, "40")
    q = index_question([job], s)
    assert (q.jobs, q.typed, q.counted, q.transcripts) == ([job], 40, 83, 1)
    assert (q.pdf_why, q.typed_why) == ("83 total pages, 50 or more", "40 total pages, under 50")
    assert indexed([job], s) == [True]  # (until answered: the transcript decides)
    typed(job, "60")  # an index either way: nothing to ask
    assert index_question([job], s) is None
    typed(job, "120")  # above the PDF's count (its caption page only, say): the typed count is the whole
    assert index_question([job], s) is None
    typed(job, "40")
    job.index_on_typed = True  # answered No: judged on the 40 typed
    assert index_question([job], s) is None and indexed([job], s) == [False]
    assert index_reason(job.invoice_opts(), s) == "40 total pages, under 50"
    job.index_on_typed = False  # answered Yes: judged on the transcript's 83
    assert index_question([job], s) is None and indexed([job], s) == [True]
    job.index_on_typed = None
    job.invoice_index = "off"  # a No (or Yes) chosen under Peripherals... already
    assert index_question([job], s) is None
    job.invoice_index = None
    s.invoice_include_index = False  # no index at all, Settings say
    assert index_question([job], s) is None


def test_the_answer_stays_with_the_job(tmp_path, s):
    """Kept with its records: a job opened again keeps it."""
    job, = days(tmp_path, s, {"June 3, 2026": 83})
    job.index_on_typed = True
    assert job_from_origin(job_origin(job), s).index_on_typed is True
    assert job_from_origin({"job": {"index_on_typed": "yes"}}, s).index_on_typed is None  # (not as expected)


def test_the_days_of_a_case_are_asked_about_together(tmp_path, s):
    """Two days on one invoice, the first typed 40 of 83, the second 30 pages: an index if any day has 50 pages
    (the default rule) is a question, and the answer is kept on the typed day only; with the days' pages added
    up (70 either way) it isn't."""
    first, second = days(tmp_path, s, {"June 3, 2026": 83, "June 4, 2026": 30})
    typed(first, "40")
    q = index_question([first, second], s)
    assert (q.jobs, q.typed, q.counted) == ([first], 40, 83)
    assert (q.pdf_why, q.typed_why) == ("a day of 83 pages, 50 or more", "no day has 50 pages")
    first.index_on_typed = True
    assert indexed([first, second], s) == [False, False] and second.index_on_typed is None
    first.index_on_typed = None
    s.invoice_index_rule = "total"
    assert index_question([first, second], s) is None


def test_each_day_on_its_own_keeps_the_other_days_index(tmp_path, s):
    """Under the rule "each", a typed day that would lose its index is asked about although another day keeps
    one, and the answer changes that day only: No leaves the other day's 60 pages their index, Yes gives none to
    a 30-page day."""
    s.invoice_index_rule = "each"
    first, second = days(tmp_path, s, {"June 3, 2026": 83, "June 4, 2026": 60})
    typed(first, "40")
    q = index_question([first, second], s)
    assert q.jobs == [first]
    assert (q.pdf_why, q.typed_why) == ("83 total pages, 50 or more", "40 total pages, under 50")
    first.index_on_typed = True
    assert indexed([first, second], s) == [False, True]


def test_each_day_yes_gives_no_index_to_a_short_day(tmp_path, s):
    s.invoice_index_rule = "each"
    first, second = days(tmp_path, s, {"June 3, 2026": 83, "June 4, 2026": 30})
    typed(first, "40")
    assert index_question([first, second], s).jobs == [first]
    first.index_on_typed = False
    assert indexed([first, second], s) == [True, False]


def test_other_reporters_invoices_are_judged_on_the_transcript(tmp_path, s):
    """83 pages, Pat Reporter's 45 and Dana Smith's 38, 40 typed: the typed count is on the user's own invoice
    only. Billed for Dana Smith alone, there is nothing to ask; billed for both, No takes the index off the
    user's invoice, and Dana Smith's keeps it."""
    job, = days(tmp_path, s, {"June 3, 2026": 83}, initials=["PR"] * 45 + ["DS"] * 38)
    doc, = job.transcripts()
    typed(job, "40")
    job.page_basis = {doc.key(): "ds"}
    assert index_question([job], s) is None
    job.page_basis = {doc.key(): ["me", "ds"]}
    assert index_question([job], s).jobs == [job]
    job.index_on_typed = True
    (_, mine), (_, theirs) = joint_invoice_sets([job])
    assert (mine.reporter, mine.pages, indexed_days(mine, s)) == ("", 40, [False])
    assert (theirs.reporter, theirs.pages, indexed_days(theirs, s)) == ("ds", 38, [True])


def test_a_day_billed_for_another_reporter_alone_is_not_on_the_users_invoice(tmp_path, s):
    """Day 1: 40 typed of 83; day 2: 60 pages Dana Smith wrote, billed for her alone (Whose pages...). The
    user's own invoice is day 1's, judged on 83 or on 40: a question, though both days together would have an
    index either way."""
    d = tmp_path / "in"
    d.mkdir()
    transcript_pdf(d / "Roe 6-3-2026.pdf", 83)
    transcript_pdf(d / "Roe 6-4-2026.pdf", 60, date="June 4, 2026", initials=["DS"] * 60)
    docs, errors = read_docs(expand_paths([str(d)]), s)
    first, second = sorted(group(docs, s), key=lambda j: j.case.get("dates"))  # (day 2 bills nobody yet)
    assert (first.case.get("dates"), second.case.get("dates")) == ("6/3/2026", "6/4/2026")
    doc, = second.transcripts()
    second.page_basis = {doc.key(): "ds"}
    typed(first, "40")
    assert index_question([first, second], s).jobs == [first]


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


def settle(window, until, seconds=20):
    """Runs the Qt event loop until no background task is left and until() is true."""
    from PySide6 import QtWidgets
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not window.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def forty_of_83(window, tmp_path):
    """An 83-page transcript ordered by Alex B. Counsel, with 40 typed as its pages and the invoice ticked."""
    window.add_files([str(transcript_pdf(tmp_path / "Roe 6-3-2026.pdf", 83))])
    settle(window, lambda: window.cur.docs)
    window.output_boxes["invoice"].setChecked(True)
    window.case.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)]
    window._show_attorneys()
    typed(window.cur, "40")
    window._show_case()
    window._update_status()


def two_days(window, tmp_path, second=30):
    """Two days of Roe v. X.Y. loaded together (83 pages and `second`), both ordered by Alex B. Counsel."""
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "Roe 6-3-2026.pdf", 83)),
                      str(transcript_pdf(tmp_path / "Roe 6-4-2026.pdf", second, date="June 4, 2026"))])
    settle(window, lambda: len(window.jobs) == 2)
    window.job_list.setCurrentRow(0)
    for j in window.jobs:
        j.case.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)]
    first, second = window.jobs
    assert (first.case.get("dates"), second.case.get("dates")) == ("6/3/2026", "6/4/2026")
    return first, second


def answer(monkeypatch, *texts, other=None):
    """Each index question answers itself with the button of the next of these texts (None: closed, as Go
    back); any other box with the button that begins with `other` (else it is closed). Returns the texts of the
    index questions, as they were asked."""
    from PySide6 import QtWidgets
    asked, left = [], list(texts)

    def exec_(box):
        if box.windowTitle() == "Index for this invoice?":
            asked.append(box.text())
            text = left.pop(0) if left else None
        else:
            text = other
        for b in box.buttons():
            if text is not None and b.text().startswith(text):
                b.click()
                return

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", exec_)
    return asked


def test_the_window_asks_and_keeps_the_answer_for_the_job(window, tmp_path, monkeypatch):
    forty_of_83(window, tmp_path)
    job = window.cur
    assert window.inv_peripherals_info.text() == "E-mailed copy, index? Generate asks\n(40 typed, the transcript has 83)"
    asked = answer(monkeypatch, None)  # closed: Go back, nothing made, nothing kept
    assert not window._ask_index([[job]]) and job.index_on_typed is None
    asked = answer(monkeypatch, "No index")
    assert window._ask_index([[job]]) and job.index_on_typed is True and job.invoice_index is None
    assert "You typed 40 pages in Est. number of pages; the transcript has 83." in asked[0]
    assert ("Judged on the transcript, the invoice gets an index (83 total pages, 50 or more); judged on the pages "
            "you typed, it gets none (40 total pages, under 50).") in asked[0]
    assert window.inv_peripherals_info.text() == \
        "E-mailed copy, no index\n(on the pages you typed: 40 total pages,\nunder 50)"
    asked = answer(monkeypatch, "Yes, an index")
    assert window._ask_index([[job]]) and asked == [] and job.index_on_typed is True  # (answered: not asked again)
    job.index_on_typed = None
    assert window._ask_index([[job]]) and len(asked) == 1 and job.index_on_typed is False
    assert window.inv_peripherals_info.text() == "E-mailed copy, index\n(83 total pages, 50 or more)"


def test_peripherals_shows_and_changes_the_answer(window, tmp_path, monkeypatch):
    """Peripherals... has a box for the typed day under Auto: ticked, the index is judged on the pages typed.
    Left unticked when never answered, Generate still asks; Same as Settings has it ask again."""
    from minute_filler.gui.dialogs import InvoicePeripheralsDialog
    forty_of_83(window, tmp_path)
    job = window.cur
    seen = []

    def run(change):
        def exec_(dlg):
            seen.append((dlg.on_typed_box.text(), dlg.on_typed_box.isChecked(), dlg.on_typed_box.isEnabled()))
            change(dlg)
            return 1
        monkeypatch.setattr(InvoicePeripheralsDialog, "exec", exec_)
        window._invoice_peripherals()

    run(lambda dlg: None)  # OK without touching it: still not answered
    assert seen[-1] == ("Judge it on the 40 pages you typed, not the transcript's 83", False, True)
    assert job.index_on_typed is None
    run(lambda dlg: dlg.on_typed_box.setChecked(True))
    assert job.index_on_typed is True and window.inv_peripherals_info.text().startswith("E-mailed copy, no index")
    run(lambda dlg: dlg.on_typed_box.setChecked(False))
    assert seen[-1][1] is True and job.index_on_typed is False  # (answered Yes: the transcript decides)
    run(lambda dlg: dlg._defaults())
    assert job.index_on_typed is None and job.invoice_index is None

    def off(dlg):
        next(b for b in dlg.index.buttons() if b.property("key") == "off").setChecked(True)
        assert not dlg.on_typed_box.isEnabled()  # (a box of Auto's)
    run(off)
    assert job.invoice_index == "off"


def test_an_answer_and_a_go_back_after_it(window, tmp_path, monkeypatch):
    """An invoice for each day, both typed 40: No for the first, then Go back on the second. Nothing is made,
    the first answer is kept (as the box says), and the Includes line shows it."""
    window.s.invoice_joint = False
    first, second = two_days(window, tmp_path, second=83)
    typed(first, "40")
    typed(second, "40")
    window._show_case()
    window._update_status()
    asked = answer(monkeypatch, "No index", None)
    assert not window._ask_index([[first], [second]]) and len(asked) == 2
    assert (first.index_on_typed, second.index_on_typed) == (True, None)
    assert window.inv_peripherals_info.text().startswith("E-mailed copy, no index")


def test_a_case_of_several_days_names_the_day_and_generate_all(window, tmp_path, monkeypatch):
    first, second = two_days(window, tmp_path)
    typed(first, "40")
    window._show_case()
    window._update_status()
    assert window.inv_peripherals_info.text() == \
        "E-mailed copy, index? Generate all asks\n(40 typed, the transcript has 83)"
    asked = answer(monkeypatch, "No index")
    assert window._ask_index([[first, second]])
    assert "You typed 40 pages in Est. number of pages for 6/3/2026; the transcript has 83." in asked[0]
    assert (first.index_on_typed, second.index_on_typed) == (True, None)
    window.s.invoice_index_rule = "each"
    first.index_on_typed = None
    second.case.fields["est_pages"] = FieldState()
    asked = answer(monkeypatch, None)
    window._ask_index([[first, second]])
    assert "Judged on the transcript, that day gets an index (83 total pages, 50 or more)" in asked[0]
    assert asked[0].endswith("Include an index (and the judge's) for that day?")


def test_generate_and_generate_all_ask_before_anything_is_made(window, tmp_path, monkeypatch):
    """Generate asks about the day shown; Generate all about each invoice it makes (the two days of the case
    together). Go back makes nothing."""
    first, second = two_days(window, tmp_path)
    typed(first, "40")
    window._show_case()
    calls = []
    monkeypatch.setattr(window, "_ask_index", lambda groups: calls.append(groups) or False)
    window.fill()
    assert calls == [[[first]]] and not first.saved
    calls.clear()
    window.fill_all_jobs()
    assert calls == [[[first, second]]]
    assert not window.filling and not any(j.saved for j in window.jobs)


def test_generate_all_asks_about_the_days_it_makes(window, tmp_path, monkeypatch):
    """The second day incomplete (no judge) and left out ("Do the 1 complete ones"): the invoice made is the
    first day's alone, and that is what is asked about (40 typed of 83, with the 60-page day an index either way
    together)."""
    first, second = two_days(window, tmp_path, second=60)
    typed(first, "40")
    second.case.fields["judge"] = FieldState("", SRC_USER, 1.0, [])
    window._show_case()
    answer(monkeypatch, other="Do the 1 complete")
    calls = []
    monkeypatch.setattr(window, "_ask_index", lambda groups: calls.append(groups) or False)
    window.fill_all_jobs()
    assert calls == [[[first]]]


def test_the_answer_reaches_the_invoice_made(window, tmp_path, monkeypatch):
    """No index answered in Generate all: the invoice made (on copies of the jobs) has none, as its record says."""
    from minute_filler.records import Ledger, default_db
    first, second = two_days(window, tmp_path)
    typed(first, "40")
    window._show_case()
    asked = answer(monkeypatch, "No index")
    window.fill_all_jobs()
    settle(window, lambda: not window.filling and all(j.saved for j in window.jobs))
    assert len(asked) == 1 and "You typed 40 pages" in asked[0] and first.index_on_typed is True
    assert [i.index for i in Ledger(default_db()).invoices()] == ["No"]
