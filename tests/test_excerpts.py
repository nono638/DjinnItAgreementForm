"""Excerpts as one table for the whole case (2.0.0): runs of pages typed in the transcript's own numbers, a box
per firm, and the prices as they change. Also removing excerpts (Remove run or Delete, Remove this day's excerpts,
Remove all excerpts, the right-click menu: the pages go to a run beside), each run's date and weekday, and a colour
for each set of firms ordering together. The example: firm A orders the whole trial, firm B an excerpt of day 1,
firm C part of B's excerpt and a stretch of day 2; pages ordered by two firms are shared two ways, by three
firms three ways. Prices are from the bundled "Sample Rates" sheet (Regular: original $4.30, copy, e-mailed copy
and index $1.00 a page). All names are made up."""
import os
import time
from decimal import Decimal

import pytest

from minute_filler.batch import fill_jobs, group, read_docs
from minute_filler.deliver import ledger_for
from minute_filler.excerpts import Day, Run, carve, case_days, firms_of, prices, remove, runs_of, store, whole_day
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


@pytest.mark.usefixtures("split_orders_choose")
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


@pytest.mark.usefixtures("split_orders_choose")
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


@pytest.mark.usefixtures("split_orders_choose")
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


# ------------------------------------------------------------------ removing runs
a, b, c = A.key(), B.key(), C.key()


def day_of(pages=40):
    """A day of 6/3/2026, printed 101 on (no job: only its runs are worked out)."""
    return Day(None, "6/3/2026", pages, list(range(101, 101 + pages)))


def runs_on(day, *rows):
    """A day's runs from (first, last, firms) rows."""
    return [Run(day, first, last, list(keys)) for first, last, keys in rows]


def spans(runs):
    return [(r.start, r.end, r.keys) for r in runs]


def test_a_run_removed_goes_to_the_run_above_and_joins_the_same_firms():
    """Remove run on Dana's excerpt (121-130) of a day Alex ordered whole: its pages go back to Alex, and
    Alex's runs on either side of it are one run again."""
    day = day_of()
    runs = runs_on(day, (1, 20, [a]), (21, 30, [a, b]), (31, 40, [a]))
    assert spans(remove(runs, 1)) == [(1, 40, [a])]
    assert spans(runs) == [(1, 20, [a]), (21, 30, [a, b]), (31, 40, [a])]  # (the runs given are left as they were)


def test_the_first_run_of_a_day_goes_to_the_run_below():
    day = day_of()
    runs = runs_on(day, (1, 10, [a, b]), (11, 30, [a]), (31, 40, [a, b]))
    assert spans(remove(runs, 0)) == [(1, 30, [a]), (31, 40, [a, b])]


def test_the_last_run_of_a_day_goes_to_the_run_above_up_to_the_last_page():
    day = day_of()
    runs = runs_on(day, (1, 20, [a]), (21, 35, [b]), (36, 40, [c]))
    out = remove(runs, 2)
    assert spans(out) == [(1, 20, [a]), (21, 40, [b])] and out[-1].end == day.pages


def test_a_run_removed_goes_to_a_firm_rather_than_to_nobody():
    """The run above was ordered by nobody, the run below by Alex: the pages go to Alex. With nobody on either
    side they go to the run above, and the runs of nobody are one."""
    day = day_of()
    runs = runs_on(day, (1, 20, []), (21, 30, [b]), (31, 40, [a]))
    assert spans(remove(runs, 1)) == [(1, 20, []), (21, 40, [a])]
    runs = runs_on(day, (1, 10, []), (11, 20, [b]), (21, 40, []))
    assert spans(remove(runs, 1)) == [(1, 40, [])]
    runs = runs_on(day, (1, 10, [a]), (11, 40, []))  # the first run: only the run below can have it
    assert spans(remove(runs, 0)) == [(1, 40, [])]


def test_runs_of_the_same_firms_elsewhere_in_the_day_stay_apart():
    """Only the runs touching the run that grew are joined: two runs of Alex left by Split run, further up the
    day, stay as they are (as Split run and a run typed make them, before the firms are ticked)."""
    day = day_of()
    runs = runs_on(day, (1, 10, [a]), (11, 20, [a]), (21, 30, [b]), (31, 40, [c]))
    assert spans(remove(runs, 3)) == [(1, 10, [a]), (11, 20, [a]), (21, 40, [b])]
    runs = runs_on(day, (1, 10, [a]), (11, 20, [b, a]), (21, 30, [b]), (31, 40, [a, b]))
    assert spans(remove(runs, 2)) == [(1, 10, [a]), (11, 40, [b, a])]  # (the same firms, in any order)


def test_every_page_is_in_one_run_whichever_run_is_removed():
    day = day_of()
    runs = runs_on(day, (1, 5, [a]), (6, 12, []), (13, 20, [a, b]), (21, 33, [c]), (34, 40, [a]))
    for i in range(len(runs)):
        out = remove(runs, i)
        assert out[0].start == 1 and out[-1].end == day.pages, i
        assert all(x.end + 1 == y.start for x, y in zip(out, out[1:])), i  # no gap, no overlap
        assert len(out) < len(runs), i


def test_the_only_run_of_a_day_is_not_removed():
    day = day_of()
    with pytest.raises(ValueError, match="6/3/2026 is one run: nothing to remove. Untick its firms to bill it to "
                                         "nobody."):
        remove(runs_on(day, (1, 40, [a, b])), 0)


def test_a_day_made_one_run_again():
    """Remove this day's excerpts: one run of every page, ordered by every firm that ordered any of it, in the
    table's order; by the firms ticked when nobody ordered any of it."""
    day = day_of()
    runs = runs_on(day, (1, 20, [b]), (21, 30, []), (31, 40, [a, b]))
    assert spans(whole_day(day, runs, [c], [A, B, C])) == [(1, 40, [a, b])]
    assert spans(whole_day(day, runs_on(day, (1, 10, []), (11, 40, [])), [c])) == [(1, 40, [c])]


def test_a_run_removed_is_kept_on_the_day(tmp_path, s):
    """After Remove run Dana orders nothing of day 1: she is unticked on it, and the one run left is kept as
    no rows at all (Job.portions None)."""
    jobs = trial(tmp_path, s)
    d1 = case_days(jobs)[0]
    firms = firms_of(jobs)
    store(d1, runs_on(d1, (1, 20, [a]), (21, 30, [a, b]), (31, 40, [a])), firms)
    assert jobs[0].portions == [(20, [a]), (30, [a, b]), (40, [a])]
    assert [x.checked for x in jobs[0].case.attorneys] == [True, True, False]
    store(d1, remove(runs_of(d1, firms), 1), firms)
    assert jobs[0].portions is None and [x.checked for x in jobs[0].case.attorneys] == [True, False, False]
    assert spans(runs_of(d1, firms)) == [(1, 40, [a])]
    # a run of several left: the rows are kept as they are now
    store(d1, runs_on(d1, (1, 10, [a, b]), (11, 30, [b]), (31, 40, [a])), firms)
    store(d1, remove(runs_of(d1, firms), 0), firms)
    assert jobs[0].portions == [(30, [b]), (40, [a])]


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


@pytest.mark.usefixtures("split_orders_choose")
def test_the_excerpts_window_follows_the_table_and_the_main_window(window, tmp_path):
    from PySide6.QtCore import Qt
    from minute_filler.gui.excerpts import DATE, FIRST_FIRM, PAGES, PLACE, WEEKDAY
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
    assert heads[:6] == ["Date", "Weekday", "Pages", "Place in the day", "Alex B. Counsel", "Dana Smith"]
    assert "Each pays · Regular" in heads and w.table.isColumnHidden(PLACE)  # the place is off by default
    w.table.item(0, PAGES).setText("121-140")  # Dana's excerpt of day 1: typed in the printed numbers
    assert [w.table.item(r, PAGES).text() for r in range(3)] == ["101–120", "121–140", "101–120"]
    # every run says its date and weekday; a day's later runs in italics
    assert [w.table.item(r, DATE).text() for r in range(3)] == ["6/3/2026", "6/3/2026", "6/4/2026"]
    assert [w.table.item(r, WEEKDAY).text() for r in range(3)] == ["Wednesday", "Wednesday", "Thursday"]
    assert [w.table.item(r, DATE).font().italic() for r in range(3)] == [False, True, False]
    w.table.item(0, FIRST_FIRM + 1).setCheckState(Qt.Unchecked)  # Dana didn't order pages 101-120
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
    assert not w.table.isColumnHidden(PLACE) and window.s.excerpt_show_place
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
    window.job_list.topLevelItem(1).setCheckState(0, Qt.Unchecked)  # day 2 left out of Generate all
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


@pytest.mark.usefixtures("split_orders_choose")
def test_the_colours_of_the_excerpts_table_are_the_sets_of_firms_ordering_together(window, tmp_path):
    """The boxes were tinted with no explanation (and glaring on the dark theme): each set of firms ordering
    together now has a colour, the same in the table and on its line under it; and the weekday is read."""
    from PySide6.QtCore import Qt
    from minute_filler.gui.excerpts import FIRST_FIRM, GROUP_COLORS, PAGES, weekdays
    assert weekdays("9/28/2026") == "Monday" and weekdays("6/3/2026, 6/4/2026") == "Wed, Thu"
    assert weekdays("(no date)") == "" and weekdays("13/45/2026") == ""
    window.add_files([str(transcript_pdf(tmp_path / "Day 1.pdf", 40))])
    wait(window, lambda: window.jobs and window.cur.docs)
    window.cur.case.attorneys = [Attorney(**{**x.__dict__}) for x in (A, B)]
    window.cur.att_touched = True
    window._show_case()
    window._who_ordered()
    w = window.excerpts
    w.table.item(0, PAGES).setText("121-140")
    w.table.item(0, FIRST_FIRM + 1).setCheckState(Qt.Unchecked)  # Alex alone, then Alex + Dana
    alone, both = w.table.item(0, FIRST_FIRM).background().color(), w.table.item(1, FIRST_FIRM).background().color()
    assert alone.name() != both.name() and {alone.name(), both.name()} <= set(GROUP_COLORS)
    assert alone.alpha() < 255  # see-through: readable on either theme
    assert w.table.item(1, FIRST_FIRM + 1).background().color().name() == both.name()  # Dana's box: the same
    assert w.table.item(0, FIRST_FIRM + 1).background().style() == Qt.NoBrush  # not ordered: no colour
    text = w.summary.text()
    assert f'color:{alone.name()}">■</span> Alex B. Counsel only' in text
    assert f'color:{both.name()}">■</span> Alex B. Counsel + Dana Smith' in text
    w.close()


def pages_shown(w):
    from minute_filler.gui.excerpts import PAGES
    return [w.table.item(r, PAGES).text() for r in range(w.table.rowCount())]


def test_delete_removes_the_run_chosen(window, tmp_path):
    """Excerpts could be made but not removed: Delete (or Remove run) gives Dana's excerpt back to the run
    above, the row now holding its pages is chosen, and the day's only run is not removed (it says why)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from minute_filler.gui.excerpts import FIRST_FIRM, PAGES
    w = two_days_open(window, tmp_path)
    day1 = window.jobs[0]
    w.table.item(0, PAGES).setText("121-140")  # Dana's excerpt of day 1
    w.table.item(0, FIRST_FIRM + 1).setCheckState(Qt.Unchecked)  # pages 101-120: Alex alone
    assert day1.portions == [(20, [a]), (40, [a, b])]
    w.activateWindow()
    w.table.setFocus()
    w.table.selectRow(1)
    QApplication.processEvents()
    assert QApplication.focusWidget() is w.table
    QTest.keyClick(w.table, Qt.Key_Delete)
    assert day1.portions is None and list(day1.ticked_keys()) == [a]  # Dana ordered nothing of day 1 now
    assert pages_shown(w) == ["101–140", "101–120"] and w.table.currentRow() == 0  # (the row of page 121)
    assert w.problem.text() == ""
    QTest.keyClick(w.table, Qt.Key_Delete)  # the day's only run
    assert w.problem.text() == "6/3/2026 is one run: nothing to remove. Untick its firms to bill it to nobody."
    assert pages_shown(w) == ["101–140", "101–120"] and day1.portions is None
    w.close()


def test_a_run_removed_leaving_nobody_says_so(window, tmp_path):
    from PySide6.QtCore import Qt
    from minute_filler.gui.excerpts import FIRST_FIRM, PAGES
    w = two_days_open(window, tmp_path)
    w.table.item(0, PAGES).setText("121-140")
    for c in (FIRST_FIRM, FIRST_FIRM + 1):
        w.table.item(1, c).setCheckState(Qt.Unchecked)  # pages 121-140: nobody
    w.table.selectRow(0)
    w._remove()  # the first run: its pages go to the run below, which nobody ordered
    assert window.jobs[0].portions == [(40, [ORDERED_BY_NOBODY])] and pages_shown(w)[0] == "101–140"
    assert w.problem.text() == "Nobody orders this day now: tick a firm."
    w.close()


def test_delete_while_a_run_is_typed_leaves_the_runs(window, tmp_path):
    """Delete in a cell being typed in deletes text, not the run."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QApplication, QLineEdit
    from minute_filler.gui.excerpts import PAGES
    w = two_days_open(window, tmp_path)
    w.table.item(0, PAGES).setText("121-140")
    w.activateWindow()
    w.table.setCurrentCell(1, PAGES)
    w.table.editItem(w.table.item(1, PAGES))
    QApplication.processEvents()
    assert w.table.state() == QAbstractItemView.EditingState
    editor = w.table.findChild(QLineEdit)
    QTest.keyClick(editor, Qt.Key_Delete)
    w._remove()  # (Remove run while typing: nothing either)
    assert pages_shown(w) == ["101–120", "121–140", "101–120"]
    assert window.jobs[0].portions == [(20, [a, b]), (40, [a, b])]
    QTest.keyClick(editor, Qt.Key_Escape)
    w.close()


def test_remove_all_excerpts(window, tmp_path, monkeypatch):
    """Remove all excerpts, after asking: every day of the case is one run again, ordered by every firm that
    ordered any of it; the table is shown and the main window told once."""
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt
    from minute_filler.gui.excerpts import FIRST_FIRM, PAGES
    w = two_days_open(window, tmp_path)
    w.table.item(0, PAGES).setText("121-140")
    w.table.item(0, FIRST_FIRM + 1).setCheckState(Qt.Unchecked)  # day 1: Alex 101-120, Alex + Dana 121-140
    w.table.item(2, PAGES).setText("111-120")
    w.table.item(3, FIRST_FIRM).setCheckState(Qt.Unchecked)  # day 2: both 101-110, Dana 111-120
    assert [j.portions for j in window.jobs] == [[(20, [a]), (40, [a, b])], [(10, [a, b]), (20, [b])]]
    asked, told = [], []
    inner = w.changed
    w.changed = lambda: told.append(1) or inner()
    button = next(x for x in w.findChildren(QtWidgets.QPushButton) if x.text() == "Remove all excerpts")
    answer = QtWidgets.QMessageBox.No
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda parent, title, text, *x, **k: asked.append(text) or answer)
    button.click()  # No: nothing changes
    assert asked == ["Every day of the case becomes one run, ordered by every firm that ordered any of it. "
                     "Remove all excerpts?"]
    assert len(w.runs) == 4 and window.jobs[0].portions is not None and told == []
    answer = QtWidgets.QMessageBox.Yes
    w.table.selectRow(3)
    button.click()
    assert [j.portions for j in window.jobs] == [None, None] and told == [1]
    assert pages_shown(w) == ["101–140", "101–120"] and w.table.currentRow() == 1  # (still on day 2)
    assert [list(j.ticked_keys()) for j in window.jobs] == [[a, b], [a, b]]
    button.click()  # nothing left to remove: not asked
    assert len(asked) == 2 and "no excerpts to remove" in w.problem.text()
    w.close()


def test_the_right_click_menu_of_a_run(window, tmp_path, monkeypatch):
    """A right-click chooses the run's row and offers Split run, Remove run (not for a day's only run), Remove
    this day's excerpts and Remove all excerpts. When the table is read afresh while the menu is open and the
    run is gone, nothing is done."""
    from minute_filler.gui.excerpts import PAGES
    w = two_days_open(window, tmp_path)
    w.table.item(0, PAGES).setText("121-140")  # day 1: two runs; day 2: one
    shown, meanwhile = [], []
    menu_of = w._run_menu

    class Picked:
        """The menu as shown (QMenu.exec can't be replaced): it picks Remove run, after what happens meanwhile."""
        def __init__(self, menu):
            self.menu = menu

        def exec(self, *_):
            shown.append([(x.text(), x.isEnabled()) for x in self.menu.actions()])
            for f in meanwhile:
                f()
            return next(x for x in self.menu.actions() if x.text() == "Remove run")

    def run_menu(run, key=None):  # (key: the firm whose box was clicked, for its speed on the run)
        menu, slots = menu_of(run, key)
        return Picked(menu), slots

    def right_click(row):
        w._context_menu(w.table.visualItemRect(w.table.item(row, PAGES)).center())

    monkeypatch.setattr(w, "_run_menu", run_menu)
    w.table.selectRow(0)
    right_click(2)  # day 2's only run: chosen, and it can't be removed
    assert w.table.currentRow() == 2
    assert shown[-1] == [("Split run", True), ("Remove run", False), ("Remove this day's excerpts", True),
                         ("Remove all excerpts", True)]
    assert len(w.runs) == 3
    meanwhile.append(lambda: (setattr(window.jobs[0], "portions", None), w.load([d.job for d in w.days])))
    right_click(1)  # the main window made day 1 one run while the menu was open
    assert "changed meanwhile" in w.problem.text() and pages_shown(w) == ["101–140", "101–120"]
    meanwhile.clear()
    w.table.item(0, PAGES).setText("121-140")
    right_click(1)
    assert shown[-1][1] == ("Remove run", True)
    assert window.jobs[0].portions is None and pages_shown(w) == ["101–140", "101–120"]
    assert w.table.currentRow() == 0
    w.close()


def test_remove_all_excerpts_after_the_table_was_read_again(window, tmp_path, monkeypatch):
    """A read that ended while "Remove all excerpts?" was asked showed the days afresh: the days found before the
    question matched none of them, and nothing was removed (nor said)."""
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt
    from minute_filler.gui.excerpts import FIRST_FIRM, PAGES
    w = two_days_open(window, tmp_path)
    w.table.item(0, PAGES).setText("121-140")
    w.table.item(0, FIRST_FIRM + 1).setCheckState(Qt.Unchecked)
    assert window.jobs[0].portions is not None

    def ask(*_a, **_k):
        window._update_status()  # (a background read ending: the main window refreshes, the table too)
        return QtWidgets.QMessageBox.Yes
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", ask)
    w._remove_all()
    assert window.jobs[0].portions is None
