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

Such an invoice of several speeds, the firm to choose one, is for a firm billed alone. Firms that ordered the
same pages (a split order) commit to a speed each, as the courthouse says (courthouses.speed_upfront), and are
billed that speed alone (quote_ordered): one original of those pages is billed, at the fastest speed ordered on
them, and divided between the firms as the courthouse's split rule says (Queens: each slower firm pays its share
as if every firm had ordered its speed, the fastest pay the rest; courthouses/queens_supreme_civil/billing.py),
while each pays its own copy, e-mailed copy and index at its own speed. With every firm at one speed that is
quote_shares' price.

Money is kept in Decimal and rounded to cents (a firm's share is worked out exactly first, as a fraction).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from fractions import Fraction

from .courthouses.base import Party
from .rates import RateSheet, Speed, parse_money, speed_key

CENT = Decimal("0.01")


def money(v) -> Decimal:
    """'$4.30' / 4.3 / '' -> Decimal('4.30') (0 when blank or unreadable)."""
    d = parse_money(v)
    return Decimal("0.00") if d is None else d


def fmt(d: Decimal) -> str:
    """Decimal('1234.5') -> '$1,234.50'."""
    return f"${d.quantize(CENT, ROUND_HALF_UP):,.2f}"


def exact_number(d: Decimal, places: int, least: int = 0) -> str:
    """A number as worked out, not rounded away: at least `least` decimals, at most `places`, then "…" when
    there is more (8.175 -> '8.175'; a third of 4.30 -> '1.4333…'; 1000 with least=2 -> '1,000.00')."""
    shown = d.quantize(Decimal(1).scaleb(-places))
    more = "…" if shown != d else ""
    text = f"{shown.normalize():,f}"
    whole, _, frac = text.partition(".")
    frac = frac.ljust(least, "0")
    return (f"{whole}.{frac}" if frac else whole) + more


def money_exact(d: Decimal | Fraction) -> str:
    """'$8.18' for whole cents; a firm's share of split pages can come to part of a cent, shown as it is
    ('$8.175', '$1.4333…'), so the lines add up to the total shown under them. Takes an exact Fraction too."""
    if isinstance(d, Fraction):
        d = _decimal(d)
    return fmt(d) if d == d.quantize(CENT) else "$" + exact_number(d, 4, 2)


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
    # a firm's ordered quote (quote_ordered): its pages as priced, stretch by stretch (see Stretch); the math
    # spells these out, and those of pages ordered at different speeds say how the firm's part was reached
    stretches: list[Stretch] = field(default_factory=list)

    @property
    def charge(self) -> str:
        """What is charged, without the speed an ordered quote of two speeds names: 'Original (Daily)' ->
        'Original'."""
        return self.label.split(" (")[0]

    @property
    def mixed(self) -> bool:
        """Some of its pages were billed once for firms that ordered them at different speeds."""
        return any(x.mixed for x in self.stretches)


@dataclass(frozen=True)
class Stretch:
    """Pages of one line of an ordered quote (quote_ordered) priced alike: how many, how many firms ordered them
    (n), what this firm pays a page of the line's charge (exact) and the other firms on them, (name, speed) each.
    On a charge billed once for pages ordered at different speeds (`mixed`): the speed and price of a page it is
    billed at (the fastest ordered on them) and how this firm's part was reached (the courthouse's split rule:
    steps of words and amounts, see courthouses.base.Part)."""
    pages: int
    n: int
    per_page: Fraction
    others: tuple[tuple[str, str], ...] = ()
    billed_at: str = ""
    billed_rate: Fraction = Fraction(0)
    steps: tuple[tuple[str, Fraction], ...] = ()

    @property
    def mixed(self) -> bool:
        """A charge billed once, for pages these firms ordered at different speeds."""
        return bool(self.billed_at)


@dataclass
class Quote:
    """The price of one speed for one job, line by line. A firm's own share (quote_shares) has `due`, the
    exact amount it owes, and its per_party is that rounded up. An ordered quote (quote_ordered) is a firm's
    share too, of the speeds it ordered (`ordered`); its `speed` names them ("Daily", "Daily + Regular")."""
    speed: str                  # the sheet's name for it, e.g. "Expedite"
    pages: int                  # all the days together
    parties: int                # on a firm's share: the most firms that shared any of its pages
    lines: list[QuoteLine] = field(default_factory=list)
    days: list[int] = field(default_factory=list)  # the pages of each day
    due: Fraction | None = None  # a firm's share, exactly (None: the whole price, split by parties)
    ordered: tuple[str, ...] = ()  # an ordered quote: the speeds the firm committed to (the sheet's names)

    @property
    def share(self) -> bool:
        """This is one firm's own share of the pages it ordered (see quote_shares)."""
        return self.due is not None

    @property
    def mixed(self) -> bool:
        """Some of its pages were ordered together with a firm at another speed (an ordered quote)."""
        return any(x.mixed for l in self.lines for x in l.stretches)

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
    its day gets an index (see index_days, judged on every page of the day's transcripts: invoice.index_pages).
    For an ordered quote (quote_ordered): the speed the firm committed to for them (the sheet's name) and the
    other firms that ordered them with it, (name, speed) each; the parties a Parties number counts beyond these
    (n - 1 - len(others)) are taken to order this firm's speed."""
    pages: int
    n: int = 1
    indexed: bool = False
    speed: str = ""
    others: tuple[tuple[str, str], ...] = ()


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


def sheet_speed(sheet: RateSheet, name: str) -> Speed:
    """The sheet's speed of this name (matched as RateSheet.find does: "Expedited" finds "Expedite"). ValueError
    when the sheet has none such."""
    sp = sheet.find(name) if name else None
    if sp is None:
        raise ValueError(f"the rate sheet has no {name or 'speed'} prices" if name else "no speed was chosen")
    return sp


def quote_ordered(shares: list[Share], sheet: RateSheet, rule=None, include_email: bool = True,
                  index_split: bool = False, days: list[int] | None = None) -> Quote:
    """One firm's price for the speeds it committed to (each share's `speed`), the pages of each priced at its
    own speed: its copy, e-mailed copy and (index_split False) index in full, as quote_shares does. A charge
    billed once for the pages (the original, the judge's index, and the index too with index_split) is split
    between the n firms that ordered them: / n when they all ordered one speed, as quote_shares; at different
    speeds it is billed at the fastest of them (the highest original on the sheet) and divided as the
    courthouse's split rule says (rule(parties, me) -> courthouses.base.Part; see courthouses.split_rule).
    One speed gives the lines "Original", "Copy"...; two (a firm that ordered Daily on one day, Regular on
    another) "Original (Daily)", ... for each. The amount is worked out exactly (Quote.due) and rounded up to the
    cent (Quote.per_party). ValueError when a speed isn't on the sheet, or the speeds are mixed without a rule."""
    shares = [x for x in shares if x.pages > 0]
    if not shares or not all(x.speed for x in shares):
        raise ValueError("an ordered invoice needs the speed of every page it bills")
    rank = {id(sp): i for i, sp in enumerate(sheet.speeds)}  # cheapest first: the higher, the faster
    found: dict[str, Speed] = {}
    for name in [x.speed for x in shares] + [o for x in shares for _, o in x.others]:
        if name not in found:
            found[name] = sheet_speed(sheet, name)
    own = list(dict.fromkeys(found[x.speed].name for x in shares))
    pages = sum(x.pages for x in shares)
    q = Quote(" + ".join(own), pages, max(x.n for x in shares), days=list(days or [pages]), due=Fraction(0),
              ordered=tuple(own))

    def stretch(x: Share, price, once: bool) -> Stretch:
        """What this firm pays a page of one charge (price(speed) -> its rate) on the share's pages."""
        mine = found[x.speed]
        n = max(1, x.n)
        if not once:
            return Stretch(x.pages, n, Fraction(price(mine)), x.others)
        # the firms on these pages: this one, the others, and parties counted by a Parties number (its speed)
        theirs = [(name, found[o]) for name, o in x.others]
        theirs += [("another party", mine)] * max(0, n - 1 - len(theirs))
        if all(sp is mine for _, sp in theirs):
            return Stretch(x.pages, n, Fraction(price(mine)) / n, x.others)
        if rule is None:
            raise ValueError("pages ordered together at different speeds can't be billed: the courthouse has no "
                             "rule for dividing them")
        parties = [Party("This firm", mine.name, Fraction(price(mine)), rank[id(mine)])]
        parties += [Party(name, sp.name, Fraction(price(sp)), rank[id(sp)]) for name, sp in theirs]
        part = rule(parties, 0)
        top = max(parties, key=lambda p: p.rank)
        return Stretch(x.pages, len(parties), part.per_page, x.others, top.speed, top.rate, part.steps)

    def line(label: str, part: list[Share], price, once: bool) -> None:
        """Adds a line for these shares (once: a charge billed once for their pages, divided between the firms
        that ordered them); none when it comes to $0 (no such column on the sheet)."""
        got = [stretch(x, price, once) for x in part]
        amount = sum((x.per_page * x.pages for x in got), Fraction(0))
        if not amount:
            return
        q.due += amount
        count = sum((Fraction(x.pages, x.n) if once else Fraction(x.pages) for x in got), Fraction(0))
        shown = Decimal(format(_decimal(count).quantize(CENT, ROUND_HALF_UP).normalize(), "f"))
        by_n: dict[int, int] = {}
        for x in got:
            by_n[x.n] = by_n.get(x.n, 0) + x.pages
        q.lines.append(QuoteLine(label, price(found[part[0].speed]), 1, _decimal(amount), shown,
                                 once and any(x.n > 1 for x in got), [(p, n) for n, p in sorted(by_n.items())],
                                 got))

    for name in own:
        mine = [x for x in shares if found[x.speed].name == name]
        tag = f" ({name})" if len(own) > 1 else ""
        line("Original" + tag, mine, lambda sp: money(sp.original), True)
        line("Copy" + tag, mine, lambda sp: money(sp.copy), False)
        if include_email:
            line("E-mailed copy" + tag, mine, lambda sp: extra_rate(sp, "email", "e-mail"), False)
        indexed = [x for x in mine if x.indexed]
        if indexed:
            line("Index" + tag, indexed, lambda sp: extra_rate(sp, "index"), index_split)
            line("Judge's index" + tag, indexed, lambda sp: extra_rate(sp, "index"), True)
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
