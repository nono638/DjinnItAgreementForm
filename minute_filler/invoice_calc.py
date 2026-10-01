"""Invoice pricing, the same arithmetic as the reporter's invoice spreadsheet.

For one speed, with N ordering parties:

    original          pages x Original rate                 (one original, shared)
    copies            pages x Copy rate  x N                (one copy per party)
    e-mailed copies   pages x Email rate x N                (optional)
    indexes           pages x Index rate x N                (optional, from the threshold, e.g. 50 pages)
    judge's index     pages x Index rate                    (with the indexes)

    total = the sum;  each party pays total / N

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
    label: str
    rate: Decimal
    qty: int        # how many (copies, parties)
    amount: Decimal


@dataclass
class Quote:
    speed: str                  # the sheet's name for it, e.g. "Expedite"
    pages: int
    parties: int
    lines: list[QuoteLine] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((l.amount for l in self.lines), Decimal("0.00"))

    @property
    def per_party(self) -> Decimal:
        return (self.total / max(1, self.parties)).quantize(CENT, ROUND_HALF_UP)

    @property
    def per_page(self) -> Decimal:
        """What each party pays per page."""
        return (self.per_party / max(1, self.pages)).quantize(CENT, ROUND_HALF_UP)


def quote(pages: int, sp: Speed, parties: int = 1, include_email: bool = True,
          include_index: bool = True, threshold: int = 50) -> Quote:
    """Prices one speed. See the module docstring for the arithmetic."""
    parties = max(1, parties)
    q = Quote(sp.name, pages, parties)

    def line(label: str, rate: Decimal, qty: int) -> None:
        if rate and qty:
            q.lines.append(QuoteLine(label, rate, qty, (rate * pages * qty).quantize(CENT, ROUND_HALF_UP)))

    line("Original", money(sp.original), 1)
    line("Copy", money(sp.copy), parties)
    if include_email:
        line("E-mailed copy", extra_rate(sp, "email", "e-mail"), parties)
    if include_index and pages >= threshold:
        idx = extra_rate(sp, "index")
        line("Index", idx, parties)
        line("Judge's index", idx, 1)
    return q


def offered(sheet: RateSheet, speeds: list[str], chosen: str, choice: bool) -> list[Speed]:
    """The sheet's speeds an invoice lists: every one in `speeds` (choice invoice), else just `chosen`.
    Slowest (cheapest) first."""
    wanted = speeds if choice else [chosen]
    keys = {speed_key(w) for w in wanted if w}
    found = [sp for sp in sheet.speeds if sp.key in keys]
    if not found and chosen:  # e.g. the job's speed isn't on the sheet's list: fall back to it
        sp = sheet.find(chosen)
        found = [sp] if sp else []
    return found or sheet.speeds[:1]


def quotes_for(pages: int, parties: int, sheet: RateSheet, s, chosen: str, choice: bool | None = None) -> list[Quote]:
    """One Quote per speed the invoice offers, using the invoice settings in `s`."""
    choice = s.invoice_choice if choice is None else choice
    return [quote(pages, sp, parties, s.invoice_include_email, s.invoice_include_index, s.invoice_index_threshold)
            for sp in offered(sheet, s.invoice_speeds, chosen, choice)]
