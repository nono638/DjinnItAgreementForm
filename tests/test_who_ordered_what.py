"""Who ordered what, spelled out: Job.order_lines gives a line per day and attorney with the pages it ordered
(every page, or which excerpt), who shares them and how many of the user's pages that bills; an attorney not
ticked on a day and a day still waiting for a choice say so. The window shows them in the "Who ordered what"
card, and Generate all warns before billing a case with a day not decided. All names are made up."""
import os
import time

import pytest

from minute_filler.batch import group, read_docs
from minute_filler.models import Attorney

from helpers import pat_settings, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def alex():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def dana():
    return Attorney(name="Dana Smith", firm="Smith Law", checked=True)


A, B = alex().key(), dana().key()


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def job_of(tmp_path, s, pages=100, initials=None, attorneys=None):
    path = transcript_pdf(tmp_path / "Roe 6-3-2026.pdf", pages, initials=initials)
    docs, _ = read_docs([str(path)], s)
    job, = group(docs, s)
    job.case.attorneys = attorneys if attorneys is not None else [alex(), dana()]
    return job


def test_every_page_and_an_excerpt_are_spelled_out(tmp_path, s):
    job = job_of(tmp_path, s)
    a, d = job.order_lines()
    assert (a.name, a.whole, a.spans, a.billed) == ("Alex B. Counsel", True, [(1, 100)], 100)
    assert a.shared == {B: [(1, 100)]} and a.printed == ["pp. 101–200"]
    job.portions = [(19, [A]), (40, [A, B]), (100, [A])]  # Dana Smith ordered pages 20-40
    a, d = job.order_lines()
    assert a.whole and a.spans == [(1, 100)] and a.shared == {B: [(20, 40)]}
    assert not d.whole and d.spans == [(20, 40)] and d.shared == {A: [(20, 40)]} and d.billed == 21
    assert d.printed == ["pp. 120–140"]


def test_only_my_pages_are_counted_and_others_order_nothing(tmp_path, s):
    job = job_of(tmp_path, s, pages=10, initials=["pr"] * 6 + ["ds"] * 4, attorneys=[alex()])
    line, other = job.order_lines([alex(), dana()])
    assert line.whole and line.spans == [(1, 10)] and line.billed == 6 and line.shared == {}
    assert other.name == "Dana Smith" and other.note.startswith("not ticked on this day")


def test_a_day_waiting_for_a_choice_says_so(tmp_path, s):
    job = job_of(tmp_path, s, pages=8, initials=["ds"] * 8)  # none of the pages are Pat's
    lines = job.order_lines()
    assert len(lines) == 2 and all(l.note.startswith("⚠ Whose pages") for l in lines)
    job.page_basis[job.docs[0].key()] = "*"
    job.portions = [(3, [A, B]), (5, [A])]  # rows that no longer end on the day's pages
    assert all("Excerpts… needs checking" in l.note for l in job.order_lines())


# ------------------------------------------------------------------ the window

@pytest.fixture
def window(tmp_path, monkeypatch, make_window):
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.outputs = ["invoice"]
    return make_window(s)


def wait(win, until, seconds=20):
    from PySide6 import QtWidgets
    end = time.time() + seconds
    while time.time() < end and not (until() and not win.work):
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def two_days(window, tmp_path):
    """Two days of Roe v. X.Y.: Alex on both, Dana on the first only, with an excerpt."""
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026")),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=20, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    first, second = sorted(window.jobs, key=lambda j: j.case.get("dates"))
    first.case.attorneys, second.case.attorneys = [alex(), dana()], [alex()]
    first.portions = [(10, [A, B]), (30, [A])]
    window.job_list.setCurrentRow(window.jobs.index(first))
    window._show_job()  # the editor shows these attorneys (else the next look at it takes them back)
    return first, second


def test_the_card_lists_every_attorney_of_every_day(window, tmp_path):
    first, second = two_days(window, tmp_path)
    assert not window.orders_card.isHidden()
    rows = [[window.orders.item(r, c).text() for c in range(6)] for r in range(window.orders.rowCount())]
    assert rows[0][:3] == ["6/3/2026", "Alex B. Counsel", "every page, 1–30"]
    assert rows[0][4].startswith("Dana Smith (1–10)")
    assert rows[1][:3] == ["6/3/2026", "Dana Smith", "excerpt: 1–10 of 30"] and rows[1][5] == "10"
    assert rows[2][1:3] == ["Alex B. Counsel", "every page, 1–20"] and rows[2][4] == "nobody (alone)"
    assert rows[3][1] == "Dana Smith" and rows[3][2].startswith("not ticked on this day")
    window.output_boxes["invoice"].setChecked(False)  # no invoice: no card
    assert window.orders_card.isHidden()


def test_generate_all_warns_about_a_day_not_decided(window, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    first, second = two_days(window, tmp_path)
    first.portions = [(10, [A, B]), (25, [A])]  # no longer ends on the day's 30 pages: needs checking
    shown, started = [], []
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: shown.append(self.text()))  # (Go back)
    monkeypatch.setattr("minute_filler.gui.main_window.MainWindow._batch_running", lambda self: False)
    import minute_filler.gui.main_window as mw
    monkeypatch.setattr(mw.Runner, "start", lambda self, *a, **k: started.append(a))
    window.fill_all_jobs()
    assert shown and "Excerpts… needs checking" in shown[0] and "6/3/2026" in shown[0]
    assert not started  # went back: nothing made


def test_generate_all_can_go_on_without_the_day(window, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    first, second = two_days(window, tmp_path)
    first.portions = [(10, [A, B]), (25, [A])]
    started = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if "Go on" in b.text()))
    import minute_filler.gui.main_window as mw
    monkeypatch.setattr(mw.Runner, "start", lambda self, *a, **k: started.append(a))
    window.fill_all_jobs()
    assert started  # made, the day not decided without its invoice (see batch.fill_jobs)


# ------------------------------------------------------------------ found in the sweep (each failed before)

def test_a_job_of_several_days_shares_by_the_parties_and_lists_the_others(tmp_path, s):
    """All days on one form (Settings): each day's lines carry the Parties number its invoice is shared by,
    and an attorney of the invoice not ticked says, for each day, that it orders nothing."""
    s.batch_combine_dates = True
    paths = [str(transcript_pdf(tmp_path / "a.pdf", 30)),
             str(transcript_pdf(tmp_path / "b.pdf", 20, date="June 4, 2026"))]
    docs, _ = read_docs(paths, s)
    job, = group(docs, s)
    job.case.attorneys = [alex()]
    job.parties = 3
    lines = job.order_lines([alex(), dana()])
    assert [(l.day, l.name, l.parties) for l in lines if not l.note] == [("6/3/2026", "Alex B. Counsel", 3),
                                                                         ("6/4/2026", "Alex B. Counsel", 3)]
    assert [(l.day, l.name) for l in lines if l.note.startswith("not ticked")] == [("6/3/2026", "Dana Smith"),
                                                                                   ("6/4/2026", "Dana Smith")]


def test_a_held_day_is_worded_as_the_invoice_panel_words_it(tmp_path, s):
    job = job_of(tmp_path, s, pages=8, initials=["ds"] * 8)
    assert {l.note for l in job.order_lines()} == {"⚠ " + job.invoice_hold()}


def test_a_typed_page_count_is_marked(tmp_path, s):
    from minute_filler.models import SRC_USER
    job = job_of(tmp_path, s)
    job.case.set("est_pages", "40", SRC_USER)
    a, d = job.order_lines()
    assert a.typed and a.pages == 40 and a.billed == 40


def rows_of(window):
    t = window.orders
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


def one_day(window, tmp_path, pages=30, initials=None, attorneys=None, name="a.pdf"):
    window.add_files([str(transcript_pdf(tmp_path / name, pages=pages, initials=initials))])
    wait(window, lambda: window.cur.docs)
    window.cur.case.attorneys = [alex(), dana()] if attorneys is None else attorneys
    window._show_case()
    window._update_status()
    return window.cur


def test_the_card_says_a_day_nobody_ticked_holds_the_case(window, tmp_path):
    from minute_filler.gui.main_window import NOBODY_DAY
    first, second = two_days(window, tmp_path)
    second.case.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=False)]
    window._update_status()
    rows = rows_of(window)
    assert [r[:3] for r in rows if r[0] == "6/4/2026"] == [["6/4/2026", "(nobody ticked)", NOBODY_DAY]]
    assert window.orders_info.text().startswith("⚠ No invoice for these 2 days of the case yet: nobody is ticked")
    assert all("no invoice yet" in r[5] for r in rows if r[0] == "6/3/2026")  # nothing billed as it stands


def test_the_card_never_shows_an_empty_table(window, tmp_path):
    one_day(window, tmp_path)
    window.rows["est_pages"].choose("0")
    rows = rows_of(window)
    assert len(rows) == 1 and rows[0][2] == "⚠ the Pages field says 0: no invoice for this job"


def test_a_typed_page_count_says_it_is_what_is_billed(window, tmp_path):
    one_day(window, tmp_path, pages=100)
    window.rows["est_pages"].choose("40")
    rows = rows_of(window)
    assert rows[0][2] == "every page (Pages field typed: 40 billed)" and rows[0][5] == "40"


def test_an_excerpt_of_none_of_my_pages_says_it_gets_no_invoice(window, tmp_path):
    job = one_day(window, tmp_path, pages=10, initials=["ds"] * 4 + ["pr"] * 6)
    job.portions = [(4, [A, B]), (10, [A])]  # Dana Smith ordered pages 1-4: the other reporter's
    window._update_status()
    rows = rows_of(window)
    assert rows[0][5] == "6"
    assert rows[1][1] == "Dana Smith" and rows[1][5] == "0 (no invoice: none of these pages are yours)"


def test_a_day_waiting_for_whose_pages_opens_whose_pages(window, tmp_path, monkeypatch):
    import minute_filler.gui.main_window as mw
    one_day(window, tmp_path, pages=8, initials=["ds"] * 8, attorneys=[])  # nobody ticked, none of it Pat's
    rows = rows_of(window)
    assert len(rows) == 1 and rows[0][1] == "(nobody ticked)" and rows[0][2].startswith("⚠ Whose pages…")
    assert window.orders_edit.text() == "⚠ Whose pages…"
    opened = []
    monkeypatch.setattr(mw.MainWindow, "_whose_pages", lambda self, job, reason="": opened.append("whose"))
    monkeypatch.setattr(mw.MainWindow, "_who_ordered", lambda self: opened.append("excerpts"))
    window.orders_edit.click()
    window._order_row_clicked(window.orders.item(0, 0))
    assert opened == ["whose", "whose"]


def test_unticking_a_day_in_the_job_list_updates_the_card(window, tmp_path):
    from PySide6.QtCore import Qt
    first, second = two_days(window, tmp_path)
    assert {r[0] for r in rows_of(window)} == {"6/3/2026", "6/4/2026"}
    window.job_list.item(window.jobs.index(second)).setCheckState(Qt.Unchecked)
    assert not second.include and {r[0] for r in rows_of(window)} == {"6/3/2026"}
    assert "Generate all may bill it with other days" in window.orders_info.text()
    window.job_list.item(window.jobs.index(first)).setCheckState(Qt.Unchecked)  # the day shown: left out
    info = window.orders_info.text()
    assert "unticked in the job list" in info and "may bill it" not in info and "Your pages billed" in info


def test_every_row_of_the_card_can_be_seen_in_a_narrow_window(window, tmp_path):
    from PySide6 import QtWidgets
    from minute_filler.gui.zoom import z
    name = "Jane Roe v. X.Y. Holding Corporation - trial, morning and afternoon sessions - 6-3-2026.pdf"
    one_day(window, tmp_path, pages=8, initials=["ds"] * 8, name=name)  # held: a long reason on each row
    window.show()
    window.resize(700, 900)
    for _ in range(5):
        QtWidgets.QApplication.processEvents()
    t = window.orders
    assert t.rowCount() == 2 and all(t.columnWidth(c) <= z(320) for c in range(2, 5))
    assert t.viewport().height() >= sum(t.rowHeight(r) for r in range(t.rowCount()))
    window.hide()


def test_the_card_follows_the_zoom(window, tmp_path):
    first, second = two_days(window, tmp_path)
    height = window.orders.rowHeight(0)
    window.set_zoom(1.5)
    assert window.orders.rowHeight(0) > height


def run_now(self, fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs):
    """Runner.start, done at once on this thread."""
    on_done(fn(*args, **kwargs))


def test_generate_all_warns_about_a_case_with_a_day_nobody_ticked(window, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    import minute_filler.gui.main_window as mw
    first, second = two_days(window, tmp_path)
    second.case.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=False)]
    shown, started = [], []
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: shown.append(self.text()))  # (Go back)
    monkeypatch.setattr(mw.MainWindow, "_batch_running", lambda self: False)
    monkeypatch.setattr(mw.Runner, "start", lambda self, *a, **k: started.append(a))
    window.fill_all_jobs()
    assert shown and "nobody is ticked on 6/4/2026" in shown[0] and "no invoice for this case" in shown[0]
    assert not started and window.cur is second  # went back, to the day to tick


def test_after_go_on_anyway_a_held_day_is_not_a_failure(window, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    import minute_filler.gui.main_window as mw
    first, second = two_days(window, tmp_path)
    first.portions = [(10, [A, B]), (25, [A])]  # needs checking
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if "Go on" in b.text()))
    monkeypatch.setattr(mw.Runner, "start", run_now)
    boxes = []
    monkeypatch.setattr(mw.MainWindow, "_saved_box",
                        lambda self, title, text, folders, details="", warn=False: boxes.append((text, warn)))
    window.fill_all_jobs()
    (text, warn), = boxes
    assert "1 day not invoiced, as you chose:" in text and "check Excerpts… for 6/3/2026" in text
    assert "could not be saved" not in text and not warn and "for 1 job" in text


def test_the_job_list_marks_a_day_whose_invoice_is_held(window, tmp_path):
    first, second = two_days(window, tmp_path)
    first.portions = [(10, [A, B]), (25, [A])]
    window._update_status()
    assert not first.problems() and first.invoice_hold()
    assert window.job_list.item(window.jobs.index(first)).text().startswith("⚠")
