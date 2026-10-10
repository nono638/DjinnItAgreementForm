"""Who ordered which pages of a case, as one table (the Excerpts window, gui/excerpts.py).

Each day of the case (a job, see batch.invoice_groups) is cut into runs of pages, and each run says which firms
ordered it: the whole day by everyone ticked (the default), or excerpts - firm A the whole trial, firm B a
stretch of day 1, firm C part of B's stretch and another of day 2. A run is typed in the page numbers printed
on the transcript ("141-170"; its place in the day when they aren't known) and the runs around it make room
(carve). A run removed gives its pages to the run above it (remove), and a day can be one run again
(whole_day). A run nobody ordered is billed to nobody. What is set is kept on each day as Job.portions (rows of
(last page, firms), and a run's own speeds, see store) and the attorneys ticked on it, so the invoices
(invoice.firm_invoices) bill it as before: pages ordered by several firms share the original and the judge's
index between them (the index too, when Settings.invoice_index_shared says "split"), and each firm pays its own
copy (and its own index). Whether a day gets an index is judged on every page of it, as
Settings.invoice_index_rule says (with "any", another day of the invoice reaching the threshold is enough), so a
firm that ordered 10 pages of a 100-page day pays an index of its 10 pages.

prices() works out, as the table is changed, what each run costs each firm that ordered it, what each set of
firms ordering together pays for its pages ("A + B: 15 pp., each pays ..."), and each firm's invoice.

Each firm on a run ordered it at a speed (Run.speeds; the day's, Job.speeds, unless the run has its own). A
firm billed alone may leave it unset and choose from its invoice; firms that ordered a run together commit to
theirs (courthouses.speed_upfront), and are priced at it (invoice_calc.quote_ordered): at different speeds, the
run's one original is billed at the fastest and divided as the courthouse's split rule says. Until a speed is
set, such a firm is priced at the agreement form's (invoice.default_speed), as Generate's question offers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from . import courthouses
from .batch import Job, joint_invoice, portion_row, same_entries
from .invoice import ORDERED_BY_NOBODY, default_speed, firm_invoices, index_mode, index_pages
from .invoice_calc import Share, index_days, offered, quote_ordered, quote_shares
from .models import Attorney
from .settings import Settings


@dataclass
class Day:
    """One day of the case: its job, its date, the pages Excerpts splits (Job.portion_pages; a job of several
    days: all its billed pages, Job.invoice_pages) and the number printed on each of them (Job.printed_pages:
    [] when not known, None for a page whose number isn't)."""
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
    """A run of pages of one day (places a..b, from 1), the firms (Attorney.key()) that ordered it (none = nobody
    ordered it: it is billed to nobody) and the speed each ordered it at, by key (only those set: the day's,
    Job.speeds, or the run's own)."""
    day: Day
    start: int
    end: int
    keys: list[str] = field(default_factory=list)
    speeds: dict[str, str] = field(default_factory=dict)

    def speed(self, key: str) -> str:
        """The speed firm `key` ordered the run at ("" = not set)."""
        return self.speeds.get(key, "")


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
    """The table's columns: every attorney of the case's days who can be billed (ticked or not), each once. The
    same firm on two days is one column: batch.same_entries runs first, as the prices and invoices see the
    firms (else a firm spelled two ways was two columns, and a tick stored its key twice)."""
    if len(group) > 1:
        same_entries(group)
    out, seen = [], set()
    for job in group:
        for a in job.case.attorneys:
            if not a.is_placeholder() and a.key() and a.key() not in seen:
                seen.add(a.key())
                out.append(a)
    return out


def runs_of(day: Day, firms: list[Attorney] | None = None) -> list[Run]:
    """A day's runs as kept (Job.portions, when they still end on its pages), else the whole day ordered by the
    attorneys ticked on it; each with the speeds set for its firms (Job.speed_of: the run's own, else the day's).
    firms: the table's columns (firms_of); each run's firms are put in their order, so
    the same firms read the same on every day ("Dana + Pat", not also "Pat + Dana")."""
    job = day.job
    order = {a.key(): i for i, a in enumerate(firms or [])}

    def keys_of(keys) -> list[str]:
        """A row's firms in the table's order; ORDERED_BY_NOBODY becomes no firms."""
        return sorted((k for k in keys if k != ORDERED_BY_NOBODY), key=lambda k: order.get(k, len(order)))

    def speeds_of(keys, row: int | None = None) -> dict[str, str]:
        """The speeds set for these firms on the run (Job.speed_of)."""
        return {k: job.speed_of(k, row) for k in keys if job.speed_of(k, row)}

    if day.splittable and job.portions is not None and job.portions_fit_pages():
        out, start = [], 1
        for i, (last, keys, *_) in enumerate(job.portions):
            ks = keys_of(keys)
            out.append(Run(day, start, int(last), ks, speeds_of(ks, i)))
            start = int(last) + 1
        return out
    ks = keys_of(job.ticked_keys())
    return [Run(day, 1, day.pages, ks, speeds_of(ks))]


def carve(runs: list[Run], i: int, a: int, b: int) -> list[Run]:
    """The day's runs with run i set to pages a..b (places): the runs around it make room, and pages it no
    longer covers stay with the firms that had them, at their speeds (a new run). ValueError when a..b isn't
    within the day."""
    day = runs[i].day
    if a < 1 or b > day.pages or a > b:
        raise ValueError(f"{day.label} has pages {day.span(1, day.pages)}")
    keys, speeds = list(runs[i].keys), dict(runs[i].speeds)
    out: list[Run] = []
    for k, r in enumerate(runs):  # the other runs, less the pages a..b
        own_keys, own_speeds = (keys, speeds) if k == i else (r.keys, r.speeds)
        if r.start < a:
            out.append(Run(day, r.start, min(r.end, a - 1), list(own_keys), dict(own_speeds)))
        if r.end > b:
            out.append(Run(day, max(r.start, b + 1), r.end, list(own_keys), dict(own_speeds)))
    out.append(Run(day, a, b, keys, speeds))
    out.sort(key=lambda r: r.start)
    return out


def remove(runs: list[Run], i: int) -> list[Run]:
    """The day's runs without run i, every page still in one run (Remove run). Its pages go to the run above it,
    or to the run below for the day's first run: "21-30" of "1-20 Alex, 21-30 Alex + Dana, 31-40 Sam" joins
    "1-20 Alex". When that run was ordered by nobody and the run on the other side by a firm, they go to the
    other side instead (a run removed isn't left to nobody when a firm can have it). The run that grew is then
    joined with the runs touching it ordered by the same firms at the same speeds: "1-20 Alex, 21-30 Alex +
    Dana, 31-40 Alex" without "21-30" is "1-40 Alex". Runs of the same firms elsewhere in the day stay apart.
    ValueError when run i is the day's only run."""
    day = runs[i].day
    if len(runs) < 2:
        raise ValueError(f"{day.label} is one run: nothing to remove. Untick its firms to bill it to nobody.")
    out = [Run(day, r.start, r.end, list(r.keys), dict(r.speeds)) for r in runs]
    gone = out.pop(i)
    above = i - 1 if i > 0 else None
    below = i if i < len(out) else None  # (the run below is at i once run i is out)
    j, other = (above, below) if above is not None else (below, None)
    if not out[j].keys and other is not None and out[other].keys:
        j = other
    grown = out[j]
    grown.start, grown.end = min(grown.start, gone.start), max(grown.end, gone.end)
    same = (set(grown.keys), grown.speeds)
    lo = hi = j
    while lo > 0 and (set(out[lo - 1].keys), out[lo - 1].speeds) == same:
        lo -= 1
    while hi + 1 < len(out) and (set(out[hi + 1].keys), out[hi + 1].speeds) == same:
        hi += 1
    return out[:lo] + [Run(day, out[lo].start, out[hi].end, list(grown.keys), dict(grown.speeds))] + out[hi + 1:]


def whole_day(day: Day, runs: list[Run], ticked: list[str], firms: list[Attorney] | None = None) -> list[Run]:
    """The day as one run of all its pages (Remove this day's excerpts), ordered by every firm that ordered any
    of its runs: "1-20 Alex, 21-40 Alex + Dana" is "1-40 Alex + Dana", each at the speed of its first run that
    has one. When no run was ordered by anyone, by the firms ticked on the day (ticked: Job.ticked_keys). firms:
    the table's columns (firms_of), to put the firms in their order, as runs_of does."""
    keys = list(dict.fromkeys(k for r in runs for k in r.keys)) or list(dict.fromkeys(ticked))
    if firms:
        order = {a.key(): n for n, a in enumerate(firms)}
        keys.sort(key=lambda k: order.get(k, len(order)))
    speeds: dict[str, str] = {}
    for r in runs:
        for k, sp in r.speeds.items():
            if k in keys and sp:
                speeds.setdefault(k, sp)
    return [Run(day, 1, day.pages, keys, speeds)]


def store(day: Day, runs: list[Run], firms: list[Attorney], s: Settings | None = None) -> None:
    """Keeps a day's runs on its job: Job.portions (None when the whole day was ordered by one set of firms; a
    day that can't be split keeps none), and the attorneys ticked on it are those who ordered any of its pages
    (an attorney of another day is added to it, ticked). The runs' speeds: the day's own (Job.speeds) for a day
    of one run (a day that can't be split is one), else a run's speed that isn't its firm's on the day is kept in
    its row. A Parties number below the firms that now order the whole day is cleared. The attorney table is
    then the user's (Job.att_touched). With the settings `s`, the day's No. of copies follows the parties now
    ordering (Job.refresh_copies; the window passes them)."""
    job = day.job
    used = list(dict.fromkeys(k for r in runs for k in r.keys))
    if day.splittable:
        whole = len(runs) == 1 and runs[0].keys
        if whole:
            job.speeds.update({k: sp for k, sp in runs[0].speeds.items() if k in runs[0].keys and sp})
        job.portions = None if whole else [
            portion_row(r.end, list(dict.fromkeys(r.keys)) or [ORDERED_BY_NOBODY],
                        {k: sp for k, sp in r.speeds.items() if k in r.keys and sp and sp != job.speeds.get(k)})
            for r in runs]
        if whole and job.parties and job.parties < len(used):
            job.parties = 0
    else:  # (a job of several days is ordered whole: its one run's speeds are the day's own)
        job.speeds.update({k: sp for r in runs for k, sp in r.speeds.items() if k in r.keys and sp})
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
    if s is not None:
        job.refresh_copies(s)  # No. of copies follows the parties ordering now


def billed_in(day: Day, run: Run) -> int:
    """The pages billed in a run: on a transcript of several reporters only those Whose pages... bills (the
    user's, by default; Job.day_mask)."""
    mask = day.job.day_mask() if day.splittable else None
    return sum(mask[run.start - 1:run.end]) if mask is not None else run.end - run.start + 1


@dataclass
class Prices:
    """What the table costs. Runs priced at each speed offered (their firms choose from the invoice): each run's
    price for each firm that ordered it and each set of firms ordering together (their pages, and what each of
    them pays for those pages). Runs whose firms are priced at the speeds they ordered (`ordered`: a speed set,
    or a split order that commits to one): what each firm pays for the run at its speed, and each set of firms
    and speeds ordering together. And each firm's invoice (rounded up to the cent, as invoiced)."""
    speeds: list[str]
    runs: dict[int, dict[str, Decimal]] = field(default_factory=dict)  # id(run) -> speed -> each pays
    # (firm keys, pages, speed -> what each of those firms pays), fewest firms first
    groups: list[tuple[tuple[str, ...], int, dict[str, Decimal]]] = field(default_factory=list)
    firms: list[tuple[str, dict[str, Decimal]]] = field(default_factory=list)  # (key, speed -> invoice)
    # id(run) -> firm key -> (its speed, what it pays for the run): runs priced at the speeds ordered
    ordered: dict[int, dict[str, tuple[str, Decimal]]] = field(default_factory=dict)
    # ((key, speed) of each firm, pages, key -> what it pays for those pages), fewest firms first
    ordered_groups: list[tuple[tuple[tuple[str, str], ...], int, dict[str, Decimal]]] = field(default_factory=list)
    # (the speed a firm is priced at while none is set for it: the agreement form's, see invoice.default_speed)
    fallback: str = ""
    committed: set[str] = field(default_factory=set)  # the firms whose invoice bills the speeds they ordered


def prices(days: list[Day], runs: list[Run], s: Settings) -> Prices:
    """The prices of the runs as they are (see Prices). A firm's share of a run is worked out exactly; the
    invoices round each firm's total up to the cent (invoice_calc.quote_shares, quote_ordered). Only one set of
    invoices is priced (batch.joint_invoice): on a transcript of several reporters, the pages of the first
    reporter Whose pages... bills (the user, when ticked), not each reporter's set (batch.joint_invoice_sets)."""
    group = [d.job for d in days]
    try:
        case, opts = joint_invoice(group)
        orders = [(d, o) for d in days for o in d.job.invoice_orders()]
    except Exception:  # (a day with nothing to bill yet)
        return Prices([])
    sheet = s.sheet()
    speeds = offered(sheet, s.invoice_speeds, case.get("delivery") or s.agreement_speed())
    # The index is decided date by date, as the invoices do (invoice.firm_invoices): a job of several days
    # isn't indexed on its total when no one of its days has the pages for it; each day on every page of its
    # transcripts, whoever wrote them (invoice.index_pages)
    indexed = index_days(index_pages([o for _, o in orders]), index_mode(opts, s), s.invoice_index_rule,
                         s.invoice_index_threshold)
    dates: dict[int, list[tuple[int, bool]]] = {}  # id(day) -> (pages, indexed) of each date it bills
    for (d, o), x in zip(orders, indexed):
        dates.setdefault(id(d), []).append((o.pages, x))
    email = s.invoice_include_email if opts.email is None else opts.email
    split = s.invoice_index_shared != "each"
    out = Prices([sp.name for sp in speeds], fallback=default_speed(case, s))
    upfront, rule = courthouses.speed_upfront(), courthouses.split_rule()
    names = {a.key(): a.label() for a in firms_of(group)}

    def shares_of(run: Run, key: str | None = None) -> list[Share]:
        """A run's pages for quote_shares, each share split as many ways as firms ordered the run; for firm
        `key`, as quote_ordered prices them: at its speed, with the other firms' (unset: the form's)."""
        n = len(run.keys)
        if run.day.job.portions is None and run.day.job.parties:  # (the Parties number shares a whole day)
            n = max(n, run.day.job.parties)
        own = dates.get(id(run.day), [])
        speed, others = "", ()
        if key is not None:
            speed = run.speed(key) or out.fallback
            others = tuple((names.get(o, o), run.speed(o) or out.fallback) for o in run.keys if o != key)
        if not run.day.splittable and len(own) > 1:  # (ordered whole: a share per date, each indexed or not)
            return [Share(p, max(1, n), x, speed, others) for p, x in own]
        return [Share(billed_in(run.day, run), max(1, n), any(x for _, x in own), speed, others)]

    def at_speeds(run: Run) -> bool:
        """The run's firms are priced at the speeds they ordered (as invoice.firm_invoices bills them): a speed
        is set for one of them, or they ordered it together and must commit to theirs."""
        return any(run.speed(k) for k in run.keys) or (len(run.keys) > 1 and upfront)

    for r in runs:
        if not r.keys:
            continue
        if at_speeds(r):
            try:
                out.ordered[id(r)] = {k: (r.speed(k) or out.fallback,
                                          quote_ordered(shares_of(r, k), s.sheet(), rule, email, split).total)
                                      for k in r.keys}
            except ValueError:  # (a speed the sheet lacks, or too many speeds: the window says why)
                pass
        else:
            out.runs[id(r)] = {sp.name: quote_shares(shares_of(r), sp, email, split).total for sp in speeds}
    together: dict[tuple[str, ...], list[Run]] = {}
    at: dict[tuple[tuple[str, str], ...], list[Run]] = {}
    order = {a.key(): i for i, a in enumerate(firms_of(group))}
    for r in runs:
        if not r.keys:
            continue
        keys = tuple(sorted(r.keys, key=lambda k: order.get(k, len(order))))  # (whatever order each day's has)
        if id(r) in out.ordered:
            at.setdefault(tuple((k, out.ordered[id(r)][k][0]) for k in keys), []).append(r)
        elif id(r) in out.runs:
            together.setdefault(keys, []).append(r)
    for keys, rs in together.items():
        shares = [x for r in rs for x in shares_of(r)]
        out.groups.append((keys, sum(x.pages for x in shares),
                           {sp.name: quote_shares(shares, sp, email, split).total for sp in speeds}))
    out.groups.sort(key=lambda g: (len(g[0]), g[0]))
    for pairs, rs in at.items():
        pages = sum(billed_in(r.day, r) for r in rs)
        out.ordered_groups.append((pairs, pages, {k: sum((out.ordered[id(r)][k][1] for r in rs), Decimal(0))
                                                  for k, _ in pairs}))
    out.ordered_groups.sort(key=lambda g: (len(g[0]), g[0]))
    try:
        for f in firm_invoices(case, s, opts):
            if f.atty is not None:
                out.firms.append((f.atty.key(), {q.speed: q.per_party for q in f.quotes}))
                if any(q.ordered for q in f.quotes):
                    out.committed.add(f.atty.key())
    except Exception:  # (nothing to bill, or a speed the sheet lacks: no firm totals to show)
        pass
    return out
