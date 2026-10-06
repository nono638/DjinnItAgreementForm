"""Who ordered which pages of a case, as one table (the Excerpts window, gui/excerpts.py).

Each day of the case (a job, see batch.invoice_groups) is cut into runs of pages, and each run says which firms
ordered it: the whole day by everyone ticked (the default), or excerpts - firm A the whole trial, firm B a
stretch of day 1, firm C part of B's stretch and another of day 2. A run is typed in the page numbers printed
on the transcript ("141-170"; its place in the day when they aren't known) and the runs around it make room
(carve). A run nobody ordered is billed to nobody. What is set is kept on each day as Job.portions (rows of
(last page, firms)) and the attorneys ticked on it, so the invoices (invoice.firm_invoices) bill it as before:
pages ordered by several firms are shared between them (the original, the index and the judge's index), each
firm pays its own copy.

prices() works out, as the table is changed, what each run costs each firm that ordered it, what each set of
firms ordering together pays for its pages ("A + B: 15 pp., each pays ..."), and each firm's invoice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from .batch import Job, joint_invoice
from .invoice import ORDERED_BY_NOBODY, firm_invoices
from .invoice_calc import Share, index_days, offered, quote_shares
from .models import Attorney
from .settings import Settings


@dataclass
class Day:
    """One day of the case: its job, its date, the pages Excerpts splits (Job.portion_pages) and the number
    printed on each of them (Job.printed_pages; [] or None where not known)."""
    job: Job
    label: str
    pages: int
    printed: list = field(default_factory=list)
    splittable: bool = True  # one day of pages (a job of several days is ordered whole)

    @property
    def numbered(self) -> bool:
        """The printed page numbers are known for every page and run upward, so runs are typed in them."""
        p = self.printed
        return len(p) == self.pages and None not in p and all(a < b for a, b in zip(p, p[1:]))

    def span(self, a: int, b: int) -> str:
        """Pages a..b of the day (places, from 1) as the table shows them: '358–377' in the printed numbers when
        they are known, else '1–20'."""
        if self.numbered:
            a, b = self.printed[a - 1], self.printed[b - 1]
        return f"{a}–{b}" if a != b else str(a)

    def place(self, a: int, b: int) -> str:
        """Pages a..b by their place in the day: '1–20 of 85'."""
        return f"{a}–{b} of {self.pages}" if a != b else f"{a} of {self.pages}"

    def parse(self, text: str) -> tuple[int, int]:
        """A run as typed ('141-170', '141 – 170', '141'), in the printed numbers when they are known, as
        (first, last) places in the day. ValueError, saying why, when it isn't one of its pages."""
        nums = [int(x) for x in re.findall(r"\d+", text or "")]
        if not nums or len(nums) > 2:
            raise ValueError("type the run as first-last page, e.g. 141-170")
        a, b = nums[0], nums[-1]
        if a > b:
            a, b = b, a
        if self.numbered:
            lo, hi = self.printed[0], self.printed[-1]
            if a < lo or b > hi:
                raise ValueError(f"{self.label}'s pages are {lo}–{hi}")
            first = self.printed.index(a) + 1 if a in self.printed else _after(self.printed, a)
            last = self.printed.index(b) + 1 if b in self.printed else _before(self.printed, b)
            if first > last:  # (every number typed falls among numbers the transcript skips)
                raise ValueError(f"no page of {self.label} is numbered {a}–{b}" if a != b
                                 else f"no page of {self.label} is numbered {a}")
            return first, last
        if a < 1 or b > self.pages:
            raise ValueError(f"{self.label} has pages 1–{self.pages}")
        return a, b


def _after(printed: list[int], n: int) -> int:
    """The place of the first page numbered n or more (a number skipped in the transcript)."""
    return next(i for i, p in enumerate(printed, 1) if p >= n)


def _before(printed: list[int], n: int) -> int:
    """The place of the last page numbered n or less."""
    return max(i for i, p in enumerate(printed, 1) if p <= n)


@dataclass
class Run:
    """A run of pages of one day (places a..b, from 1) and the firms (Attorney.key()) that ordered it; none =
    nobody ordered it (it is billed to nobody)."""
    day: Day
    start: int
    end: int
    keys: list[str] = field(default_factory=list)


def case_days(group: list[Job]) -> list[Day]:
    """The days of a case (the jobs of one invoice, see batch.invoice_groups), in order: a Day per job. A job of
    several days (Settings.batch_combine_dates) is one Day of all its pages, labelled with its dates, that can't
    be split (ordered whole)."""
    out = []
    for job in group:
        days = job.invoice_days()
        label = days[0][0] if len(days) == 1 else job.case.get("dates") or job.title()
        out.append(Day(job, label or "(no date)", job.portion_pages() if len(days) == 1 else job.invoice_pages(),
                       job.printed_pages() if len(days) == 1 else [], len(days) == 1))
    return out


def firms_of(group: list[Job]) -> list[Attorney]:
    """The table's columns: every attorney of the case's days who can be billed (ticked or not), each once."""
    out, seen = [], set()
    for job in group:
        for a in job.case.attorneys:
            if not a.is_placeholder() and a.key() and a.key() not in seen:
                seen.add(a.key())
                out.append(a)
    return out


def runs_of(day: Day, firms: list[Attorney] | None = None) -> list[Run]:
    """A day's runs as kept (Job.portions, when they still end on its pages), else the whole day ordered by the
    attorneys ticked on it. firms: the table's columns (firms_of); each run's firms are put in their order, so
    the same firms read the same on every day ("Dana + Pat", not also "Pat + Dana")."""
    job = day.job
    order = {a.key(): i for i, a in enumerate(firms or [])}

    def keys_of(keys) -> list[str]:
        """A row's firms in the table's order; ORDERED_BY_NOBODY becomes no firms."""
        return sorted((k for k in keys if k != ORDERED_BY_NOBODY), key=lambda k: order.get(k, len(order)))

    if day.splittable and job.portions is not None and job.portions_fit_pages():
        out, start = [], 1
        for last, keys in job.portions:
            out.append(Run(day, start, int(last), keys_of(keys)))
            start = int(last) + 1
        return out
    return [Run(day, 1, day.pages, keys_of(job.ticked_keys()))]


def carve(runs: list[Run], i: int, a: int, b: int) -> list[Run]:
    """The day's runs with run i set to pages a..b (places): the runs around it make room, and pages it no
    longer covers stay with the firms that had them (a new run). ValueError when a..b isn't within the day."""
    day = runs[i].day
    if a < 1 or b > day.pages or a > b:
        raise ValueError(f"{day.label} has pages {day.span(1, day.pages)}")
    keys = list(runs[i].keys)
    out: list[Run] = []
    for k, r in enumerate(runs):  # the other runs, less the pages a..b
        own_keys = keys if k == i else r.keys
        if r.start < a:
            out.append(Run(day, r.start, min(r.end, a - 1), list(own_keys)))
        if r.end > b:
            out.append(Run(day, max(r.start, b + 1), r.end, list(own_keys)))
    out.append(Run(day, a, b, keys))
    out.sort(key=lambda r: r.start)
    return out


def store(day: Day, runs: list[Run], firms: list[Attorney]) -> None:
    """Keeps a day's runs on its job: Job.portions (None when the whole day was ordered by one set of firms; a
    day that can't be split keeps none), and the attorneys ticked on it are those who ordered any of its pages
    (an attorney of another day is added to it, ticked). A Parties number below the firms that now order the
    whole day is cleared. The attorney table is then the user's (Job.att_touched)."""
    job = day.job
    used = list(dict.fromkeys(k for r in runs for k in r.keys))
    if day.splittable:
        whole = len(runs) == 1 and runs[0].keys
        job.portions = None if whole else [(r.end, list(r.keys) or [ORDERED_BY_NOBODY]) for r in runs]
        if whole and job.parties and job.parties < len(used):
            job.parties = 0
    have = {a.key() for a in job.case.attorneys}
    for a in job.case.attorneys:
        if not a.is_placeholder() and a.key():
            a.checked = a.key() in used
    for k in used:
        if k not in have:
            other = next((a for a in firms if a.key() == k), None)
            if other is not None:
                job.case.attorneys.append(replace(other, checked=True))
    job.att_touched = True


def billed_in(day: Day, run: Run) -> int:
    """The pages billed in a run: on a transcript of several reporters only those Whose pages... bills (the
    user's, by default; Job.day_mask)."""
    mask = day.job.day_mask() if day.splittable else None
    return sum(mask[run.start - 1:run.end]) if mask is not None else run.end - run.start + 1


@dataclass
class Prices:
    """What the table costs, at each speed offered: each run's price for each firm that ordered it, each set of
    firms ordering together (their pages, and what each of them pays for those pages), and each firm's invoice
    (rounded up to the cent, as invoiced)."""
    speeds: list[str]
    runs: dict[int, dict[str, Decimal]] = field(default_factory=dict)  # id(run) -> speed -> each pays
    # (firm keys, pages, speed -> what each of those firms pays), fewest firms first
    groups: list[tuple[tuple[str, ...], int, dict[str, Decimal]]] = field(default_factory=list)
    firms: list[tuple[str, dict[str, Decimal]]] = field(default_factory=list)  # (key, speed -> invoice)


def prices(days: list[Day], runs: list[Run], s: Settings) -> Prices:
    """The prices of the runs as they are (see Prices). A firm's share of a run is worked out exactly; the
    invoices round each firm's total up to the cent (invoice_calc.quote_shares). Only one set of invoices is
    priced (batch.joint_invoice): on a transcript of several reporters, the pages of the first reporter Whose
    pages... bills (the user, when ticked), not each reporter's set (batch.joint_invoice_sets)."""
    group = [d.job for d in days]
    try:
        case, opts = joint_invoice(group)
        orders = [(d, o) for d in days for o in d.job.invoice_orders()]
    except Exception:  # (a day with nothing to bill yet)
        return Prices([])
    sheet = s.sheet()
    speeds = offered(sheet, s.invoice_speeds, case.get("delivery") or s.agreement_speed())
    mode = opts.index if opts.index is not None else ("auto" if s.invoice_include_index else "off")
    # The index is decided date by date, as the invoices do (invoice.firm_invoices): a job of several days
    # isn't indexed on its total when no one of its days has the pages for it
    indexed = index_days([o.pages for _, o in orders], mode, s.invoice_index_rule, s.invoice_index_threshold)
    dates: dict[int, list[tuple[int, bool]]] = {}  # id(day) -> (pages, indexed) of each date it bills
    for (d, o), x in zip(orders, indexed):
        dates.setdefault(id(d), []).append((o.pages, x))
    email = s.invoice_include_email if opts.email is None else opts.email
    split = s.invoice_index_shared != "each"
    out = Prices([sp.name for sp in speeds])

    def shares_of(run: Run) -> list[Share]:
        """A run's pages for quote_shares, each share split as many ways as firms ordered the run."""
        n = len(run.keys)
        if run.day.job.portions is None and run.day.job.parties:  # (the Parties number shares a whole day)
            n = max(n, run.day.job.parties)
        own = dates.get(id(run.day), [])
        if not run.day.splittable and len(own) > 1:  # (ordered whole: a share per date, each indexed or not)
            return [Share(p, max(1, n), x) for p, x in own]
        return [Share(billed_in(run.day, run), max(1, n), any(x for _, x in own))]

    for r in runs:
        if r.keys:
            out.runs[id(r)] = {sp.name: quote_shares(shares_of(r), sp, email, split).total for sp in speeds}
    together: dict[tuple[str, ...], list[Run]] = {}
    order = {a.key(): i for i, a in enumerate(firms_of(group))}
    for r in runs:
        if r.keys:  # (the same firms, whatever order each day's attorney table has them in)
            together.setdefault(tuple(sorted(r.keys, key=lambda k: order.get(k, len(order)))), []).append(r)
    for keys, rs in together.items():
        shares = [x for r in rs for x in shares_of(r)]
        out.groups.append((keys, sum(x.pages for x in shares),
                           {sp.name: quote_shares(shares, sp, email, split).total for sp in speeds}))
    out.groups.sort(key=lambda g: (len(g[0]), g[0]))
    try:
        for f in firm_invoices(case, s, opts):
            if f.atty is not None:
                out.firms.append((f.atty.key(), {q.speed: q.per_party for q in f.quotes}))
    except Exception:  # (nothing to bill: no firm totals to show)
        pass
    return out
