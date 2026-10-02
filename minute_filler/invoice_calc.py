"""Invoice pricing, the same arithmetic as the reporter's invoice spreadsheet.

For one speed, with N ordering parties:

    original          pages x Original rate                 (one original, shared)
    copies            pages x Copy rate  x N                (one copy per party)
    e-mailed copies   pages x Email rate x N                (optional)
    indexes           pages x Index rate x N                (optional, from the threshold, e.g. 50 pages)
    judge's index     pages x Index rate                    (with the indexes)

    total = the sum;  each party pays total / N

An invoice can cover several days (a transcript each). Original and copies are charged on all their pages; the
indexes on the pages of the days that get one (see index_days: by default, all of them once any day reaches
the threshold).

The speeds priced are those of Settings.invoice_speeds (see offered). A job's Extras... can turn the e-mailed
copies and the index on or off for its own invoice (quotes_for); otherwise the invoice settings decide.

Money is kept in Decimal and rounded to cents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

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
    (rate x pages x qty)."""
    label: str
    rate: Decimal
    qty: int        # how many (copies, parties)
    amount: Decimal
    pages: int = 0  # the pages charged (an index covers only the days that get one)


@dataclass
class Quote:
    """The price of one speed for one job, line by line."""
    speed: str                  # the sheet's name for it, e.g. "Expedite"
    pages: int                  # all the days together
    parties: int
    lines: list[QuoteLine] = field(default_factory=list)
    days: list[int] = field(default_factory=list)  # the pages of each day

    @property
    def total(self) -> Decimal:
        """The whole price of this speed, all parties together."""
        return sum((l.amount for l in self.lines), Decimal("0.00"))

    @property
    def per_party(self) -> Decimal:
        """What each party pays: the total split evenly, to the cent."""
        return (self.total / max(1, self.parties)).quantize(CENT, ROUND_HALF_UP)

    @property
    def per_page(self) -> Decimal:
        """What each party pays per page."""
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
          index: str | bool = "auto", rule: str = "any", threshold: int = 50) -> Quote:
    """Prices one speed for the pages of one day (an int) or of each day (a list). index: "auto" (see
    index_days), "on", "off"; True/False are "auto"/"off". See the module docstring for the arithmetic."""
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
    indexed = sum(p for p, on in zip(days, index_days(days, index, rule, threshold)) if on)
    if indexed:
        idx = extra_rate(sp, "index")
        line("Index", idx, parties, indexed)
        line("Judge's index", idx, 1, indexed)
    return q


def offered(sheet: RateSheet, speeds: list[str], chosen: str) -> list[Speed]:
    """The sheet's speeds an invoice lists: every one in `speeds` (the speeds ticked; one alone makes a
    single-speed invoice). When none of them is on the sheet, the job's own speed `chosen`. Slowest
    (cheapest) first."""
    keys = {speed_key(w) for w in speeds if w}
    found = [sp for sp in sheet.speeds if sp.key in keys]
    if not found and chosen:  # none of them is on the sheet: the job's own speed, matched by name too
        sp = sheet.find(chosen)
        found = [sp] if sp else []
    return found or sheet.speeds[:1]


def quotes_for(days: int | list[int], parties: int, sheet: RateSheet, s, chosen: str,
               email: bool | None = None, index: str | None = None) -> list[Quote]:
    """One Quote per speed the invoice offers (Settings.invoice_speeds; `chosen`, the job's own speed, when
    none of them is on the sheet), for the pages of one day or of each day. The job's own choices: email
    (True/False: an e-mailed copy) and index ("auto", "on" or "off"); None = as the invoice settings in `s`
    say."""
    email = s.invoice_include_email if email is None else email
    if index is None:
        index = "auto" if s.invoice_include_index else "off"
    return [quote(days, sp, parties, email, index, s.invoice_index_rule, s.invoice_index_threshold)
            for sp in offered(sheet, s.invoice_speeds, chosen)]
