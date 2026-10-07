"""Excerpts (who ordered which pages): each firm's invoice bills only the days it is ticked on and, of a day
split in the Excerpts window (Job.portions), only the pages it ordered. Pages ordered together share the original,
the judge's index and (by default) the index; each firm pays its own copies. Also the index setting, the Parties
number (and one below the firms ticked), portions kept to be checked when the pages or the attorneys change, the
rows following a firm renamed or filled in by a merge, the same attorney entered twice, a run stopped part way,
the files counted for Generate all and the records of each firm. All names and numbers are made up; prices are from the bundled "Sample Rates" sheet (Regular: original
$4.30, copy, e-mailed copy and index $1.00 a page; Expedite: $5.40 and $1.10)."""
import json
from decimal import Decimal

import pymupdf
import pytest

from minute_filler.batch import (Job, expand_paths, files_to_make, fill_jobs, group, invoice_groups,
                                 joint_invoice, read_docs, remerge)
from minute_filler.deliver import ledger_for
from minute_filler.invoice import DayOrder, InvoiceOpts, Portion, firm_invoices, invoice_count, make_invoices
from minute_filler.invoice_calc import Share, index_days, quote, quote_shares
from minute_filler.models import Attorney
from minute_filler.records import Ledger
from minute_filler.settings import Settings

from helpers import ROE, make_case, pat_settings, transcript_pdf


def alex(checked=True):
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=checked)


def dana(checked=True):
    return Attorney(name="Dana Smith", firm="Smith Law", checked=checked)


A, B = alex().key(), dana().key()


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def read_days(tmp_path, s, pages: dict[str, int]):
    """One job per day of Roe v. X.Y. (a transcript each, {"June 3, 2026": 30, ...}), earliest first (one job
    for all of them when Settings.batch_combine_dates says so)."""
    d = tmp_path / "in"
    d.mkdir()
    for day, n in pages.items():
        transcript_pdf(d / f"Roe {day}.pdf", n, date=day)
    docs, errors = read_docs(expand_paths([str(d)]), s)
    assert not errors
    jobs = sorted(group(docs, s), key=lambda j: j.invoice_days()[0][0])
    assert sum(j.invoice_pages() for j in jobs) == sum(pages.values())
    return jobs


@pytest.fixture
def four_days(tmp_path, s):
    """The user's example: day 1 is Alex only, day 2 Dana only, day 3 both, and on day 4 the first 40 pages are
    Dana's only and the next 50 both's."""
    jobs = read_days(tmp_path, s, {"June 3, 2026": 30, "June 4, 2026": 20, "June 5, 2026": 60, "June 6, 2026": 90})
    for job, who in zip(jobs, ([alex()], [dana()], [alex(), dana()], [alex(), dana()])):
        job.case.attorneys = who
    jobs[3].portions = [(40, [B]), (90, [A, B])]
    return jobs


# ------------------------------------------------------------------ the arithmetic

def test_every_firm_ordering_every_page_pays_what_the_whole_invoice_splits(s):
    """quote_shares for one firm and quote for the whole invoice agree when every firm ordered every page."""
    sheet = s.sheet()
    for mode in ("split", "each"):
        for days in ([10], [49], [60], [30, 60], [7, 33, 133]):
            flags = index_days(days)
            for n in (1, 2, 3, 4):
                for sp in sheet.speeds:
                    whole = quote(days, sp, n, index_shared=mode)
                    mine = quote_shares([Share(p, n, f) for p, f in zip(days, flags)], sp,
                                        index_split=mode == "split")
                    assert abs(mine.per_party - whole.per_party) <= Decimal("0.01"), (mode, days, n, sp.name)
                    assert mine.pages == sum(days) and mine.parties == n


def test_the_four_days_priced_by_hand(four_days, s):
    """Alex: 30 pages alone, 60 shared, 50 shared. Dana: 20 alone, 60 shared, 40 alone, 50 shared. Every day is
    indexed (one of them has 50 pages or more)."""
    case, opts = joint_invoice(invoice_groups(four_days, s)[0])
    firms = {f.atty.name: f for f in firm_invoices(case, s, opts)}
    assert set(firms) == {"Alex B. Counsel", "Dana Smith"}
    a, d = firms["Alex B. Counsel"], firms["Dana Smith"]
    # Regular, Alex: original (30 + 60/2 + 50/2 = 85 pp.) x 4.30 = 365.50; copy and e-mailed copy 140 pp. x 1.00
    # each; index and judge's index 85 pp. x 1.00 each: 365.50 + 140 + 140 + 85 + 85 = 815.50
    # Dana: original (20 + 30 + 40 + 25 = 115 pp.) x 4.30 = 494.50 + 170 + 170 + 115 + 115 = 1064.50
    assert [q.per_party for q in a.quotes] == [Decimal("815.50"), Decimal("954.00")]  # Expedite: 85 x 5.40 = 459
    assert [q.per_party for q in d.quotes] == [Decimal("1064.50"), Decimal("1248.00")]  # ... + 154 + 154 + 93.5 x 2
    # together they pay for one original, a copy of each page each ordered, one index and the judge's
    whole = 200 * Decimal("4.30") + 2 * (140 + 170) + 200 + 200
    assert a.quotes[0].per_party + d.quotes[0].per_party == whole
    assert (a.opts.pages, a.opts.days) == (140, [("6/3/2026", 30), ("6/5/2026", 60), ("6/6/2026", 50)])
    assert (d.opts.pages, d.opts.days) == (170, [("6/4/2026", 20), ("6/5/2026", 60), ("6/6/2026", 90)])
    assert a.case.get("dates") == "6/3/2026, 6/5/2026, 6/6/2026" and a.opts.parties == 2

    s.invoice_index_shared = "each"  # an index for each firm: Alex pays 140 index pages, not 85
    firms = {f.atty.name: f for f in firm_invoices(case, s, opts)}
    assert firms["Alex B. Counsel"].quotes[0].per_party == Decimal("870.50")
    assert firms["Dana Smith"].quotes[0].per_party == Decimal("1119.50")


def test_the_index_of_an_excerpt_is_split_like_the_practice(s):
    """A 100-page transcript; side B ordered a 10-page excerpt of it. The index is billed by the transcript's
    pages: A pays 90 pages at the one-sided rate and 10 at the split rate, B the 10 at the split rate."""
    sp = s.sheet().find("Regular")  # index $1.00 a page
    a = quote_shares([Share(90, 1, True), Share(10, 2, True)], sp)
    b = quote_shares([Share(10, 2, True)], sp)
    index = {name: next(l for l in q.lines if l.label == "Index") for name, q in (("A", a), ("B", b))}
    assert index["A"].amount == Decimal("95.00")  # 90 x $1.00 + 10 x $0.50
    assert index["B"].amount == Decimal("5.00")   # 10 x $0.50
    # the judge's index the same way: A pays for 95 pages of it, B for 5
    judge = {name: next(l for l in q.lines if l.label == "Judge's index") for name, q in (("A", a), ("B", b))}
    assert (judge["A"].amount, judge["B"].amount) == (Decimal("95.00"), Decimal("5.00"))


def test_a_share_is_rounded_up_to_the_cent(s):
    sp = s.sheet().find("Regular")
    q = quote_shares([Share(10, 3)], sp)  # 10 x 4.30 / 3 + 10 + 10 = 34.3333...
    assert q.per_party == Decimal("34.34") and q.lines[0].pages == Decimal("3.33") and q.lines[0].shared
    assert not q.lines[1].shared and q.lines[1].pages == 10
    assert q.per_party * 3 >= 10 * Decimal("4.30") + 60  # three firms cover the whole price


def test_the_index_setting_is_kept_and_checked(tmp_path):
    """invoice_index_shared defaults to "split"; a value it can't be ("sometimes") loads as "split", "each" as
    saved."""
    assert Settings().invoice_index_shared == "split" and Settings().settings_version == 10
    s = Settings()
    s.path.write_text(json.dumps({"settings_version": 6, "invoice_index_shared": "sometimes"}), encoding="utf-8")
    assert Settings.load().invoice_index_shared == "split"
    s.path.write_text(json.dumps({"settings_version": 7, "invoice_index_shared": "each"}), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.invoice_index_shared == "each" and loaded.settings_version == Settings.settings_version


# ------------------------------------------------------------------ the days and pages of each firm

def test_generate_all_makes_an_invoice_per_firm_for_its_own_days_and_pages(four_days, s):
    assert files_to_make(four_days, ["invoice"], s) == 2
    fill_jobs(four_days, s, outputs=["invoice"])
    assert not any(j.error for j in four_days) and all(j.invoiced for j in four_days)
    rows = {i.bill_to: i for i in ledger_for(s).invoices()}
    assert set(rows) == {"Alex B. Counsel", "Dana Smith"}
    a, d = rows["Alex B. Counsel"], rows["Dana Smith"]
    assert (a.pages, a.parties, a.dates, a.amounts) == (140, 2, "6/3/2026, 6/5/2026, 6/6/2026",
                                                        {"Regular": "815.50", "Expedite": "954.00"})
    assert (d.pages, d.dates, d.amounts["Regular"]) == (170, "6/4/2026, 6/5/2026, 6/6/2026", "1064.50")
    made = {a.attorney: a for a in ledger_for(s).activity(kind="invoice")}
    assert made["Alex B. Counsel"].pages == 140 and made["Dana Smith"].dates == "6/4/2026, 6/5/2026, 6/6/2026"
    # every day lists both invoices
    assert all(len([p for p in j.saved if p.name.startswith("Invoice")]) == 2 for j in four_days)
    with pymupdf.open(a.file_path) as doc:
        fields = {w.field_name: w.field_value for w in doc[0].widgets()}
        text = doc[0].get_text()
    assert fields["dates"] == "6/3/2026, 6/5/2026, 6/6/2026" and fields["amount Regular"] == "$815.50"
    assert "delivered once every party has paid" in text  # some of its pages are shared


def test_a_firm_is_billed_only_for_the_days_it_is_ticked(tmp_path, s):
    jobs = read_days(tmp_path, s, {"June 3, 2026": 30, "June 4, 2026": 20})
    jobs[0].case.attorneys = [alex(), dana(checked=False)]
    jobs[1].case.attorneys = [alex(checked=False), dana()]
    case, opts = joint_invoice(invoice_groups(jobs, s)[0])
    firms = {f.atty.name: f for f in firm_invoices(case, s, opts)}
    # each alone on its day: the whole of it, as a one-party invoice (30 x 6.30, 20 x 6.30; no index under 50)
    assert firms["Alex B. Counsel"].opts.days == [("6/3/2026", 30)]
    assert firms["Alex B. Counsel"].quotes[0].per_party == Decimal("189.00")
    assert firms["Dana Smith"].quotes[0].per_party == Decimal("126.00")
    assert firms["Dana Smith"].case.get("dates") == "6/4/2026" and firms["Dana Smith"].opts.parties == 1


def test_the_parties_number_shares_a_day_without_portions(tmp_path, s):
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    job.case.attorneys = [alex()]
    job.parties = 2  # someone else ordered too, not among the attorneys
    f, = firm_invoices(job.case, s, job.invoice_opts())
    # 30 x 4.30 / 2 + 30 + 30 = 124.50, as the whole invoice split two ways
    assert f.quotes[0].per_party == Decimal("124.50") == quote(30, s.sheet().find("Regular"), 2).per_party
    job.case.attorneys = [alex(), dana()]
    job.portions = [(10, [A]), (30, [A, B])]  # a day split by Excerpts...: the number no longer applies
    firms = {f.atty.name: f.quotes[0].per_party for f in firm_invoices(job.case, s, job.invoice_opts())}
    # Alex: 10 alone + 20 shared: (10 + 10) x 4.30 + 30 + 30 = 146.00; Dana: 10 x 4.30 + 20 + 20 = 83.00
    assert firms == {"Alex B. Counsel": Decimal("146.00"), "Dana Smith": Decimal("83.00")}


def test_a_ticked_attorney_who_ordered_no_pages_gets_no_invoice(tmp_path, s):
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    job.case.attorneys = [alex(), dana()]
    job.portions = [(30, [A])]
    assert files_to_make([job], ["invoice"], s) == 1 and invoice_count(job.case, job.invoice_opts()) == 1
    fill_jobs([job], s, outputs=["invoice"])
    inv, = ledger_for(s).invoices()
    assert inv.bill_to == "Alex B. Counsel" and inv.amounts["Regular"] == "189.00" and inv.parties == 1


def test_portions_that_no_longer_fit_are_kept_to_be_checked(tmp_path, s):
    """The pages typed again ("30" -> "3" -> "30") leave the rows as they were; while they don't fit, the day
    needs checking and Generate all makes no invoice for it."""
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    job.case.attorneys = [alex(), dana()]
    rows = [(10, [A]), (30, [A, B])]
    job.portions = list(rows)
    assert [p.pages for p in job.invoice_orders()[0].portions] == [10, 20]
    job.case.set("est_pages", "3")  # typed in: the last row no longer ends on the last page
    assert job.valid_portions() is None and job.portions == rows  # kept, not dropped
    assert job.portions_check() == "Excerpts… needs checking (its rows don't end on this day's 3 pages)"
    assert files_to_make([job], ["invoice"], s) == 0
    job.case.set("est_pages", "30")
    assert job.valid_portions() == rows and not job.portions_check()
    for bad in ([(10, [A]), (5, [B]), (30, [A])], [(30, [])], [], [("x", [A])]):
        job.portions = bad
        assert job.valid_portions() is None and job.portions == bad and job.portions_problem(), bad


def test_portions_need_a_job_of_one_day(tmp_path, s):
    """A job of two days is not refused by the Excerpts window (it is listed, ordered whole), but rows splitting
    it don't bill; a job without transcript pages can't be split at all."""
    s.batch_combine_dates = True  # both days in one job
    job, = read_days(tmp_path, s, {"June 3, 2026": 30, "June 4, 2026": 20})
    assert not job.portions_unavailable()  # (in the Excerpts window, ordered whole)
    job.portions = [(10, [A]), (50, [A, B])]
    assert job.valid_portions() is None
    assert "no transcript pages" in Job().portions_unavailable()


def test_a_day_with_nobody_ticked_is_billed_to_everyone_on_the_invoice(s, tmp_path):
    """Nobody ticked on the second day: its pages are not left unbilled."""
    case = make_case(dict(ROE, dates="6/3/2026, 6/4/2026"), [alex(), dana()])
    opts = InvoiceOpts(50, days=[("6/3/2026", 30), ("6/4/2026", 20)],
                       orders=[DayOrder("6/3/2026", 30, [Portion(30, [A], 1)]), DayOrder("6/4/2026", 20, [Portion(20, [])])])
    firms = {f.atty.name: f.opts.days for f in firm_invoices(case, s, opts)}
    assert firms == {"Alex B. Counsel": [("6/3/2026", 30), ("6/4/2026", 20)], "Dana Smith": [("6/4/2026", 20)]}


def test_a_firms_detail_says_which_pages_are_shared(tmp_path, s):
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    job.case.attorneys = [alex(), dana()]
    job.portions = [(10, [A]), (30, [A, B])]
    job.invoice_detail = True
    paths = make_invoices(job.case, s, tmp_path / "out", job.invoice_opts(), Ledger(tmp_path / "r.db"))
    with pymupdf.open(paths[0]) as doc:
        text = " ".join(doc[0].get_text().replace("ﬁ", "fi").replace("E-\n", "E-").split())
    assert "Original: 10 pp. × $4.30 + 20 pp. × $4.30 ÷ 2 firms" in text
    assert "Copy: 30 pp. × $1.00 + E-mailed copy: 30 pp. × $1.00 = $146.00" in text
    assert "split 2 ways" not in text and "Amounts are your share" in text
    assert "the original, the index and the judge's index of pages ordered together" in text


# ------------------------------------------------------------------ found in the sweep of who ordered which pages

def test_rows_naming_an_attorney_no_longer_ticked_bill_nobody_until_checked(tmp_path, s):
    """Dana is unticked (or her firm renamed) after the day was split: the rows are kept, the day needs checking,
    and Generate all makes no invoice for it, rather than billing Dana's pages to Alex. Another name in the firm's
    row is the same firm, and the rows still name it."""
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    job.case.attorneys = [alex(), dana()]
    job.portions = [(10, [A]), (30, [A, B])]
    job.case.attorneys[1].checked = False
    assert job.valid_portions() is None and job.portions == [(10, [A]), (30, [A, B])]
    assert job.portions_check() == "Excerpts… needs checking (it names an attorney no longer ticked)"
    assert files_to_make([job], ["invoice"], s) == 0
    fill_jobs([job], s, outputs=["agreement", "invoice"])
    assert not ledger_for(s).invoices() and not job.invoiced
    assert job.error == "invoice not made: check Excerpts… for 6/3/2026"
    assert [p.name.split(" - ")[0] for p in job.saved] == ["Minute Agreement"]  # the rest is made
    job.portions = [(10, [A]), (30, [A])]  # names only Alex: fine (one firm may order a day in runs)
    assert job.portions_problem() == "" and job.valid_portions() == [(10, [A]), (30, [A])]
    job.portions = [(10, [A]), (30, [A, B])]

    job.case.attorneys[1].checked = True
    job.case.attorneys[1].name = "Dana M. Smith, Robin Smith"  # the firm's row names more: the same firm, key
    assert job.portions_problem() == "" and job.case.attorneys[1].key() == B
    job.case.attorneys[1].firm = "Smith & Lane Law"  # the firm renamed: the rows still say "smith law"
    assert job.portions_problem() == "it names an attorney no longer ticked"
    job.rename_in_portions(B, job.case.attorneys[1].key())  # as the window does when the firm is edited
    assert job.valid_portions() == [(10, [A]), (30, [A, "smith and lane law"])]
    assert files_to_make([job], ["invoice"], s) == 2


def test_a_name_filled_in_by_a_merge_renames_the_attorney_in_the_rows(tmp_path, s):
    """An attorney typed with the firm only gets its name from a document read later (dedupe_attorneys): the
    entry keeps its key (the firm's), so the Excerpts... rows still name it; an attorney typed with the name only
    gets the firm from a document, and the rows follow the new key (the firm's)."""
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    firm_only = Attorney(firm="Counsel & Counsel", email="ab@counsel.example", checked=True)
    name_only = Attorney(name="Dana Smith", checked=True)
    job.case.attorneys = [firm_only, name_only]
    job.att_touched = True
    job.portions = [(10, [firm_only.key()]), (30, [firm_only.key(), "dana smith"])]
    job.docs[0].regex.attorneys = [Attorney(name="Alex B. Counsel", email="ab@counsel.example"),
                                   Attorney(name="Dana Smith", firm="Smith Law")]
    remerge(job, s)
    assert [(a.name, a.firm) for a in job.case.attorneys] == [("Alex B. Counsel", "Counsel & Counsel"),
                                                              ("Dana Smith", "Smith Law")]
    assert job.portions == [(10, [A]), (30, [A, B])] and job.valid_portions()


def test_portions_of_a_day_name_only_its_own_attorneys(tmp_path, s):
    """Dana is ticked on the second day only; rows of the first day giving Dana pages need checking: Generate all
    bills the second day and leaves the first day's invoice out, saying why."""
    jobs = read_days(tmp_path, s, {"June 3, 2026": 30, "June 4, 2026": 60})
    jobs[0].case.attorneys = [alex(), dana(checked=False)]
    jobs[1].case.attorneys = [alex(), dana()]
    assert not jobs[0].portions_unavailable() and not jobs[1].portions_unavailable()  # (one firm is enough)
    jobs[0].portions = [(10, [B]), (30, [A])]
    assert jobs[0].portions_problem() == "it names an attorney no longer ticked"
    fill_jobs(jobs, s, outputs=["invoice"])
    assert {(i.bill_to, i.dates) for i in ledger_for(s).invoices()} == {("Alex B. Counsel", "6/4/2026"),
                                                                       ("Dana Smith", "6/4/2026")}
    assert not jobs[0].invoiced and "check Excerpts… for 6/3/2026" in jobs[0].error and jobs[1].invoiced


def test_the_same_attorney_entered_twice_is_one_party(tmp_path, s):
    """Two entries with the same Attorney.key() are one party with one invoice; a blank row being typed in is
    no party at all."""
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    twice = Attorney(name="Alex B. Counsel.", firm="Counsel & Counsel", checked=True)
    job.case.attorneys = [alex(), twice, dana(), Attorney(checked=True)]
    assert twice.key() == A and job.ticked_keys() == [A, B]
    opts = job.invoice_opts()
    assert opts.parties == 2 and opts.orders[0].portions == [Portion(30, [A, B], 2)]
    firms = firm_invoices(job.case, s, opts)
    assert [f.atty.name for f in firms] == ["Alex B. Counsel", "Dana Smith"]
    assert firms[0].quotes[0].per_party == quote(30, s.sheet().find("Regular"), 2).per_party  # 124.50
    assert invoice_count(job.case, opts) == 2 and files_to_make([job], ["invoice"], s) == 2


def test_a_stopped_run_does_not_bill_an_attorney_twice(tmp_path, s, monkeypatch):
    """Dana's invoice can't be saved (the PDF is open): Alex's was made. Generate all again makes Dana's only,
    and then the days are invoiced. Days on one invoice and days billed alone alike."""
    from minute_filler import deliver
    real = deliver.make_invoice
    calls = []

    def flaky(case, atty, *a, **k):
        calls.append(atty.name)
        if atty.name == "Dana Smith" and calls.count("Dana Smith") == 1:
            raise PermissionError("the PDF is open in another program")
        return real(case, atty, *a, **k)

    monkeypatch.setattr(deliver, "make_invoice", flaky)

    def billed() -> list[str]:
        """Who the invoices made since the round began are for."""
        return sorted(i.bill_to for i in ledger_for(s).invoices() if i.invoice_no not in before)

    for joint in (True, False):
        s.invoice_joint = joint
        before = {i.invoice_no for i in ledger_for(s).invoices()}
        calls.clear()
        (tmp_path / str(joint)).mkdir()
        jobs = read_days(tmp_path / str(joint), s, {"June 3, 2026": 30, "June 4, 2026": 60}) if joint else \
            read_days(tmp_path / str(joint), s, {"June 3, 2026": 30})
        for j in jobs:
            j.case.attorneys = [alex(), dana()]
        fill_jobs(jobs, s, outputs=["invoice"])
        assert billed() == ["Alex B. Counsel"]
        assert all(not j.invoiced and j.invoiced_keys == [A] and "open in another program" in j.error for j in jobs)
        assert files_to_make(jobs, ["invoice"], s) == 1  # Dana's only
        fill_jobs(jobs, s, outputs=["invoice"])
        assert billed() == ["Alex B. Counsel", "Dana Smith"]
        assert all(j.invoiced and not j.error for j in jobs)
        jobs[0].unbill()  # a new document: billable again, for everyone
        assert not jobs[0].invoiced and not jobs[0].invoiced_keys


def test_shared_pages_show_without_trailing_zeros_and_undated_days_keep_the_cases_dates(s):
    sp = s.sheet().find("Regular")
    q = quote_shares([Share(40, 1), Share(25, 2)], sp)
    assert str(q.lines[0].pages) == "52.5" and str(q.lines[1].pages) == "65"
    case = make_case(dict(ROE, dates="6/3/2026, 6/4/2026"), [alex(), dana()])
    opts = InvoiceOpts(90, days=[("6/3/2026", 30), ("", 60)],
                       orders=[DayOrder("6/3/2026", 30, [Portion(30, [A], 1)]), DayOrder("", 60, [Portion(60, [B], 1)])])
    firms = {f.atty.name: f.case.get("dates") for f in firm_invoices(case, s, opts)}
    assert firms == {"Alex B. Counsel": "6/3/2026", "Dana Smith": "6/3/2026, 6/4/2026"}


def test_a_parties_number_below_the_firms_ticked_bills_no_more_than_their_share(tmp_path, s):
    """Bug (1.8.0): Parties 1 with two attorneys ticked billed each of them the whole original and index."""
    job, = read_days(tmp_path, s, {"June 3, 2026": 30})
    job.case.attorneys = [alex(), dana()]
    even = {f.atty.name: f.quotes[0].per_party for f in firm_invoices(job.case, s, job.invoice_opts())}
    job.parties = 1
    clamped = {f.atty.name: f.quotes[0].per_party for f in firm_invoices(job.case, s, job.invoice_opts())}
    # 30 x 4.30 / 2 + 30 + 30 = 124.50 each, as with the number left alone
    assert clamped == even == {"Alex B. Counsel": Decimal("124.50"), "Dana Smith": Decimal("124.50")}
