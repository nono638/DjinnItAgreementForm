"""A minute agreement counts every page its attorney ordered, whoever wrote them; each reporter's invoice bills
only that reporter's pages of them. The reporter's own example: one day of trial, 150 pages, Pat Reporter ("PR")
wrote pages 1-120 and Dana Smith ("DS") 121-150. Alex B. Counsel (firm A) ordered the whole day, Sam Advocate
(firm B) pages 71-150. A's agreement says 150 pages and B's 80. Pat's invoice bills A for 70 pages at the
one-party rate and 50 at the split rate, B for 50 at the split rate; Dana's bill A and B 30 pages each at the
split rate. The same on the forms of a trial of two days (Generate all), with a typed page count, and without a
transcript. The Est. number of pages field shows the transcript's whole count (typed again, it is still the count;
another number is the user's); the MOFR the pages anyone ordered, a run nobody ordered on no form. All names and
numbers are made up."""
from decimal import Decimal

import pymupdf
import pytest

from minute_filler.batch import _date_key, case_forms, fill_jobs, form_groups, group, read_docs, Job
from minute_filler.deliver import ledger_for
from minute_filler.invoice import ORDERED_BY_NOBODY
from minute_filler.invoice_calc import money
from minute_filler.models import SRC_PDF, SRC_USER, Attorney, FieldState
from minute_filler.settings import ReporterProfile

from helpers import ROE, make_case, pat_settings, transcript_pdf

PR, DS = "pr", "ds"


def alex():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def sam():
    return Attorney(name="Sam Advocate", firm="Advocate LLP", checked=True)


A, B = alex().key(), sam().key()


@pytest.fixture
def s(tmp_path):
    s = pat_settings(initials="pr")
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.invoice_speeds = ["Regular"]
    s.invoice_include_index = False  # (the original and the copy alone: the arithmetic below)
    s.invoice_include_email = False
    s.reporters = {DS: ReporterProfile(name="Dana", full_name="Dana Smith", address1="9 Sample Lane",
                                       email="dsmith@example.com", payment="Zelle: (555) 010-0199")}
    return s


def rates(s) -> tuple[Decimal, Decimal]:
    """The Regular original and copy rates of the rate sheet."""
    sp = s.sheet().find("Regular")
    return money(sp.original), money(sp.copy)


def jobs_of(tmp_path, s, days: dict) -> list[Job]:
    """The jobs of these transcripts ({date: initials of each page}), earliest first, each ordered by Alex B.
    Counsel and Sam Advocate, Pat's and Dana's pages both billed (Whose pages...)."""
    paths = [str(transcript_pdf(tmp_path / f"Roe {day}.pdf", len(marks), date=day, initials=marks))
             for day, marks in days.items()]
    docs, errors = read_docs(paths, s)
    assert not errors
    jobs = sorted(group(docs, s), key=lambda j: _date_key(j.case.get("dates")))
    for job in jobs:
        job.case.attorneys = [alex(), sam()]
        for d in job.transcripts():
            job.page_basis[d.key()] = ["me", DS]
    return jobs


DAY_1 = {"June 3, 2026": [PR] * 120 + [DS] * 30}  # 150 pages: Pat's 1-120, Dana's 121-150


@pytest.fixture
def day(tmp_path, s) -> Job:
    """The example's day: firm A the whole day, firm B pages 71-150."""
    job, = jobs_of(tmp_path, s, DAY_1)
    job.portions = [(70, [A]), (150, [A, B])]
    assert not job.invoice_hold()
    return job


def field_values(path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [str(w.field_value) for w in doc[0].widgets()]


def agreements(s) -> dict[str, int]:
    """The pages of each agreement made, by attorney, as recorded and as written on the form."""
    out = {}
    for row in ledger_for(s).activity("agreement"):
        assert str(row.pages) in field_values(row.file_path)
        out[row.attorney] = row.pages
    return out


def invoices(math) -> dict[tuple[str, str], list]:
    """(reporter, attorney) -> the Original line's parts and the amount due, of each invoice made."""
    return {(f.opts.reporter, f.atty.name): ([l.parts for l in f.quotes[0].lines if l.label == "Original"][0],
                                             f.quotes[0].per_party, f.opts.pages)
            for f, _ in math}


# ------------------------------------------------------------------ one day

def test_each_agreement_counts_the_pages_its_attorney_ordered(day, s):
    assert day.transcript_pages() == 150 and day.invoice_pages() == 120  # Pat's own pages are billed
    assert day.ordered_pages() == {A: 150, B: 80, "": 150}
    math = []
    fill_jobs([day], s, outputs=["agreement", "mofr", "invoice"], math=math)
    assert not day.error
    assert agreements(s) == {"Alex B. Counsel": 150, "Sam Advocate": 80}
    mofr, = ledger_for(s).activity("mofr")
    assert mofr.pages == 150 and "150" in field_values(mofr.file_path)  # the pages anyone ordered


def test_each_reporter_bills_their_own_pages_of_what_each_firm_ordered(day, s):
    o, c = rates(s)
    math = []
    fill_jobs([day], s, outputs=["invoice"], math=math)
    got = invoices(math)
    assert set(got) == {("", "Alex B. Counsel"), ("", "Sam Advocate"), (DS, "Alex B. Counsel"),
                        (DS, "Sam Advocate")}
    # Pat: A 70 pages alone + 50 shared with B (pp. 71-120); B the 50 shared
    assert got[("", "Alex B. Counsel")] == ([(70, 1), (50, 2)], 70 * o + 50 * o / 2 + 120 * c, 120)
    assert got[("", "Sam Advocate")] == ([(50, 2)], 50 * o / 2 + 50 * c, 50)
    # Dana: pp. 121-150, ordered by both
    assert got[(DS, "Alex B. Counsel")] == ([(30, 2)], 30 * o / 2 + 30 * c, 30)
    assert got[(DS, "Sam Advocate")] == ([(30, 2)], 30 * o / 2 + 30 * c, 30)
    assert {(i.reporter or "", i.bill_to, i.pages) for i in ledger_for(s).invoices()} == {
        ("", "Alex B. Counsel", 120), ("", "Sam Advocate", 50), (DS, "Alex B. Counsel", 30),
        (DS, "Sam Advocate", 30)}


def test_the_window_makes_the_same_agreements(day, s, make_window, qt, monkeypatch):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s.use_ai = s.open_after = s.show_math = False
    s.welcomed = True
    s.outputs = ["agreement", "mofr"]
    win = make_window(s, preview=True)  # (the preview makes them too: with the same pages)
    win.jobs, win.cur = [day], day
    win._refresh_jobs()
    win._show_job()
    assert win.rows["est_pages"].text() == "150" and win.rows["est_pages"].state.source == SRC_PDF
    assert "Your pages: 120 of the 150 total transcribed pages" in win.inv_pages_info.text()
    assert "120" not in win.rows["est_pages"].state.alternatives  # (picked, it would be a typed number)
    win.fill()
    assert agreements(s) == {"Alex B. Counsel": 150, "Sam Advocate": 80}
    assert ledger_for(s).activity("mofr")[0].pages == 150


def test_a_run_nobody_ordered_is_on_no_agreement_nor_the_mofr(day, s):
    day.portions = [(20, [ORDERED_BY_NOBODY]), (70, [A]), (150, [A, B])]
    assert not day.invoice_hold()
    assert day.ordered_pages() == {A: 130, B: 80, "": 130}
    fill_jobs([day], s, outputs=["agreement", "mofr"])
    assert agreements(s) == {"Alex B. Counsel": 130, "Sam Advocate": 80}
    assert ledger_for(s).activity("mofr")[0].pages == 130


def test_a_typed_page_count_is_the_days_pages_on_every_agreement(day, s):
    day.portions = None  # every firm ordered the day
    day.case.set("est_pages", "100")
    assert day.pages_typed() and day.ordered_pages() == {A: 100, B: 100, "": 100}
    fill_jobs([day], s, outputs=["agreement", "mofr", "invoice"])
    assert agreements(s) == {"Alex B. Counsel": 100, "Sam Advocate": 100}
    assert ledger_for(s).activity("mofr")[0].pages == 100
    pages = {(i.reporter or "", i.bill_to): i.pages for i in ledger_for(s).invoices()}
    assert pages[("", "Alex B. Counsel")] == 100 and pages[(DS, "Sam Advocate")] == 30  # Dana: her own pages
    # with Excerpts... rows of the typed pages, an excerpt counts its runs of them
    day.portions = [(40, [A]), (100, [A, B])]
    assert not day.portions_problem() and day.ordered_pages() == {A: 100, B: 60, "": 100}


def test_without_a_transcript_the_forms_show_the_field(tmp_path, s):
    """No transcript, the pages typed in: they are the day's pages, every attorney ordering all of them (and an
    excerpt its runs of them, as with a transcript). A number read from a document is no count of the user's:
    the forms show the field as it is."""
    job = Job(case=make_case({**ROE, "est_pages": "42"}, attorneys=[alex(), sam()]))
    assert job.ordered_pages() == {A: 42, B: 42, "": 42}
    fill_jobs([job], s, outputs=["agreement", "mofr"])
    assert not job.error
    assert agreements(s) == {"Alex B. Counsel": 42, "Sam Advocate": 42}
    assert ledger_for(s).activity("mofr")[0].pages == 42


def test_the_field_shows_every_page_and_a_typed_count_stays(day, s):
    """The count typed in again billed the whole transcript (Dana's pages too) on Pat's invoice: it is the count;
    another number typed is the user's."""
    from minute_filler.batch import remerge
    fs = day.case.fields["est_pages"]
    assert (fs.value, fs.source) == ("150", SRC_PDF) and "120" not in fs.alternatives
    day.case.set("est_pages", "150")  # the count, typed again: still Pat's own pages billed
    assert not day.pages_typed() and day.invoice_pages() == 120
    remerge(day, s)
    assert day.case.fields["est_pages"].source == SRC_PDF and day.invoice_pages() == 120
    day.case.set("est_pages", "140")  # another number: the user's, billed as typed
    remerge(day, s)
    assert day.pages_typed() and day.invoice_pages() == 140
    day.case.fields["est_pages"] = FieldState("120", SRC_USER, 1.0, [])  # the pages billed anyway
    remerge(day, s)
    assert day.case.get("est_pages") == "150" and not day.pages_typed() and day.invoice_pages() == 120


# ------------------------------------------------------------------ a trial of two days

TRIAL = {**DAY_1, "June 4, 2026": [PR] * 20 + [DS] * 20}  # day 2: 40 pages, ordered whole by both firms


@pytest.fixture
def trial(tmp_path, s) -> list[Job]:
    jobs = jobs_of(tmp_path, s, TRIAL)
    assert [j.case.get("dates") for j in jobs] == ["6/3/2026", "6/4/2026"]
    jobs[0].portions = [(70, [A]), (150, [A, B])]
    return jobs


def test_the_trials_agreements_count_every_page_each_firm_ordered(trial, s):
    g, = form_groups(trial, s)
    forms = {(f.kind, f.atty.name if f.atty else ""): f for f, _ in case_forms(g, s, ["agreement", "mofr"])}
    assert {k: f.pages for k, f in forms.items()} == {("agreement", "Alex B. Counsel"): 190,
                                                       ("agreement", "Sam Advocate"): 120, ("mofr", ""): 190}
    assert forms[("agreement", "Sam Advocate")].my_pages == 140  # (the records: Pat's pages of its days)
    o, c = rates(s)
    math = []
    fill_jobs(trial, s, outputs=["agreement", "mofr", "invoice"], math=math)
    assert not any(j.error for j in trial)
    assert agreements(s) == {"Alex B. Counsel": 190, "Sam Advocate": 120}
    mofr, = ledger_for(s).activity("mofr")
    assert mofr.pages == 190 and mofr.dates == "6/3/2026, 6/4/2026"
    got = invoices(math)
    # Pat: day 1 as above, day 2 her 20 pages shared by both firms
    assert got[("", "Alex B. Counsel")] == ([(70, 1), (70, 2)], 70 * o + 70 * o / 2 + 140 * c, 140)
    assert got[("", "Sam Advocate")] == ([(70, 2)], 70 * o / 2 + 70 * c, 70)
    # Dana: 30 pages of day 1 and 20 of day 2, both shared
    assert got[(DS, "Alex B. Counsel")] == ([(50, 2)], 50 * o / 2 + 50 * c, 50)
    assert got[(DS, "Sam Advocate")] == ([(50, 2)], 50 * o / 2 + 50 * c, 50)
