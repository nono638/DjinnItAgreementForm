"""The invoice arithmetic at its edges: an index from exactly the threshold, the "total" rule, the index decided by
the whole transcript (every reporter's pages) and charged on the user's own, and why in a few words, a firm's
share of one page, of no pages and of stretches ordered alone and with others (the parts the small print adds up),
no $0 lines, no "÷ 1 firm", and Parties 0. Most tests catch changes to invoice_calc.py that a mutation run
(tools/mutate.py) showed no other test would. Prices are from the bundled "Sample Rates" sheet (Regular: original
$4.30, copy, e-mailed copy and index $1.00 a page); names are made up."""
from decimal import Decimal

import pytest

from minute_filler.invoice import DayOrder, FirmInvoice, InvoiceOpts, Portion, firm_invoices, index_reason
from minute_filler.invoice_calc import Share, index_days, quote, quote_shares
from minute_filler.invoice_math import explain
from minute_filler.models import Attorney
from minute_filler.rates import Speed

from helpers import make_case, pat_settings


@pytest.fixture
def sp():
    """The Sample Rates sheet's Regular speed."""
    return pat_settings().sheet().find("Regular")


def labels(q):
    return [l.label for l in q.lines]


def line(q, label):
    return next(l for l in q.lines if l.label == label)


def math_of(q, pages):
    """A firm's share spelled out as the plain text of The math (what Copy all copies)."""
    f = FirmInvoice(Attorney(name="Dana Smith"), make_case({}), InvoiceOpts(pages, q.parties), [q])
    return explain([(f, "2026-0001")])[1]


# ------------------------------------------------------------------ the index
def test_an_index_from_exactly_the_threshold(sp):
    """50 pages (the default threshold) is enough for an index, 49 is not: by any day, by each day, in a quote."""
    assert index_days([50]) == [True] and index_days([49]) == [False]
    assert index_days([30, 50]) == [True, True]  # "any": every day once one of them has 50
    assert index_days([50, 49], rule="each") == [True, False]
    assert "Index" in labels(quote(50, sp)) and "Index" not in labels(quote(49, sp))


def test_the_total_rule_counts_the_days_together():
    """Rule "total": every day gets an index once the days come to 50 pages together (20 + 30), none below."""
    assert index_days([20, 30], rule="total") == [True, True]
    assert index_days([20, 29], rule="total") == [False, False]


def test_the_whole_transcript_decides_the_index_charged_on_your_pages(sp):
    """The user wrote 45 of an 83-page transcript (another reporter the rest): the 83 pages decide that there is
    an index (50 or more), and the user's invoice charges it, and the judge's, on the user's own 45 pages. 40 of 45
    gets none; Peripherals... can say no to 83 pages, or yes to 12; a Pages number typed with no transcript
    (total 0) is judged on its own pages. (The user, 2026-10-08.)"""
    s = pat_settings()
    s.invoice_speeds = ["Regular"]
    case = make_case({"dates": "6/3/2026", "delivery": "Regular"}, [Attorney(name="Dana Smith", checked=True)])

    def firm(pages, total, index=None):
        opts = InvoiceOpts(pages, 1, days=[("6/3/2026", pages)], index=index,
                           orders=[DayOrder("6/3/2026", pages, [Portion(pages, [])], total=total)])
        return firm_invoices(case, s, opts)[0]

    def indexes(f):
        return {l.label: l.pages for l in f.quotes[0].lines if l.label in ("Index", "Judge's index")}

    f = firm(45, 83)
    assert indexes(f) == {"Index": 45, "Judge's index": 45}
    assert f.index_note == "Index: 83 total pages, 50 or more."
    f = firm(40, 45)
    assert indexes(f) == {} and f.index_note == "No index: 45 total pages, under 50."
    f = firm(45, 83, "off")
    assert indexes(f) == {} and f.index_note == "No index: turned off for this job."
    f = firm(10, 12, "on")
    assert indexes(f) == {"Index": 10, "Judge's index": 10} and f.index_note == "Index: turned on for this job."
    assert indexes(firm(60, 0)) == {"Index": 60, "Judge's index": 60}
    assert indexes(firm(49, 0)) == {}
    # the same in a quote of a whole invoice: judged on 83, charged on 45; without `judged`, on the 45
    assert line(quote(45, sp, judged=[83]), "Index").pages == 45 and "Index" not in labels(quote(45, sp))
    assert line(quote([30, 20], sp, judged=[60, 20]), "Index").pages == 50  # (rule "any": both days)


def test_why_there_is_an_index_or_not_in_a_few_words():
    """index_reason, for the Invoice panel and the math, by the rule for several days."""
    s = pat_settings()

    def why(days, rule="any", index=None, include=True):
        s.invoice_index_rule, s.invoice_include_index = rule, include
        opts = InvoiceOpts(sum(days), days=[(f"6/{i + 3}/2026", n) for i, n in enumerate(days)], index=index,
                           orders=[DayOrder(f"6/{i + 3}/2026", n, total=n) for i, n in enumerate(days)])
        return index_reason(opts, s)

    assert why([83]) == "83 total pages, 50 or more" and why([1200]) == "1,200 total pages, 50 or more"
    assert why([49]) == "49 total pages, under 50"
    assert why([30, 60]) == "a day of 60 pages, 50 or more" and why([30, 40]) == "no day has 50 pages"
    assert why([30, 60], "each") == "1 of 2 days have 50 pages or more"
    assert why([50, 60], "each") == "every day has 50 pages or more" and why([30, 40], "each") == "no day has 50 pages"
    assert why([20, 30], "total") == "50 total pages over 2 days, 50 or more"
    assert why([20, 29], "total") == "49 total pages over 2 days, under 50"
    assert why([10], index="on") == "turned on for this job" and why([90], index="off") == "turned off for this job"
    assert why([90], include=False) == "turned off in Settings → Invoice"


# ------------------------------------------------------------------ a firm's own share
def test_the_small_print_adds_up_the_pages_of_each_kind(sp):
    """Two stretches ordered alone (40 and 30 pages) and one shared by two firms (50): whatever their order, the
    lines' parts are 70 pages alone and 50 shared, and the math says so."""
    for shares in ([Share(40), Share(30), Share(50, 2)], [Share(50, 2), Share(40), Share(30)]):
        q = quote_shares(shares, sp)
        assert line(q, "Original").parts == [(70, 1), (50, 2)]
        assert line(q, "Copy").parts == [(70, 1), (50, 2)]
    plain = math_of(q, 120)
    assert "70 pages ordered by this firm alone: 70 × $4.30 = $301.00\n" in plain
    assert "50 pages ordered by 2 firms: 50 × $4.30 = $215.00 ÷ 2 = $107.50\n" in plain


def test_a_firms_share_has_no_line_for_a_charge_the_sheet_does_not_have():
    """A speed with no E-mail or Index column: a firm's share has no $0.00 lines for them."""
    q = quote_shares([Share(60, 2, True)], Speed("Regular", "4.30", "1.00"))
    assert labels(q) == ["Original", "Copy"]
    assert q.per_party == Decimal("189.00")  # 60 x 4.30 / 2 + 60 x 1.00


def test_a_firm_that_ordered_alone_divides_by_nobody(sp):
    """40 pages one firm ordered alone: none of its lines is split, and the math has no "÷ 1 firm"."""
    q = quote_shares([Share(40, 1)], sp)
    assert not any(l.shared for l in q.lines)
    plain = math_of(q, 40)
    assert "Original: 40 pages × $4.30 = $172.00\n" in plain and "÷" not in plain


def test_a_one_page_stretch_is_billed(sp):
    """One page ordered alone costs $4.30 + $1.00 + $1.00, on its own or beside a shared stretch; a quote of one
    page costs its whole price a page."""
    assert quote_shares([Share(1)], sp).per_party == Decimal("6.30")
    q = quote_shares([Share(1), Share(10, 2)], sp)
    assert line(q, "Original").parts == [(1, 1), (10, 2)] and q.pages == 11
    one = quote(1, sp)
    assert one.per_page == one.per_party == Decimal("6.30")


def test_a_stretch_of_no_pages_is_left_out(sp):
    """A stretch of 0 pages (shared by 3), or of fewer (a portion that ends before it starts), changes nothing:
    still one party, and its parts don't show."""
    q = quote_shares([Share(40), Share(0, 3), Share(-5, 2)], sp)
    assert q.parties == 1 and line(q, "Original").parts == [(40, 1)]
    assert q.per_party == quote_shares([Share(40)], sp).per_party


def test_a_stretch_ordered_by_no_firm_counts_as_ordered_alone(sp):
    """n = 0 doesn't come from the app (excerpts counts the firm itself), but it is read as 1: 30 pages and 40
    pages alone are 70 pages alone, not split."""
    q = quote_shares([Share(30), Share(40, 0)], sp)
    assert q.per_party == quote_shares([Share(70)], sp).per_party
    assert line(q, "Original").parts == [(70, 1)] and not line(q, "Original").shared


def test_a_share_is_ordered_alone_unless_told_otherwise(sp):
    """Share(40) is 40 pages one firm ordered alone: the price of the whole invoice of 40 pages for one party.
    No stretches at all: nothing to pay, one party."""
    q = quote_shares([Share(40)], sp)
    assert q.parties == 1 and q.per_party == quote(40, sp).per_party == Decimal("252.00")
    nothing = quote_shares([], sp)
    assert (nothing.parties, nothing.lines, nothing.per_party) == (1, [], Decimal("0.00"))


# ------------------------------------------------------------------ the whole invoice
def test_parties_0_is_one_party(sp):
    """Parties 0 (none typed) prices one copy, as the default does; no line of a whole invoice is a firm's split
    share."""
    one = quote(10, sp, 1)
    for q in (quote(10, sp, 0), quote(10, sp)):
        assert q.parties == 1 and line(q, "Copy").qty == 1 and q.per_party == one.per_party == Decimal("63.00")
    assert not any(l.shared for l in quote(60, sp, 3).lines)


def test_a_quote_of_no_pages_costs_nothing_a_page(sp):
    """No pages: no lines and $0.00 a page (not a division by zero)."""
    q = quote(0, sp)
    assert (q.lines, q.total, q.per_page) == ([], Decimal("0.00"), Decimal("0.00"))
