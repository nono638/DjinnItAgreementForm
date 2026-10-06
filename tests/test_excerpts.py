"""Excerpts as one table for the whole case (2.0.0): runs of pages typed in the transcript's own numbers, a box
per firm, and the prices as they change. The example: firm A orders the whole trial, firm B an excerpt of day 1,
firm C part of B's excerpt and a stretch of day 2; pages ordered by two firms are shared two ways, by three
firms three ways. Prices are from the bundled "Sample Rates" sheet (Regular: original $4.30, copy, e-mailed copy
and index $1.00 a page). All names are made up."""
import os
import time
from decimal import Decimal

import pytest

from minute_filler.batch import fill_jobs, group, read_docs
from minute_filler.deliver import ledger_for
from minute_filler.excerpts import Run, carve, case_days, firms_of, prices, runs_of, store
from minute_filler.invoice import ORDERED_BY_NOBODY
from minute_filler.models import Attorney

from helpers import pat_settings, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def firm(name, firm_name):
    return Attorney(name=name, firm=firm_name, checked=True)


A, B, C = (firm("Alex B. Counsel", "Counsel & Counsel"), firm("Dana Smith", "Smith Law"),
           firm("Sam Advocate", "Advocate LLP"))


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.invoice_speeds = ["Regular"]
    s.invoice_include_index = False
    return s


def trial(tmp_path, s):
    """Two days of Roe v. X.Y.: 40 pages on 6/3 (printed 101-140 after the cover), 20 on 6/4; A, B and C on
    both days' attorney tables, only A ticked on the second."""
    a = transcript_pdf(tmp_path / "Day 1.pdf", 40)
    b = transcript_pdf(tmp_path / "Day 2.pdf", 20, date="June 4, 2026")
    docs, errors = read_docs([str(a), str(b)], s)
    assert not errors
    jobs = group(docs, s)
    assert len(jobs) == 2
    for j in jobs:
        j.case.attorneys = [Attorney(**{**x.__dict__}) for x in (A, B, C)]
    jobs[1].case.attorneys[1].checked = jobs[1].case.attorneys[2].checked = False
    return jobs


def test_the_runs_of_a_day_typed_in_its_printed_numbers(tmp_path, s):
    jobs = trial(tmp_path, s)
    d1, d2 = case_days(jobs)
    assert d1.pages == 40 and d1.numbered and d1.printed[:2] == [101, 102]
    assert d1.parse("110-119") == (10, 19) and d1.parse("120") == (20, 20)
    with pytest.raises(ValueError, match="pages are 101–140"):
        d1.parse("90-95")
    runs = runs_of(d1)
    assert [(r.start, r.end, r.keys) for r in runs] == [(1, 40, [A.key(), B.key(), C.key()])]
    runs = carve(runs, 0, 9, 18)  # typed 109-118 in the one run: it is cut out of it, the rest keeps its firms
    assert [(r.start, r.end) for r in runs] == [(1, 8), (9, 18), (19, 40)]
    runs = carve(runs, 1, 5, 25)  # widened over the runs around it
    assert [(r.start, r.end) for r in runs] == [(1, 4), (5, 25), (26, 40)]
    assert d1.span(5, 25) == "105–125" and d1.place(5, 25) == "5–25 of 40"


def test_the_example_case_is_billed_two_and_three_ways(tmp_path, s):
    jobs = trial(tmp_path, s)
    d1, d2 = days = case_days(jobs)
    firms = firms_of(jobs)
    a, b, c = (x.key() for x in firms)
    # day 1: A everything; B pages 21-40; C 31-40 (part of B's). Day 2: A everything; C pages 11-15.
    store(d1, [Run(d1, 1, 20, [a]), Run(d1, 21, 30, [a, b]), Run(d1, 31, 40, [a, b, c])], firms)
    store(d2, [Run(d2, 1, 10, [a]), Run(d2, 11, 15, [a, c]), Run(d2, 16, 20, [a])], firms)
    assert jobs[0].portions == [(20, [a]), (30, [a, b]), (40, [a, b, c])]
    assert jobs[1].portions == [(10, [a]), (15, [a, c]), (20, [a])]
    assert [x.checked for x in jobs[1].case.attorneys] == [True, False, True]  # C now ordered on day 2
    runs = [r for d in days for r in runs_of(d)]
    p = prices(days, runs, s)
    by = {keys: (pages, each["Regular"]) for keys, pages, each in p.groups}
    # alone: 4.30 + 1 + 1 = 6.30 a page; two ways: 2.15 + 2 = 4.15; three ways: 1.4333 + 2 = 3.4333
    assert by[(a,)] == (35, Decimal("220.50"))
    assert by[(a, b)] == (10, Decimal("41.50"))
    assert by[(a, c)] == (5, Decimal("20.75"))
    assert by[(a, b, c)][0] == 10 and abs(by[(a, b, c)][1] - Decimal("34.3333")) < Decimal("0.001")
    firms_due = dict((k, v["Regular"]) for k, v in p.firms)
    assert firms_due == {a: Decimal("317.09"), b: Decimal("75.84"), c: Decimal("55.09")}
    fill_jobs(jobs, s, outputs=["invoice"])
    billed = {i.bill_to: Decimal(i.amounts["Regular"]) for i in ledger_for(s).invoices()}
    assert billed == {"Alex B. Counsel": Decimal("317.09"), "Dana Smith": Decimal("75.84"),
                      "Sam Advocate": Decimal("55.09")}  # the invoices bill what the table shows


def test_a_run_nobody_ordered_is_billed_to_nobody(tmp_path, s):
    jobs = trial(tmp_path, s)
    d1 = case_days(jobs)[0]
    firms = firms_of(jobs)
    store(d1, [Run(d1, 1, 10, [A.key()]), Run(d1, 11, 40, [])], firms)
    assert jobs[0].portions == [(10, [A.key()]), (40, [ORDERED_BY_NOBODY])]
    assert not jobs[0].portions_problem() and [x.checked for x in jobs[0].case.attorneys] == [True, False, False]
    assert runs_of(d1)[1].keys == []
    fill_jobs(jobs[:1], s, outputs=["invoice"])
    inv, = ledger_for(s).invoices()
    assert inv.pages == 10 and inv.amounts["Regular"] == "63.00"


def test_one_set_of_firms_for_the_whole_day_keeps_no_rows(tmp_path, s):
    jobs = trial(tmp_path, s)
    d2 = case_days(jobs)[1]
    store(d2, [Run(d2, 1, 20, [A.key(), C.key()])], firms_of(jobs))
    assert jobs[1].portions is None and [x.checked for x in jobs[1].case.attorneys] == [True, False, True]


def test_a_run_among_numbers_the_transcript_skips_is_refused():
    """Pages 5-8 of a day numbered 1, 2, 10, 11 came back as places 3..2, and said "has pages 1-12"."""
    from minute_filler.excerpts import Day
    d = Day(None, "6/3/2026", 4, [1, 2, 10, 11])
    assert d.numbered and d.parse("2-10") == (2, 3)
    with pytest.raises(ValueError, match="no page of 6/3/2026 is numbered 5–8"):
        d.parse("5-8")


def test_the_index_of_a_job_of_two_dates_is_priced_as_the_invoices_do(tmp_path, s):
    """Two dates of 30 pages read as one job: the index (from 50 pages a day) was decided on the job's 60 pages
    in the table's prices ($309) while the invoices decide date by date and bill no index ($249). The table now
    shows $249 too."""
    s.invoice_include_index = True
    s.invoice_index_rule, s.invoice_index_threshold = "any", 50
    s.batch_combine_dates = True
    docs, errors = read_docs([str(transcript_pdf(tmp_path / "D1.pdf", 30)),
                              str(transcript_pdf(tmp_path / "D2.pdf", 30, date="June 4, 2026"))], s)
    jobs = group(docs, s)
    assert len(jobs) == 1 and len(jobs[0].invoice_days()) == 2
    jobs[0].case.attorneys = [Attorney(**{**x.__dict__}) for x in (A, B)]
    days = case_days(jobs)
    p = prices(days, [r for d in days for r in runs_of(d)], s)
    (keys, pages, each), = p.groups
    assert pages == 60 and each["Regular"] == Decimal("249")
    assert [amounts["Regular"] for _, amounts in p.firms] == [Decimal("249.00")] * 2


def test_the_same_firms_on_two_days_are_one_group_whatever_their_order(tmp_path, s):
    jobs = trial(tmp_path, s)
    for j in jobs:
        j.case.attorneys = [Attorney(**{**x.__dict__}) for x in (A, B)]
    jobs[1].case.attorneys.reverse()  # day 2's table lists Dana first
    days = case_days(jobs)
    firms = firms_of(jobs)
    runs = [r for d in days for r in runs_of(d, firms)]
    assert [r.keys for r in runs] == [[A.key(), B.key()]] * 2
    (keys, pages, _), = prices(days, runs, s).groups
    assert keys == (A.key(), B.key()) and pages == 60


# ------------------------------------------------------------------ the window
@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt, s):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s.use_ai = s.open_after = False
    s.outputs = ["invoice"]
    return make_window(s)


def wait(win, until, seconds=20):
    from PySide6.QtWidgets import QApplication
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_the_excerpts_window_follows_the_table_and_the_main_window(window, tmp_path):
    from PySide6.QtCore import Qt
    from minute_filler.gui.excerpts import PAGES
    window.add_files([str(transcript_pdf(tmp_path / "Day 1.pdf", 40)),
                      str(transcript_pdf(tmp_path / "Day 2.pdf", 20, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    for j in window.jobs:
        j.case.attorneys = [Attorney(**{**x.__dict__}) for x in (A, B)]
        j.att_touched = True
    window._show_case()
    window._who_ordered()
    w = window.excerpts
    assert w.isVisible() and [r.day.label for r in w.runs] == ["6/3/2026", "6/4/2026"]
    heads = [w.table.horizontalHeaderItem(c).text() for c in range(w.table.columnCount())]
    assert heads[:5] == ["Day", "Pages", "Place in the day", "Alex B. Counsel", "Dana Smith"]
    assert "Each pays · Regular" in heads and w.table.isColumnHidden(2)  # the place is off by default
    w.table.item(0, PAGES).setText("121-140")  # Dana's excerpt of day 1: typed in the printed numbers
    assert [w.table.item(r, PAGES).text() for r in range(3)] == ["101–120", "121–140", "101–120"]
    w.table.item(0, 4).setCheckState(Qt.Unchecked)  # Dana didn't order pages 101-120
    assert window.jobs[0].portions == [(20, [A.key()]), (40, [A.key(), B.key()])]
    assert "Alex B. Counsel only: 20 pp." in w.summary.text()  # (day 2: both firms)
    assert "Alex B. Counsel + Dana Smith: 40 pp." in w.summary.text() and "each of the 2 pays" in w.summary.text()
    assert "(÷ 2)" in w.table.item(1, w.table.columnCount() - 1).text()
    w.table.item(1, PAGES).setText("90-95")  # not a page of the day: said, and nothing changes
    assert "101–140" in w.problem.text() and window.jobs[0].portions == [(20, [A.key()]), (40, [A.key(), B.key()])]
    # a change in the main window shows in the table at once
    window.jobs[1].case.attorneys[1].checked = False
    window._update_status()
    assert w.runs[-1].keys == [A.key()]
    w.speed_boxes["Regular"].setChecked(False)  # the prices of a speed can be hidden
    assert "Each pays · Regular" not in [w.table.horizontalHeaderItem(c).text() for c in range(w.table.columnCount())]
    assert window.s.excerpt_hidden_speeds == ["Regular"]
    w.place.setChecked(True)
    assert not w.table.isColumnHidden(2) and window.s.excerpt_show_place
    w.close()


def two_days_open(window, tmp_path):
    """Two days of a case read, Alex and Dana ticked on both, and the Excerpts window open on them."""
    window.add_files([str(transcript_pdf(tmp_path / "Day 1.pdf", 40)),
                      str(transcript_pdf(tmp_path / "Day 2.pdf", 20, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    for j in window.jobs:
        j.case.attorneys = [Attorney(**{**x.__dict__}) for x in (A, B)]
        j.att_touched = True
    window._show_case()
    window._who_ordered()
    assert window.excerpts.isVisible() and len(window.excerpts.days) == 2
    return window.excerpts


def test_new_job_closes_the_excerpts_window(window, tmp_path):
    """The window kept showing (and changing) the days New job had dropped."""
    w = two_days_open(window, tmp_path)
    window._clear_jobs()
    assert not w.isVisible() and w.runs == [] and w.table.rowCount() == 0


def test_the_excerpts_window_closes_when_its_days_are_removed(window, tmp_path):
    from minute_filler.batch import Job
    w = two_days_open(window, tmp_path)
    window.jobs = [Job()]  # (as "Remove this job" does for the last ones)
    window.cur = window.jobs[0]
    window._refresh_jobs()
    window._show_job()
    assert not w.isVisible() and w.runs == []


def test_the_excerpts_window_follows_speeds_and_jobs_ticked(window, tmp_path):
    """Speeds offered, a job unticked: they reached the main window's prices, not the Excerpts window."""
    w = two_days_open(window, tmp_path)
    assert w.speeds == ["Regular"]
    window.inv_speed_boxes["Expedite"].setChecked(True)
    assert "Expedite" in w.speeds
    from PySide6.QtCore import Qt
    window.job_list.item(1).setCheckState(Qt.Unchecked)  # day 2 left out of Generate all
    assert [d.job for d in w.days] == [window.jobs[0]]


def test_a_cell_typed_while_the_main_window_changed_the_runs(window, tmp_path):
    """A reload skipped while a run was typed was lost: keeping the typing then wrote the old firms back."""
    from PySide6.QtWidgets import QAbstractItemView
    from minute_filler.gui.excerpts import PAGES
    w = two_days_open(window, tmp_path)
    w.table.state = lambda: QAbstractItemView.EditingState  # (as while an editor is open)
    day2 = window.jobs[1]
    day2.case.attorneys[1].checked = False  # Dana unticked on day 2 (in the main window)
    window._update_status()
    assert w.runs[1].keys == [A.key(), B.key()]  # not shown yet: the cell is being typed in
    w.table.item(1, PAGES).setText(w.days[1].span(1, 10))  # the typing kept
    del w.table.state
    assert list(day2.ticked_keys()) == [A.key()]  # Dana stays unticked
    assert day2.portions == [(10, [A.key()]), (20, [A.key()])]
