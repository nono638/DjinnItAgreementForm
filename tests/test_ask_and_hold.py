"""The user's answers after the 2.1.0 sweep: No. of copies of a day split under Excerpts... follows the firms that
ordered (a Parties number set by hand that disagrees is asked about at Generate); a case whose run sheet can't be
written (open in Excel) gets nothing made, its days saying why, and Generate checks the run sheet before making
anything; the takes of a trial go on its run sheet in one save; rows that may be one firm ("Smith Law" and "Smith
Law Group", "Mr. Smith" and Dana Smith of Smith Law) are not joined without asking, and the answer is kept. All
names are made up."""
import os
import sys

import pytest

from minute_filler.batch import (HELD, Job, fill_jobs, firm_questions, group, join_entries, read_docs,
                                 sheet_units)
from minute_filler.deliver import ledger_for
from minute_filler.extract_regex import dedupe_attorneys, maybe_same_entry, same_entry, use_firm_answers
from minute_filler.models import Attorney

from helpers import pat_settings, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def s(tmp_path):
    s = pat_settings(initials="pr")
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.output_dirs = {"runsheet": str(tmp_path / "sheets")}
    return s


def firm(name, firm_name, checked=True):
    return Attorney(name=name, firm=firm_name, checked=checked)


# ------------------------------------------------------------------ copies of a day split under Excerpts...
def split_day(tmp_path, s):
    """A 30-page day: Alex's firm ordered pages 1-10, Alex's and Dana's 11-30; Sam's firm is not ticked."""
    docs, _ = read_docs([str(transcript_pdf(tmp_path / "Roe.pdf", 30))], s)
    job, = group(docs, s)
    alex, dana = firm("Alex B. Counsel", "Counsel & Counsel"), firm("Dana Smith", "Smith Law")
    job.case.attorneys = [alex, dana, firm("Sam Advocate", "Advocate LLP", checked=False)]
    job.att_touched = True
    job.portions = [(10, [alex.key()]), (30, [alex.key(), dana.key()])]
    return job


def test_copies_of_a_split_day_follow_the_firms_that_ordered(tmp_path, s):
    job = split_day(tmp_path, s)
    job.parties = 3  # set by hand before the day was split
    job.refresh_copies(s)
    assert job.case.get("copies") == "2"
    assert job.parties_mismatch() == "you set Parties to 3, but 2 firms ordered pages under Excerpts…"
    job.parties = 2
    assert job.parties_mismatch() == ""
    job.portions, job.parties = None, 3  # a whole day: the Parties number counts
    job.refresh_copies(s)
    assert job.case.get("copies") == "3" and job.parties_mismatch() == ""


def test_a_firm_ticked_without_a_run_of_its_own_orders_nothing(tmp_path, s):
    """Sam's firm is ticked on the split day but named in no run: no agreement, no copy, and Parties 2 is right
    (before: "3 firms ordered pages", and 3 copies)."""
    job = split_day(tmp_path, s)
    job.case.attorneys[2].checked = True
    job.parties = 2
    job.refresh_copies(s)
    assert job.case.get("copies") == "2" and job.parties_mismatch() == ""


# ------------------------------------------------------------------ the run sheet of a case, and holding it
def trial(tmp_path, s, days=("June 3, 2026", "June 4, 2026")):
    """Two days of Jane Roe's trial, Alex's firm ticked on both."""
    paths = [str(transcript_pdf(tmp_path / f"Roe {d}.pdf", 20, date=d, initials=["pr"] * 10 + ["ds"] * 10))
             for d in days]
    docs, _ = read_docs(paths, s)
    jobs = sorted(group(docs, s), key=lambda j: j.case.get("dates"))
    for j in jobs:
        j.case.attorneys = [firm("Alex B. Counsel", "Counsel & Counsel")]
        j.att_touched = True
    return jobs


def test_the_takes_of_a_trial_go_on_its_run_sheet_in_one_save(tmp_path, s, monkeypatch):
    import minute_filler.runsheet as rs
    jobs = trial(tmp_path, s)
    assert [len(u) for u in sheet_units(jobs, ["runsheet"])] == [2]
    saves = []
    real = rs._save
    monkeypatch.setattr(rs, "_save", lambda wb, path: saves.append(path) or real(wb, path))
    made = fill_jobs(jobs, s, outputs=["runsheet"])
    assert len(saves) == 1 and len(made) == 1 and all(j.saved == made and not j.error for j in jobs)


def test_a_run_sheet_open_in_excel_holds_the_whole_case(tmp_path, s, monkeypatch):
    """Nothing is made for the case (no forms, no invoice, no run sheet with a day missing), and each day says why:
    they are done again together once the run sheet is closed."""
    import minute_filler.runsheet as rs
    jobs = trial(tmp_path, s)

    def locked(wb, path):
        raise PermissionError(f"{path.name} is open in another program - close it in Excel and try again")
    monkeypatch.setattr(rs, "_save", locked)
    made = fill_jobs(jobs, s, outputs=["agreement", "mofr", "invoice", "runsheet"])
    assert made == [] and not ledger_for(s).invoices() and not ledger_for(s).activity()
    for j in jobs:
        assert j.saved == [] and not j.invoiced
        assert j.error.startswith(HELD) and "open in another program - close it in Excel" in j.error


def test_another_case_is_made_when_one_is_held(tmp_path, s, monkeypatch):
    import minute_filler.runsheet as rs
    roe = trial(tmp_path, s)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    docs, _ = read_docs([str(transcript_pdf(other_dir / "Lee.pdf", 12, date="June 9, 2026"))], s)
    lee, = group(docs, s)
    lee.case.set("index_no", "700111/2025")
    lee.case.set("case_name", "Kim Lee v. Example Corp.")
    lee.case.attorneys = [firm("Dana Smith", "Smith Law")]
    real = rs._save

    def roe_locked(wb, path):
        if "Roe" in path.name:
            raise PermissionError(f"{path.name} is open in another program - close it in Excel and try again")
        real(wb, path)
    monkeypatch.setattr(rs, "_save", roe_locked)
    fill_jobs(roe + [lee], s, outputs=["agreement", "runsheet"])
    assert all(j.error.startswith(HELD) for j in roe)
    assert lee.saved and not lee.error


def test_a_held_case_gets_no_second_run_sheet(tmp_path, s, monkeypatch):
    """Two run sheets for one trial (the window asked about its days apart), the first open in Excel: the second
    isn't written either, and its forms aren't made."""
    import minute_filler.runsheet as rs
    jobs = trial(tmp_path, s)
    for n, j in enumerate(jobs):
        j.runsheet_group, j.runsheet_to = n, ""
    assert [len(u) for u in sheet_units(jobs, ["runsheet"])] == [1, 1]
    saves = []

    def first_locked(wb, path):
        saves.append(path)
        raise PermissionError(f"{path.name} is open in another program - close it in Excel and try again")
    monkeypatch.setattr(rs, "_save", first_locked)
    made = fill_jobs(jobs, s, outputs=["agreement", "runsheet"])
    assert len(saves) == 1 and made == []
    assert all(HELD in j.error and not j.saved for j in jobs)


def test_a_trial_whose_forms_fail_gets_no_invoice(tmp_path, s, monkeypatch):
    """The user's rule: hold the whole trial. Its forms couldn't be made, so its joint invoice waits for them."""
    import minute_filler.batch as batch
    jobs = trial(tmp_path, s)

    def broken(*args, **kwargs):
        raise OSError("the form template could not be read")
    monkeypatch.setattr(batch, "make_forms", broken)
    fill_jobs(jobs, s, outputs=["agreement", "invoice"])
    assert not ledger_for(s).invoices() and not any(j.invoiced for j in jobs)
    assert all(HELD in j.error and "form template" in j.error for j in jobs)


def test_a_folder_is_not_locked(tmp_path):
    from minute_filler.runsheet import is_locked
    assert not is_locked(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")
def test_a_file_held_by_another_program_is_locked(tmp_path):
    """As Excel holds an open workbook: no other program may write it."""
    import ctypes
    from minute_filler.runsheet import is_locked
    path = tmp_path / "Roe - Run Sheet.xlsx"
    path.write_bytes(b"x")
    assert not is_locked(path) and not is_locked(tmp_path / "not there.xlsx")
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)  # GENERIC_READ, no sharing, OPEN_EXISTING
    try:
        assert is_locked(path)
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    assert not is_locked(path)


# ------------------------------------------------------------------ rows that may be one firm
def test_a_firm_whose_name_starts_with_another_is_asked_about():
    a, b = firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC")
    assert not same_entry(a, b) and maybe_same_entry(a, b)
    assert len(dedupe_attorneys([a, b])) == 2  # (before: one firm, without asking)
    assert same_entry(firm("Dana Smith", "Smith Law, PLLC"), firm("Sam Poe", "SMITH LAW"))  # sure: joined


def test_mr_smith_and_dana_smith_are_asked_about():
    mr, dana = Attorney(name="Mr. Smith"), firm("Dana Smith", "Smith Law")
    assert not same_entry(mr, dana) and maybe_same_entry(mr, dana)
    assert same_entry(Attorney(name="Dana Smith"), dana)  # her whole name: surely her firm's


def test_the_users_answer_is_kept():
    a, b = firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC")
    use_firm_answers([[a.key(), b.key(), True]])
    assert same_entry(a, b) and not maybe_same_entry(a, b) and len(dedupe_attorneys([a, b])) == 1
    use_firm_answers([[b.key(), a.key(), False]])
    assert not same_entry(a, b) and not maybe_same_entry(a, b) and len(dedupe_attorneys([a, b])) == 2


def test_rows_said_to_be_one_firm_become_one_on_every_day():
    day1, day2 = Job(), Job()
    a, b = firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC", checked=False)
    day1.case.attorneys = [a, b]
    day1.portions = [(10, [b.key()]), (20, [a.key()])]
    day2.case.attorneys = [Attorney(name="Mr. Smith")]
    assert len(firm_questions([day1, day2])) == 2
    use_firm_answers([[a.key(), b.key(), True], [a.key(), "mr smith", True]])
    join_entries([day1, day2], a, b)
    join_entries([day1, day2], a, day2.case.attorneys[0])
    assert [(x.firm, x.name, x.checked) for x in day1.case.attorneys] == [("Smith Law", "Dana Smith, Sam Poe", True)]
    assert day1.portions == [(10, ["smith law"]), (20, ["smith law"])]
    assert day2.case.attorneys[0].key() == "smith law" and firm_questions([day1, day2]) == []


def test_one_attorney_on_two_days_without_a_firm_becomes_one(tmp_path):
    """'Mr. Smith' on one day and 'Dana Smith' on another, neither with a firm, said to be one: one key on both days
    (before: two rows, two invoices, and the question gone)."""
    from minute_filler.batch import group_attorneys
    day1, day2 = Job(), Job()
    day1.case.attorneys, day2.case.attorneys = [Attorney(name="Mr. Smith")], [Attorney(name="Dana Smith")]
    day2.portions = [(10, ["dana smith"]), (20, ["dana smith"])]
    a, b = day1.case.attorneys[0], day2.case.attorneys[0]
    assert firm_questions([day1, day2]) == [(a, b)]
    use_firm_answers([[a.key(), b.key(), True]])
    join_entries([day1, day2], a, b)
    assert day1.case.attorneys[0].key() == day2.case.attorneys[0].key() == "dana smith"
    assert day2.portions == [(10, ["dana smith"]), (20, ["dana smith"])]
    assert len(group_attorneys([day1, day2])) == 1


def test_the_answers_are_replaced_in_one_step():
    """Generate all reads the answers on another thread while Settings may be saved: the old dict is never emptied
    in place (its reader would see no answers for a moment)."""
    import minute_filler.extract_regex as er
    use_firm_answers([["smith law", "smith law group", True]])
    old = er._ANSWERS
    use_firm_answers([["roe legal", "roe legal partners", False]])
    assert old == {frozenset(("smith law", "smith law group")): True} and er._ANSWERS is not old


# ------------------------------------------------------------------ the window
@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt, s):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s.use_ai = s.open_after = False
    s.welcomed = True
    return make_window(s)


def test_the_attorneys_card_asks_and_keeps_the_answer(window):
    from minute_filler.settings import Settings
    window.cur.case.attorneys = [firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC")]
    window._show_case()
    window._update_status()
    assert window.firm_ask_box.isVisibleTo(window) and "Smith Law Group" in window.firm_ask.text()
    window._answer_firm(True)
    assert [a.firm for a in window.cur.case.attorneys] == ["Smith Law"]
    assert not window.firm_ask_box.isVisibleTo(window) and window.att.rowCount() >= 1
    assert Settings.load().firm_answers == [["smith law", "smith law group", True]]


def test_an_answer_joins_the_rows_of_every_job_loaded(window):
    """The answer is kept for every case, so every job loaded follows it (before: only the case asked about; the
    other kept two rows and was never asked again)."""
    from copy import deepcopy
    other = Job()
    other.case.set("case_name", "Kim Lee v. Example Corp.")
    other.case.set("index_no", "700111/2025")
    rows = [firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC")]
    window.cur.case.attorneys = rows
    other.case.attorneys = deepcopy(rows)
    window.jobs.append(other)
    window._record_firm_answer(rows[0], rows[1], True)
    assert [a.firm for a in other.case.attorneys] == ["Smith Law"] and firm_questions([other]) == []


def test_every_question_is_asked_when_a_read_merges_the_job_meanwhile(window, monkeypatch):
    """A read that ends while the first box is open gives the job new rows: the second pair is still asked about
    (before: skipped, and Generate went on with two rows of one firm)."""
    from copy import deepcopy
    from minute_filler.gui import main_window
    job = window.cur
    job.case.attorneys = [firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC"),
                          firm("Kim Lee", "Roe Legal"), firm("Pat Doe", "Roe Legal Partners")]
    asked = []

    def answer(box):
        asked.append(box.text())
        if len(asked) == 1:
            job.case.attorneys = deepcopy(job.case.attorneys)  # (as remerge does)
        next(b for b in box.buttons() if b.text() == "Same firm").click()
        return 0
    monkeypatch.setattr(main_window.QMessageBox, "exec", answer)
    assert window._ask_firms([[job]])
    assert len(asked) == 2 and firm_questions([job]) == []
    assert [a.firm for a in job.case.attorneys] == ["Smith Law", "Roe Legal"]


def test_generate_asks_and_go_back_makes_nothing(window, tmp_path):
    window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 20))])
    from test_gui_batch import wait
    wait(window, lambda: bool(window.cur.docs))
    window.cur.case.attorneys = [firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC")]
    window.cur.att_touched = True
    window._show_case()
    window.fill()  # the question's box is closed without an answer: as Go back
    assert window.cur.saved == [] and not list((tmp_path / "out").glob("*.pdf"))


def test_generate_says_when_the_parties_disagree_with_excerpts(window, tmp_path):
    job = split_day(tmp_path, window.s)
    window.jobs, window.cur = [job], job
    window._refresh_jobs()
    window._show_job()
    job.parties = 3
    window.fill()  # the box is closed without an answer: as Go back
    assert job.saved == []
    job.parties = 0
    assert window._ask_parties([job])


def test_generate_checks_the_run_sheet_first(window, tmp_path, monkeypatch):
    import minute_filler.runsheet as rs
    from minute_filler.gui import main_window
    shown = []
    monkeypatch.setattr(rs, "is_locked", lambda path: True)
    monkeypatch.setattr(main_window.QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    assert not window._sheets_free([str(tmp_path / "Roe - Run Sheet.xlsx"), ""], "Generate all")
    assert "Roe - Run Sheet.xlsx" in shown[0] and "press Generate all again" in shown[0]
    assert window._sheets_free(["", None], "Generate")
