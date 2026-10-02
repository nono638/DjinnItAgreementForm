"""Invoices that cover several days of one case: the joint invoice (or one per day, as Settings say), the index
rule, a job's Extras and what granular detail shows. Also: a day billed once (Job.invoiced) is not billed
again, choices kept when jobs or days come together (Who ordered... too), a failed joint invoice, a day with
nobody ticked holding the joint invoice back, the progress
count, the files counted for Generate all, Pages typed as 0, and the invoice's field names. Attorneys ordering
different days or pages are in test_portions.py. All names and numbers are made up."""
from decimal import Decimal

import pymupdf
import pytest

from minute_filler import batch
from minute_filler.batch import (expand_paths, files_to_make, fill_jobs, group, group_attorneys, invoice_groups,
                                 joint_invoice, read_docs)
from minute_filler.deliver import ledger_for
from minute_filler.invoice import InvoiceOpts, make_invoices
from minute_filler.invoice_calc import index_days, quote, quotes_for
from minute_filler.models import Attorney
from minute_filler.records import Ledger
from minute_filler.settings import Settings

from helpers import ROE, invoice_text, make_case, pat_settings, text_doc, transcript_pdf


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


@pytest.fixture
def three_days(tmp_path, s):
    """Three days of Roe v. X.Y. (30, 60 and 40 pages), read and grouped: three jobs, one per day."""
    d = tmp_path / "in"
    d.mkdir()
    for day, pages in (("June 3, 2026", 30), ("June 4, 2026", 60), ("June 5, 2026", 40)):
        transcript_pdf(d / f"Roe {day}.pdf", pages, date=day)
    docs, errors = read_docs(expand_paths([str(d)]), s)
    assert not errors
    jobs = group(docs, s)
    assert len(jobs) == 3
    for job in jobs:  # the same attorney ordered every day
        for a in job.case.attorneys:
            a.checked = "Counsel" in (a.firm or "")
    return jobs


def test_a_day_with_nobody_ticked_holds_the_joint_invoice_back(three_days, s):
    """The user's choice: warn instead of billing that day to everyone. The other outputs are still made."""
    from minute_filler.batch import group_problem
    for a in three_days[1].case.attorneys:  # nobody ticked on June 4
        a.checked = False
    g = invoice_groups(three_days, s)[0]
    assert "nobody is ticked on 6/4/2026" in group_problem(g)
    assert files_to_make(three_days, ["invoice"], s) == 0
    fill_jobs(three_days, s, outputs=["agreement", "invoice"])
    assert ledger_for(s).invoices() == []
    assert all("invoice not made: nobody is ticked on 6/4/2026" in j.error for j in three_days)
    assert not any(j.invoiced for j in three_days)  # billed once that day says who ordered it
    assert all(any(p.name.startswith("Minute Agreement") for p in j.saved) for j in three_days if j.case.orderers() != [None])
    for a in three_days[1].case.attorneys:  # ticked: the case is billed as one invoice again
        a.checked = "Counsel" in (a.firm or "")
    assert group_problem(invoice_groups(three_days, s)[0]) == ""
    # no attorney ticked on any day (a transcript without appearances): one invoice with a blank Bill To, as before
    for job in three_days:
        for a in job.case.attorneys:
            a.checked = False
    assert group_problem(invoice_groups(three_days, s)[0]) == ""


def test_index_rules():
    assert index_days([30, 60], "auto", "any", 50) == [True, True]   # one long day: every day gets an index
    assert index_days([30, 60], "auto", "each", 50) == [False, True]
    assert index_days([30, 30], "auto", "total", 50) == [True, True]  # 60 pages together
    assert index_days([30, 30], "auto", "any", 50) == [False, False]
    assert index_days([10], "on") == [True] and index_days([90], "off") == [False]


def test_prices_by_day(s):
    sp = s.sheet().find("Regular")
    one = quote(90, sp, 1)
    by_day = quote([30, 60], sp, 1)  # "any": both days indexed, as if one 90-page transcript
    assert by_day.total == one.total and by_day.pages == 90 and by_day.days == [30, 60]
    each = quote([30, 60], sp, 1, index="auto", rule="each")
    idx = next(l for l in each.lines if l.label == "Index")
    assert idx.pages == 60 and each.total < one.total
    assert "E-mailed copy" not in [l.label for l in quote([30, 60], sp, 1, include_email=False).lines]
    assert "Index" in [l.label for l in quote(10, sp, 1, index="on").lines]  # a short one, indexed anyway
    assert "Index" not in [l.label for l in quote(90, sp, 1, index="off").lines]
    # the job's Extras win over the settings
    q = quotes_for([10], 1, s.sheet(), s, "Regular", email=False, index="on")[0]
    assert [l.label for l in q.lines] == ["Original", "Copy", "Index", "Judge's index"]


def test_three_days_one_invoice(three_days, s):
    assert [len(g) for g in invoice_groups(three_days, s)] == [3]
    assert files_to_make(three_days, ["invoice"], s) == 1  # one ordering attorney, one joint invoice
    case, opts = joint_invoice(invoice_groups(three_days, s)[0])
    assert opts.days == [("6/3/2026", 30), ("6/4/2026", 60), ("6/5/2026", 40)] and opts.pages == 130
    assert case.get("dates") == "6/3/2026, 6/4/2026, 6/5/2026"

    fill_jobs(three_days, s, outputs=["agreement", "invoice"])
    invoices = {p for job in three_days for p in job.saved if p.name.startswith("Invoice")}
    agreements = [p for job in three_days for p in job.saved if p.name.startswith("Minute Agreement")]
    assert len(invoices) == 1 and len(agreements) == 3  # the agreements stay one per day
    assert all(invoices <= set(job.saved) and job.invoiced for job in three_days)  # the invoice is each day's
    assert not any(job.error for job in three_days)
    inv = ledger_for(s).invoices()[0]
    assert inv.pages == 130 and inv.dates == "6/3/2026, 6/4/2026, 6/5/2026"
    single = quotes_for([30, 60, 40], 1, s.sheet(), s, "Regular")[0]
    assert Decimal(inv.amounts["Regular"]) == single.per_party
    text = pymupdf.open(invoices.pop())[0].get_text()
    assert "6/3/2026, 6/4/2026, 6/5/2026" in text and "The transcripts are sent" in text


def test_an_invoice_per_day_when_settings_say_so(three_days, s):
    s.invoice_joint = False
    assert files_to_make(three_days, ["invoice"], s) == 3
    fill_jobs(three_days, s, outputs=["invoice"])
    assert len(ledger_for(s).invoices()) == 3


def test_one_selected_day_is_billed_alone(three_days, s):
    """Generate this job (one day picked from the batch) bills that day only."""
    job = three_days[1]
    assert job.invoice_opts().days == [("6/4/2026", 60)]
    fill_jobs([job], s, outputs=["invoice"])
    assert ledger_for(s).invoices()[0].pages == 60


def test_the_first_days_extras_apply_to_the_joint_invoice(three_days, s):
    for job in three_days:
        job.invoice_email, job.invoice_index = False, "off"
    _, opts = joint_invoice(invoice_groups(three_days, s)[0])
    assert opts.email is False and opts.index == "off"


def test_granular_detail_is_per_job_and_off_by_default(three_days, s):
    assert not any(job.invoice_opts().detail for job in three_days)
    assert joint_invoice(invoice_groups(three_days, s)[0])[1].detail is False
    three_days[1].invoice_detail = True  # ticked while looking at the second day: the joint invoice shows it
    assert joint_invoice(invoice_groups(three_days, s)[0])[1].detail is True


def test_granular_detail_shows_what_was_chosen(s, tmp_path):
    case = make_case(dict(ROE, dates="6/3/2026, 6/4/2026"))
    days = [("6/3/2026", 30), ("6/4/2026", 60)]

    def text(**kw) -> str:
        path = make_invoices(case, s, tmp_path / str(len(list(tmp_path.iterdir()))),
                             InvoiceOpts(90, days=days, detail=True, **kw), Ledger(tmp_path / "r.db"))[0]
        with pymupdf.open(path) as doc:
            page = doc[0]
            fields = {w.field_name: w.field_value for w in page.widgets()}
            for xref in [w.xref for w in page.widgets()]:
                page.delete_widget(page.load_widget(xref))
            return page.get_text(), fields

    every, fields = text()
    assert "Pages by day" in every and "Per page" in every and "Index: 90" in every
    assert fields["pages 6/3/2026"] == "30" and fields["pages 6/4/2026"] == "60" and fields["pages"] == "90"
    some, fields = text(show=["days"])
    assert "Pages by day" in some and "Per page" not in some and "Original:" not in some and "pages" not in fields
    s.invoice_detail_items = ["per_page"]  # the default for jobs that don't choose
    assert "Pages by day" not in text()[0] and "Per page" in text()[0]


def test_settings_v6(tmp_path):
    import json
    s = Settings()
    s.path.write_text(json.dumps({"settings_version": 5, "invoice_index_rule": "sometimes",
                                  "invoice_detail_items": ["pages", "bogus", ["x"]]}), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.invoice_index_rule == "any" and loaded.invoice_detail_items == ["pages"]
    assert loaded.invoice_joint is True and Settings().invoice_detail_items == ["pages", "days", "per_page",
                                                                               "charges", "split"]


# --------------------------------------------------------------- found in the sweep of the joint invoices

def test_a_day_already_invoiced_is_not_billed_again(three_days, s):
    """Generate this job, then Generate all (twice): every day is billed once."""
    fill_jobs([three_days[0]], s, outputs=["invoice"])  # one day picked and billed alone
    assert three_days[0].invoiced
    assert files_to_make(three_days, ["invoice"], s) == 1  # the two other days, on one invoice
    fill_jobs(three_days, s, outputs=["agreement", "invoice"])
    assert sorted(i.dates for i in ledger_for(s).invoices()) == ["6/3/2026", "6/4/2026, 6/5/2026"]
    assert all(j.invoiced and not j.error for j in three_days)
    assert files_to_make(three_days, ["invoice"], s) == 0
    fill_jobs(three_days, s, outputs=["invoice"])  # again: nothing left to bill, and that is no error
    assert len(ledger_for(s).invoices()) == 2 and not any(j.error or j.saved for j in three_days)


def test_a_new_document_makes_a_day_billable_again(three_days, s, tmp_path):
    for job in three_days:
        job.invoiced = True
    more = transcript_pdf(tmp_path / "Roe June 4, 2026 afternoon.pdf", 20, date="June 4, 2026")
    docs, _ = read_docs([str(more)], s)
    jobs = group(docs, s, three_days)
    assert [j.invoiced for j in jobs] == [True, False, True]


def test_a_failed_joint_invoice_is_reported_on_every_day(three_days, s, monkeypatch):
    real = batch.generate

    def generate(case, s, out_dir, outputs, *args, **kw):
        if list(outputs) == ["invoice"]:  # the joint invoice
            raise PermissionError("Invoice 2026-0001.pdf is open in another program")
        return real(case, s, out_dir, outputs, *args, **kw)

    monkeypatch.setattr(batch, "generate", generate)
    fill_jobs(three_days, s, outputs=["agreement", "invoice"])
    assert [j.error for j in three_days] == ["invoice: Invoice 2026-0001.pdf is open in another program"] * 3
    assert not any(j.invoiced for j in three_days)  # so Generate all tries again


def test_progress_counts_the_joint_invoice(three_days, s):
    seen = []
    fill_jobs(three_days, s, outputs=["invoice"], progress=lambda i, n, name: seen.append((i, n, name)))
    assert [(i, n) for i, n, _ in seen] == [(0, 4), (1, 4), (2, 4), (3, 4)]
    assert seen[-1][2].startswith("Invoice for Jane Roe")


def test_a_later_days_choices_are_kept_when_an_earlier_day_joins(three_days, s):
    later = three_days[1]
    later.invoice_email, later.invoice_index, later.invoice_show, later.parties = False, "on", ["days"], 3
    _, opts = joint_invoice(invoice_groups(three_days, s)[0])
    assert (opts.email, opts.index, opts.show, opts.parties) == (False, "on", ["days"], 3)


def test_jobs_joined_by_a_document_keep_their_invoice_choices(s):
    a = text_doc(s, "Can I get the transcript for Smith v Jones from 5/22/2026? Thanks", "a.txt")
    b = text_doc(s, invoice_text(title="Roe v Doe", index="712222-2024", date="5-22-2026"), "b.txt")
    jobs = group([a, b], s)
    assert len(jobs) == 2
    jobs[0].invoiced = True
    jobs[1].invoice_email, jobs[1].invoice_index, jobs[1].parties, jobs[1].runsheet_to = False, "off", 2, ""
    jobs[1].portions = [(10, ["dana smith"]), (20, ["dana smith", "alex b counsel"])]
    link = text_doc(s, invoice_text(title="Smith v Jones", index="712222-2024", date="5-22-2026"), "c.txt")
    joined, = group([link], s, jobs)  # names both: the two jobs become one
    assert (joined.invoice_email, joined.invoice_index, joined.parties, joined.runsheet_to) == (False, "off", 2, "")
    assert joined.portions == jobs[1].portions  # kept (and checked against the pages when billed)
    assert not joined.invoiced


def test_the_same_attorney_written_two_ways_gets_one_joint_invoice(three_days, s):
    for job, name, firm in zip(three_days, ("Dana Smith, Esq.", "Dana Smith Esq", "DANA SMITH, ESQ."),
                               ("Smith Law", "Smith Law PLLC", "")):
        job.case.attorneys = [Attorney(name=name, firm=firm, checked=True)]
    assert len(group_attorneys(three_days)) == 1 and files_to_make(three_days, ["invoice"], s) == 1


def test_the_invoices_of_one_day_are_counted_as_generate_makes_them(three_days, s):
    """The same attorney entered twice is one party, with one invoice (counted so too)."""
    day = three_days[0]
    day.case.attorneys = [Attorney(name="Dana Smith", firm="Smith Law", checked=True),
                          Attorney(name="Dana Smith", firm="Smith Law", checked=True)]
    assert files_to_make([day], ["invoice"], s) == 1 and day.invoice_opts().parties == 1
    fill_jobs([day], s, outputs=["invoice"])
    inv, = ledger_for(s).invoices()
    assert inv.parties == 1


def test_pages_typed_as_zero_leave_out_only_the_invoice(three_days, s):
    day = three_days[0]
    day.case.set("est_pages", "0")
    assert day.makeable(["agreement", "mofr", "invoice"]) == ["agreement", "mofr"]
    fill_jobs([day], s, outputs=["agreement", "mofr", "invoice"])
    assert [p.name.split(" - ")[0] for p in day.saved] == ["Minute Agreement", "MOFR"]
    assert day.error == "no invoice: the Pages field says 0"


def test_invoice_field_names_have_no_dots_and_do_not_repeat(s, tmp_path):
    """A dot would nest a field under another; two fields of one name share their value."""
    case = make_case(dict(ROE, dates="Sept. 3, 2026"))
    days = [("Sept. 3, 2026", 30), ("", 20), ("", 10)]
    path = make_invoices(case, s, tmp_path, InvoiceOpts(60, days=days, detail=True), Ledger(tmp_path / "r.db"))[0]
    with pymupdf.open(path) as doc:
        names = [w.field_name for w in doc[0].widgets()]
    assert len(names) == len(set(names)) and not any("." in n for n in names)
    assert {"pages Sept 3, 2026", "pages", "pages 2", "pages 3", "amount Regular", "per page Regular"} <= set(names)
