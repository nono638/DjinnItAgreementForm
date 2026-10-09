"""Invoice pricing, the same arithmetic as the reporter's invoice spreadsheet.

For one speed, with N ordering parties (quote):

    original          pages x Original rate                 (one original, shared)
    copies            pages x Copy rate  x N                (one copy per party)
    e-mailed copies   pages x Email rate x N                (optional)
    index             pages x Index rate x N                (optional, from the threshold, e.g. 50 pages; one per
                                                             party, as Settings.invoice_index_shared "each" says,
                                                             else one, shared: "split")
    judge's index     pages x Index rate                    (with the index; one, shared)

    total = the sum;  each party pays total / N, rounded up to the cent

An invoice can cover several days (a transcript each). Original and copies are charged on all their pages; the
index and the judge's index on the pages of the days that get one (see index_days: by default, all of them once
any day reaches the threshold). Whether a day gets one is judged on every page of its transcripts, whoever
wrote them (quote's `judged`): the user's 45 pages of an 83-page transcript get an index, charged on the 45.

When the parties did not all order the same pages (a day ordered by one firm, another by two; see
invoice.DayOrder), each firm gets its own invoice priced by quote_shares: for each stretch of pages it ordered
with n firms in all, it pays the original / n, its own copy and e-mailed copy in full, its own index in full
("each", the reporters' practice) or / n ("split"), and the judge's index / n. Its total is rounded up to the
cent. When every firm orders every page, that is the same as quote's per-party amount. (100 pages, A orders
them all and B 10 of them: A pays an index of 100 pages and 95 of the judge's, B an index of 10 and 5 of the
judge's.)

The speeds priced are those of Settings.invoice_speeds (see offered). A job's Peripherals... can turn the
e-mailed copies and the index on or off for its own invoice (quotes_for); otherwise the invoice settings decide.

Money is kept in Decimal and rounded to cents (a firm's share is worked out exactly first, as a fraction).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from fractions import Fraction

from .rates import RateSheet, Speed, parse_money, speed_key

CENT = Decimal("0.01")


def money(v) -> Decimal:
    """'$4.30' / 4.3 / '' -> Decimal('4.30') (0 when blank or unreadable)."""
    d = parse_money(v)
    return Decimal("0.00") if d is None else d


def fmt(d: Decimal) -> str:
    """Decimal('1234.5') -> '$1,234.50'."""
    return f"${d.quantize(CENT, ROUND_HALF_UP):,.2f}"


def extra_rate(sp: Speed, *names: str) -> Decimal:
    """A rate from one of the sheet's other columns ('Email', 'Index'); blank -> 0."""
    for k, v in sp.extras.items():
        if k.strip().lower() in names:
            return money(v)
    return Decimal("0.00")


@dataclass
class QuoteLine:
    """One line of a Quote: 'Copy', its rate per page, how many, the pages charged and the amount
    (rate x pages x qty, to the cent; on a firm's share, exact and possibly part of a cent, with qty 1)."""
    label: str
    rate: Decimal
    qty: int        # how many (copies, parties)
    amount: Decimal
    pages: int | Decimal = 0  # the pages charged (an index covers only the days that get one); on a firm's
    #                           share, pages split between n firms count 1/n each, so this may be 32.5 (to the
    #                           cent, without trailing zeros)
    shared: bool = False  # a firm's share: some of these pages are split with other firms
    # a firm's share: the pages it ordered, by how many firms ordered them ((pages, n), n = 1 for those it
    # ordered alone), for the math spelled out; the amount is split n ways when `shared`
    parts: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Quote:
    """The price of one speed for one job, line by line. A firm's own share (quote_shares) has `due`, the
    exact amount it owes, and its per_party is that rounded up."""
    speed: str                  # the sheet's name for it, e.g. "Expedite"
    pages: int                  # all the days together
    parties: int                # on a firm's share: the most firms that shared any of its pages
    lines: list[QuoteLine] = field(default_factory=list)
    days: list[int] = field(default_factory=list)  # the pages of each day
    due: Fraction | None = None  # a firm's share, exactly (None: the whole price, split by parties)

    @property
    def share(self) -> bool:
        """This is one firm's own share of the pages it ordered (see quote_shares)."""
        return self.due is not None

    @property
    def total(self) -> Decimal:
        """The whole price of this speed, all parties together; on a firm's share, what that firm owes
        (before it is rounded up to the cent)."""
        if self.due is not None:
            return _decimal(self.due)
        return sum((l.amount for l in self.lines), Decimal("0.00"))

    @property
    def per_party(self) -> Decimal:
        """What each party pays: the total split evenly, rounded up to the cent so the shares cover the
        total ($319.30 three ways -> $106.44 each). On a firm's share: its own amount, rounded up."""
        if self.due is not None:
            return (Decimal(math.ceil(self.due * 100)) / 100).quantize(CENT)
        return (self.total / max(1, self.parties)).quantize(CENT, ROUND_CEILING)

    @property
    def per_page(self) -> Decimal:
        """What each party pays a page, to the cent: per_party over the pages billed, so the copies and the
        index are in it too."""
        return (self.per_party / max(1, self.pages)).quantize(CENT, ROUND_HALF_UP)


def index_days(days: list[int], mode: str = "auto", rule: str = "any", threshold: int = 50) -> list[bool]:
    """Which days get an index. mode: "on" (every day), "off" (none) or "auto", where the rule decides:
    "any" - every day once any day has `threshold` pages; "each" - the days that have them;
    "total" - every day once the days together have them. [30, 60] -> any: both; each: the second only."""
    if mode == "on":
        return [True] * len(days)
    if mode == "off" or not days:
        return [False] * len(days)
    if rule == "each":
        return [p >= threshold for p in days]
    if rule == "total":
        return [sum(days) >= threshold] * len(days)
    return [any(p >= threshold for p in days)] * len(days)


def quote(days: int | list[int], sp: Speed, parties: int = 1, include_email: bool = True,
          index: str | bool = "auto", rule: str = "any", threshold: int = 50, index_shared: str = "each",
          judged: list[int] | None = None) -> Quote:
    """Prices one speed for the pages of one day (an int) or of each day (a list). index: "auto" (see
    index_days), "on", "off"; True/False are "auto"/"off". judged: the pages each day's index is judged on,
    every page of its transcripts whoever wrote them (default: the days' own pages; the index is still charged
    on the days' own pages). index_shared: "each" (an index for each party) or "split" (one index, its price
    split between the parties like the original), as Settings.invoice_index_shared says. See the module
    docstring for the arithmetic."""
    days = [days] if isinstance(days, int) else list(days)
    index = {True: "auto", False: "off"}.get(index, index)
    pages = sum(days)
    parties = max(1, parties)
    q = Quote(sp.name, pages, parties, days=days)

    def line(label: str, rate: Decimal, qty: int, count: int = pages) -> None:
        """Adds a line charging `count` pages; none when it would be $0 (no such column on the sheet)."""
        if rate and qty and count:
            q.lines.append(QuoteLine(label, rate, qty, (rate * count * qty).quantize(CENT, ROUND_HALF_UP), count))

    line("Original", money(sp.original), 1)
    line("Copy", money(sp.copy), parties)
    if include_email:
        line("E-mailed copy", extra_rate(sp, "email", "e-mail"), parties)
    on = index_days(days if judged is None else judged, index, rule, threshold)
    indexed = sum(p for p, x in zip(days, on) if x)
    if indexed:
        idx = extra_rate(sp, "index")
        line("Index", idx, parties if index_shared == "each" else 1, indexed)
        line("Judge's index", idx, 1, indexed)
    return q


@dataclass(frozen=True)
class Share:
    """A stretch of pages one firm ordered: how many, how many firms ordered them together (n), and whether
    its day gets an index (see index_days, judged on every page of the day's transcripts: invoice.index_pages)."""
    pages: int
    n: int = 1
    indexed: bool = False


def _decimal(f: Fraction) -> Decimal:
    """An exact fraction as a Decimal (to Decimal's 28 digits): 65/2 -> 32.5."""
    return Decimal(f.numerator) / Decimal(f.denominator)


def quote_shares(shares: list[Share], sp: Speed, include_email: bool = True, index_split: bool = False,
                 days: list[int] | None = None) -> Quote:
    """One firm's price for one speed, from the stretches of pages it ordered (see the module docstring):
    the original / n, its own copy and e-mailed copy, its own index whole (or / n: index_split), the judge's
    index / n. The lines show the pages charged, those shared counting 1/n each (the original of 40 pages alone and
    50 shared by two: 65 pages), and their parts, the pages by how many firms ordered them ([(40, 1), (50, 2)]).
    The amount is worked out exactly (Quote.due) and rounded up to the cent (Quote.per_party). days: the pages
    the firm ordered on each day, for the invoice."""
    shares = [x for x in shares if x.pages > 0]
    pages = sum(x.pages for x in shares)
    q = Quote(sp.name, pages, max([x.n for x in shares] or [1]), days=list(days or [pages]), due=Fraction(0))

    def line(label: str, rate: Decimal, part: list[Share], split: bool) -> None:
        """Adds a line for these stretches (split: each counts pages / n); none when it would be $0."""
        count = sum((Fraction(x.pages, max(1, x.n) if split else 1) for x in part), Fraction(0))
        if rate and count:
            amount = Fraction(rate) * count
            q.due += amount
            # to the cent, without trailing zeros: 32.5 pages, not 32.50 (and 40, not 40.00 or 4E+1)
            shown = Decimal(format(_decimal(count).quantize(CENT, ROUND_HALF_UP).normalize(), "f"))
            by_n: dict[int, int] = {}
            for x in part:
                by_n[max(1, x.n)] = by_n.get(max(1, x.n), 0) + x.pages
            q.lines.append(QuoteLine(label, rate, 1, _decimal(amount), shown,
                                     split and any(x.n > 1 for x in part),
                                     [(p, n) for n, p in sorted(by_n.items())]))

    line("Original", money(sp.original), shares, True)
    line("Copy", money(sp.copy), shares, False)
    if include_email:
        line("E-mailed copy", extra_rate(sp, "email", "e-mail"), shares, False)
    indexed = [x for x in shares if x.indexed]
    if indexed:
        idx = extra_rate(sp, "index")
        line("Index", idx, indexed, index_split)
        line("Judge's index", idx, indexed, True)
    return q


def offered(sheet: RateSheet, speeds: list[str], chosen: str) -> list[Speed]:
    """The sheet's speeds an invoice lists: every one in `speeds` (the speeds ticked; one alone makes a
    single-speed invoice). When none of them is on the sheet, the job's own speed `chosen`, else the sheet's
    first. Slowest (cheapest) first, as the sheet is sorted."""
    keys = {speed_key(w) for w in speeds if w}
    found = [sp for sp in sheet.speeds if sp.key in keys]
    if not found and chosen:  # none of them is on the sheet: the job's own speed, matched by name too
        sp = sheet.find(chosen)
        found = [sp] if sp else []
    return found or sheet.speeds[:1]


def quotes_for(days: int | list[int], parties: int, sheet: RateSheet, s, chosen: str,
               email: bool | None = None, index: str | None = None, judged: list[int] | None = None) -> list[Quote]:
    """One Quote per speed the invoice offers (Settings.invoice_speeds; `chosen`, the job's own speed, when
    none of them is on the sheet), for the pages of one day or of each day. The job's own choices: email
    (True/False: an e-mailed copy) and index ("auto", "on" or "off"); None = as the invoice settings in `s`
    say. judged: the pages each day's index is judged on (see quote)."""
    email = s.invoice_include_email if email is None else email
    if index is None:
        index = "auto" if s.invoice_include_index else "off"
    return [quote(days, sp, parties, email, index, s.invoice_index_rule, s.invoice_index_threshold,
                  s.invoice_index_shared, judged) for sp in offered(sheet, s.invoice_speeds, chosen)]
