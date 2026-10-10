"""How Queens Supreme Court, Civil Term bills pages that firms ordered together at different speeds (the
courthouse's split rule, Courthouse.split_rule).

The reporter bills ONE original of a page, however many firms ordered it, and a copy (and an e-mailed copy, and
an index) for each firm. When the firms ordered the page at different speeds, the one original is billed at the
fastest speed ordered on it, and its price is divided like this:

1. Each firm that did NOT order the fastest speed pays what it would have paid had every firm on the page
   ordered its speed: that speed's original ÷ the number of firms (never more than the fastest speed's own
   price ÷ the number of firms: a sheet can price an index lower at a faster speed).
2. The firms that ordered the fastest speed split what is left, equally.

So a slower firm is never charged for speed it didn't ask for, and the extra cost of the faster original falls
only on the firms that asked for it. The judge's index (billed once, like the original) is divided the same
way. Each firm's own copy, e-mailed copy and index are its own, at its own speed (invoice_calc.quote_ordered).
When every firm ordered the same speed, this is the usual split: each pays the original ÷ the number of firms.

Example, Sample Rates, 10 pages, no index: one firm orders Daily (original $6.50, copy and e-mailed copy $1.25
each), one Immediate (original $7.60, copy and e-mailed copy $1.45 each). The original is billed at Immediate,
$7.60 a page. The Daily firm pays $6.50 ÷ 2 = $3.25 a page of it, the Immediate firm the rest, $7.60 − $3.25 =
$4.35. With their own copies: Daily $5.75 a page, $57.50; Immediate $7.25 a page, $72.50; together $130.00, the
whole bill (one original at Immediate, $76.00, and each firm's own copies, $25.00 and $29.00).

The rule comes from the reporter's own workbook (research/Daily_Immediate_Rate_Calculator_v10.xlsx, git-ignored
with the questions still open about it, research/OPEN_QUESTIONS.md). It works for any number of speeds, but how
three or more speeds on the same pages should be billed isn't settled (the workbook marks it open: a "layered"
rule gives other answers from three speeds up), so the profile allows two for now (speeds_together).
"""
from __future__ import annotations

from fractions import Fraction

from ...invoice_calc import money_exact
from ..base import Part, Party


def _all(n: int) -> str:
    """'both firms', 'all 3 firms'."""
    return "both firms" if n == 2 else f"all {n} firms"


def split(parties: list[Party], me: int) -> Part:
    """What parties[me] pays a page of a charge billed once for pages these firms ordered (each Party's rate is
    that charge's price at its own speed), and how, in words for the math (see the module docstring)."""
    n = len(parties)
    top = max(p.rank for p in parties)
    billed = next(p for p in parties if p.rank == top)  # (the speed the charge is billed at, and its price)

    def rate(p: Party) -> Fraction:
        """The price a slower firm's share is worked out from: its own speed's, but never above the price the
        charge is billed at (a sheet can price the faster speed's index lower: the fastest firm paid less than
        nothing then)."""
        return min(p.rate, billed.rate)

    mine = parties[me]
    if mine.rank != top:  # a slower firm: its own speed's share
        pays = rate(mine) / n
        if mine.rate > billed.rate:
            how = (f"This firm ordered {mine.speed}, but this is billed at {billed.speed}, for less: "
                   f"{money_exact(billed.rate)} ÷ {n} = {money_exact(pays)} a page")
        else:
            how = (f"This firm ordered {mine.speed}, so it pays as if {_all(n)} had ordered {mine.speed}: "
                   f"{money_exact(mine.rate)} ÷ {n} = {money_exact(pays)} a page")
        return Part(pays, ((how, pays),))
    slower = [p for p in parties if p.rank != top]
    if not slower:  # (every firm at one speed: the usual split)
        pays = mine.rate / n
        return Part(pays, ((f"{money_exact(mine.rate)} ÷ {n} = {money_exact(pays)} a page", pays),))
    steps = []
    for p in slower:
        theirs = rate(p) / n
        steps.append((f"{p.name} ordered {p.speed} and pays {money_exact(rate(p))} ÷ {n} = "
                      f"{money_exact(theirs)} a page of it", theirs))
    less = " − ".join(money_exact(rate(p) / n) for p in slower)
    k = n - len(slower)  # the firms at the fastest speed, this one among them
    pays = (mine.rate - sum((rate(p) / n for p in slower), Fraction(0))) / k
    if k == 1:
        steps.append((f"This firm pays the rest: {money_exact(mine.rate)} − {less} = {money_exact(pays)} a page",
                      pays))
    else:
        steps.append((f"The {k} firms at {mine.speed} split the rest: ({money_exact(mine.rate)} − {less}) ÷ {k} = "
                      f"{money_exact(pays)} a page", pays))
    return Part(pays, tuple(steps))
