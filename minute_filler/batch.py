"""Batch processing: many documents in, one job (one set of forms) per case and date out.

Two documents belong to the same job when they are about the same case - the same
index number or, when one of them has no index number, a matching caption - and
share a date of proceedings. A transcript and the invoice for it therefore make
one job, while two days of the same trial make two (unless
Settings.batch_combine_dates is on). The days of one case still share one invoice
(see invoice_groups and joint_invoice), unless Settings.invoice_joint is off; each
attorney's invoice bills only the days it is ticked on, and of a day split by
Excerpts... (Job.portions) only the pages it ordered.

A transcript written by several reporters (their initials alternate at the foot
of its pages, see takes.page_owners) is billed for the user's own pages only,
unless Whose pages... says otherwise (Job.page_basis, Job.front_owner). A
transcript whose pages are someone else's, or that begins with pages nobody's
initials are on, gets no invoice until the user says whose pages to bill
(Job.ownership_problem).

fill_jobs makes the outputs of every job (Generate all). A day once invoiced
(Job.invoiced) is not billed again until a new document is added to it; when a run stops part way, the
attorneys already invoiced (Job.invoiced_keys) are not billed again by the next one. A day whose Excerpts...
rows no longer fit it (Job.portions_problem) gets no invoice until they are checked.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .extract_regex import RegexExtractor, dedupe_attorneys, find_dates, norm_index
from .deliver import NO_INVOICE, generate, ledger_for
from .fill import is_generated, short_caption
from .invoice import DayOrder, InvoiceOpts, Portion, day_reporters, invoice_count, reporters_text
from .ingest import IMAGE_EXT, Ingested, ingest_file
from .log import error as log_error, log
from .merge import merge, refresh_delivery_date, refresh_rate
from .models import Attorney, CaseInfo, Extraction, FIELD_LABELS, FieldState, SRC_REGEX, SRC_USER, to_int
from .runsheet import NO_RUNSHEET, RunSheetOpts, matches, name_rows, read_info, rows_from, transcript_pages
from .takes import my_initials, page_owners
from .settings import OUTPUTS, Settings

BATCH_EXT = {".pdf", ".eml", ".docx", ".txt", ".htm", ".html"} | IMAGE_EXT  # taken from a dropped folder
Progress = Callable[[int, int, str], None]
# How fill_jobs begins a day's error when its invoice was held back (a choice to make): the window tells these
# apart from files that could not be saved
NOT_INVOICED = "invoice not made:"

# Words that don't help tell two captions apart.
_NOISE = {"the", "of", "and", "in", "re", "matter", "et", "al", "ano", "inc", "llc", "llp", "corp",
          "corporation", "co", "company", "ltd", "pc", "as", "by"}


# ----------------------------------------------------------------- identity

@dataclass(frozen=True)
class Ident:
    """What a document says about which case and day it belongs to."""
    index: str = ""
    dates: frozenset = frozenset()
    sides: tuple = ()  # caption words, one set per side of the "v."

    def __bool__(self) -> bool:
        """False when there is neither index number nor caption: nothing to match the case by."""
        return bool(self.index or self.sides)


def _sides(name: str) -> tuple:
    """Caption words per side; a word written with a full stop ("Auth.") keeps it, as it may be short for another."""
    t = re.sub(r"[^a-z0-9. ]", " ", name.lower().replace("-against-", " v "))
    parts = re.split(r"\s+(?:v|vs|versus|against)\.?\s+", " ".join(t.split()), maxsplit=1)
    sides = []
    for p in parts:
        words = set()
        for w in p.split():
            core = w.replace(".", "")
            if len(core) > 1 and core not in _NOISE:
                words.add(core + ("." if w.endswith(".") else ""))
        sides.append(frozenset(words))
    return tuple(sides) if all(sides) else ()


def ident(case: CaseInfo) -> Ident:
    """What case and days these fields are about: the index number, the dates and the caption words."""
    nums = re.findall(r"\d+", case.get("index_no"))
    # "712345/21" is "712345/2021" (as on the run sheet, see runsheet.index_numbers)
    index = (norm_index(*nums) if len(nums) == 2 else None) or "/".join(str(int(n)) for n in nums)
    dates = frozenset(d for _, _, d in find_dates(case.get("dates")))
    return Ident(index, dates, _sides(case.get("case_name")))


def _same_word(a: str, b: str) -> bool:
    """The same caption word, or one is short for the other: "auth." is "authority", but "smith" is not
    "smithson" (only a word written with a full stop is a short form)."""
    x, y = a.rstrip("."), b.rstrip(".")
    if x == y:
        return True
    return (a.endswith(".") and len(x) >= 3 and y.startswith(x)) or (b.endswith(".") and len(y) >= 3 and x.startswith(y))


def _same_caption(a: tuple, b: tuple) -> bool:
    """'Roe v Poe' matches 'Jane Roe v. Poe Trucking Corp.': on each side, every
    word of the shorter name appears in the longer one."""
    if not a or len(a) != len(b):
        return False
    for x, y in zip(a, b):
        small, big = sorted((x, y), key=len)
        if not all(any(_same_word(w, v) for v in big) for w in small):
            return False
    return True


def same_case(a: Ident, b: Ident) -> bool:
    """The same index number when both have one, else matching captions (dates are not compared)."""
    if a.index and b.index:
        return a.index == b.index
    return _same_caption(a.sides, b.sides)


# --------------------------------------------------------------------- jobs

@dataclass
class Doc:
    """One input with what was extracted from it."""
    ing: Ingested
    regex: Extraction
    ai: Extraction | None = None
    path: str = ""
    ident: Ident = field(default_factory=Ident)

    def extractions(self) -> list[Extraction]:
        """The regex extraction, then the AI model's when there is one (for merge)."""
        return [self.regex] + ([self.ai] if self.ai is not None else [])

    def key(self) -> str:
        """The document as Job.page_basis and Job.front_owner name it: its path (its name when pasted)."""
        return self.path or self.ing.name

    def owners(self) -> list[str]:
        """Whose each billed page of this transcript is: the reporter's initials ("" for pages before the first
        initials; see takes.page_owners). [] when no page has initials (a scan, or a reporter who doesn't
        initial the pages)."""
        n = transcript_pages(self.ing)
        owners = page_owners(self.ing.marks)[:n]
        if not any(owners):
            return []
        return owners + [owners[-1]] * (n - len(owners))

    def reporters(self) -> list[str]:
        """The initials on this transcript's pages, in the order they first appear (["pr", "ds"])."""
        return list(dict.fromkeys(o for o in self.owners() if o))

    def front_pages(self) -> int:
        """How many pages this transcript begins with before the first initials (whose they are isn't known
        when several reporters wrote it)."""
        owners = self.owners()
        return next((i for i, o in enumerate(owners) if o), 0)


@dataclass
class Job:
    """The documents for one form (per attorney) and the editable result."""
    docs: list[Doc] = field(default_factory=list)
    case: CaseInfo = field(default_factory=CaseInfo)
    proc_touched: bool = False   # the user changed the proceeding types / attorneys,
    att_touched: bool = False    # so later extractions must not overwrite them
    batch: bool = False          # put together by group()
    include: bool = True         # ticked for "Generate all"
    saved: list[Path] = field(default_factory=list)
    error: str = ""
    invoiced: bool = False       # an invoice covering this day was made: Generate all must not bill it again
    # the Attorney.key()s invoiced for this day by a run that stopped before every attorney's invoice was made:
    # the next run leaves them out (see unbill)
    invoiced_keys: list = field(default_factory=list)
    parties: int = 0             # ordering parties on the invoice; 0 = the attorneys ticked (see ticked_keys)
    # The invoice's own choices (Extras and Customize); None = as Settings say.
    invoice_email: bool | None = None  # an e-mailed copy for each party
    invoice_index: str | None = None   # the index: "auto", "on" or "off"
    invoice_show: list | None = None   # what granular detail shows (keys of settings.DETAIL_ITEMS)
    invoice_detail: bool = False       # "Show granular detail" for this job's invoice (off for every new job)
    # Who ordered which pages of the day (the Excerpts... grid): rows of (last page, Attorney.key()s of
    # attorneys ticked on the day), each row from the page after the one above; the last ends at the day's
    # pages. None = every ticked attorney ordered every page. Only for a job of one day. Kept when they no
    # longer fit (the pages changed, an attorney was unticked): see portions_problem.
    portions: list | None = None
    runsheet_to: str | None = None  # the run sheet to add to; "" = a new one; None = as Settings say
    # Whose pages of a transcript of several reporters are billed (Whose pages...), by Doc.key(): "me" (the
    # default: the pages with the user's initials), another reporter's initials, or "*" (the whole transcript).
    page_basis: dict = field(default_factory=dict)
    # Who the pages before the first initials count for, by Doc.key(): their initials, or "none". Missing on a
    # transcript of several reporters that begins with such pages: Whose pages... must say (ownership_problem).
    front_owner: dict = field(default_factory=dict)
    own: tuple = ()  # the user's initials (takes.my_initials), set from the settings by remerge
    runsheet_group: int | None = None  # jobs of one case asked about together: they share the run sheet made

    def is_empty(self) -> bool:
        """Nothing dropped and nothing typed."""
        typed = any(f.source == SRC_USER and f.value for f in self.case.fields.values())
        return not (self.docs or typed or self.case.attorneys or self.proc_touched)

    def idents(self) -> list[Ident]:
        """What the job's fields and each of its documents say about the case (empty ones left out)."""
        return [i for i in [ident(self.case)] + [d.ident for d in self.docs] if i]

    def title(self) -> str:
        """The job's name in the list: the short caption, else the index number, else the first file's name."""
        return (short_caption(self.case.get("case_name"), 48) or self.case.get("index_no")
                or (self.docs[0].ing.name if self.docs else "New job"))

    def missing(self) -> list[str]:
        """The keys of the required fields (models.REQUIRED_KEYS) that are still blank."""
        return self.case.missing_required()

    def no_attorney_chosen(self) -> bool:
        """Attorneys were found but none is ticked, so the form's attorney block would stay blank.
        (A transcript lists everyone who appeared; it can't say who ordered.)"""
        real = [a for a in self.case.attorneys if not a.is_placeholder()]
        return bool(real) and not any(a.checked for a in self.case.attorneys)

    def problems(self) -> list[str]:
        """What to check before the forms are made: "Index Number is missing", "no attorney is ticked"."""
        out =[FIELD_LABELS[k] + " is missing" for k in self.missing()]
        if self.no_attorney_chosen():
            out.append("no attorney is ticked")
        return out

    def name_key(self) -> tuple:
        """The case as file names show it: jobs with the same key (days of one case) get dated file names."""
        return short_caption(self.case.get("case_name")).lower(), self.case.get("index_no")

    def form_count(self) -> int:
        """One agreement per ticked attorney, at least one (invoices: see CaseInfo.invoice_orderers)."""
        return max(1, sum(1 for a in self.case.attorneys if a.checked))

    def transcripts(self) -> list[Doc]:
        """The transcript PDFs among the job's documents (a photo of a transcript has no pages to count)."""
        return [d for d in self.docs if d.regex.doc_kind == "transcript" and d.ing.kind == "pdf"]

    def transcript_pages(self) -> int:
        """Pages of the transcript PDFs among the inputs, without the word index printed after them; 0 when
        there is none (then no invoice or run sheet can be made)."""
        return sum(transcript_pages(d.ing) for d in self.transcripts())

    def runsheet_opts(self, s: Settings) -> RunSheetOpts:
        """The takes of every transcript of the job, for the run sheet."""
        rows = []
        for d in self.transcripts():
            rows += rows_from(d.ing, self._day_of(d), s)
        rows.sort(key=lambda r: (r.day or date.max, r.start if r.start is not None else -1))
        name_rows(rows)  # a reporter named on one day's title page is named on the other days too
        return RunSheetOpts(rows, self.runsheet_to)

    def _day_of(self, doc: Doc) -> date | None:
        """The day of a transcript: its own date, else the job's first."""
        days = sorted(doc.ident.dates, key=_date_key) or [d for _, _, d in find_dates(self.case.get("dates"))]
        if not days:
            return None
        m, d, y = (int(x) for x in days[0].split("/"))
        return date(y, m, d)

    def pages_typed(self) -> bool:
        """The Pages field was typed in: it is what is billed, whoever wrote the pages."""
        return self.case.fields["est_pages"].source == SRC_USER

    def billed_mask(self, doc: Doc) -> list[bool] | None:
        """Which pages of a transcript are billed, page by page; None when all of them are. They all are when no
        page has initials, when one reporter wrote it all (the user, or nobody says who the user is), or when
        Whose pages... says the whole transcript. Otherwise those with the user's initials (or the reporter's
        chosen under Whose pages...); the pages before the first initials count for Job.front_owner."""
        owners = doc.owners()
        found = list(dict.fromkeys(o for o in owners if o))
        basis = self.page_basis.get(doc.key(), "me")
        if basis == "*" or not found:
            return None
        if basis == "me" and len(found) == 1 and (not self.own or found[0] in self.own):
            return None
        targets = set(self.own) if basis == "me" else {basis}
        front = found[0] if len(found) == 1 else self.front_owner.get(doc.key(), "")
        return [(o or front) in targets for o in owners]

    def billed_pages_of(self, doc: Doc) -> int:
        """The pages of one transcript that are billed (see billed_mask)."""
        mask = self.billed_mask(doc)
        return transcript_pages(doc.ing) if mask is None else sum(mask)

    def own_pages(self) -> int:
        """The pages of the job's transcripts that are billed: the user's own, on a transcript of several
        reporters (see billed_mask)."""
        return sum(self.billed_pages_of(d) for d in self.transcripts())

    def shared_transcripts(self) -> list[Doc]:
        """The transcripts written by more than one reporter (Whose pages... lists them)."""
        return [d for d in self.transcripts() if len(d.reporters()) > 1]

    def ownership_problem(self) -> str:
        """Why the job's transcripts can't be billed as they are ("" when they can): one of several reporters
        begins with pages nobody's initials are on, none of a transcript's pages carry the user's initials (or
        the user's initials aren't known), or the reporter chosen under Whose pages... is no longer on it. The
        invoice is held until Whose pages... says (see invoice_hold). A Pages field typed in is billed as it is."""
        if self.pages_typed():
            return ""
        for d in self.transcripts():
            found, basis = d.reporters(), self.page_basis.get(d.key(), "me")
            if basis == "*" or not found:
                continue
            if basis != "me" and basis not in found:
                return f"{d.ing.name}: {basis.upper()}'s initials are no longer on its pages"
            front = d.front_pages()
            if len(found) > 1 and front and d.key() not in self.front_owner:
                pages = "page has" if front == 1 else f"{front} pages have"
                return f"the first {pages} no reporter's initials in {d.ing.name}: whose are they?"
            if basis == "me" and len(found) > 1 and not self.own:
                return (f"{d.ing.name} was written by several reporters ({', '.join(x.upper() for x in found)}): "
                        "your initials aren't known (Settings → My info)")
            if basis == "me" and not self.billed_pages_of(d):
                mine = "/".join(sorted(x.upper() for x in self.own))
                return (f"none of the pages of {d.ing.name} carry your initials ({mine}); "
                        f"they are {', '.join(x.upper() for x in found)}'s")
        return ""

    def invoice_hold(self) -> str:
        """Why no invoice is made for this job until the user checks something ("" when it can be made):
        Excerpts... rows that no longer fit, or whose pages to bill (Whose pages...)."""
        if self.portions_problem():
            return self.portions_check()
        why = self.ownership_problem()
        return f"Whose pages… needs choosing ({why})" if why else ""

    def invoice_pages(self) -> int:
        """The pages to bill: the Pages field when typed in, else the user's own pages of the transcripts (all
        of them when one reporter wrote them, see billed_mask); 0 = no transcript, or no page is the user's
        (then ownership_problem says why)."""
        pages = self.transcript_pages()
        if not pages:
            return 0
        if self.pages_typed():
            return to_int(self.case.get("est_pages"), pages)
        # worked out afresh, not read from the Pages field: Whose pages... may have changed since it was filled
        return self.own_pages()

    def day_mask(self) -> list[bool] | None:
        """Which pages of the job's transcripts (one after the other) are billed; None when they all are or the
        Pages field was typed in. Excerpts... rows count pages in this whole stretch."""
        if self.pages_typed():
            return None
        masks = [(self.billed_mask(d), transcript_pages(d.ing)) for d in self.transcripts()]
        if all(m is None for m, _ in masks):
            return None
        return [b for m, n in masks for b in (m if m is not None else [True] * n)]

    def portion_pages(self) -> int:
        """The pages Excerpts... splits: every page of the day's transcripts when only some of them are
        billed (a firm is billed for the billed pages of the stretch it ordered), else the pages billed."""
        mask = self.day_mask()
        return len(mask) if mask is not None else self.invoice_pages()

    def printed_pages(self) -> list[int | None]:
        """The number printed on each page Excerpts... counts ([358, 359, ...]; None where none is known);
        [] when the Pages field was typed in."""
        if self.pages_typed():
            return []
        out: list[int | None] = []
        for d in self.transcripts():
            n = transcript_pages(d.ing)
            nums = [m.number for m in d.ing.marks[:n]]
            nums += [None] * (n - len(nums))
            prev = d.ing.first_page_no - 1 if d.ing.first_page_no is not None and not d.ing.marks else None
            for x in nums:
                prev = x if x is not None else (prev + 1 if prev is not None else None)
                out.append(prev)
        return out if len(out) == self.portion_pages() else []

    def invoice_days(self) -> list[tuple[str, int]]:
        """(date, pages) of each day billed, earliest first: a line per day of the transcripts (volumes of one
        day added up), or one line with the job's dates and all the pages when there is one day, the Pages
        field was typed in, or a transcript has no date (its day isn't known). Empty without a transcript."""
        pages = self.invoice_pages()
        if not pages:
            return []
        by_day: dict[date | None, int] = {}
        for d in self.transcripts():
            day = self._day_of(d)
            by_day[day] = by_day.get(day, 0) + self.billed_pages_of(d)
        typed = self.pages_typed()
        if len(by_day) < 2 or typed or None in by_day:
            return [(self.case.get("dates"), pages)]
        return [(f"{d.month}/{d.day}/{d.year}", n) for d, n in sorted(by_day.items())]

    def day_totals(self) -> list[int]:
        """Every page of the transcripts of each day of invoice_days (the user's or not), in the same order."""
        days = self.invoice_days()
        if len(days) < 2:
            return [self.transcript_pages()] if days else []
        by_day: dict[date | None, int] = {}
        for d in self.transcripts():
            by_day[self._day_of(d)] = by_day.get(self._day_of(d), 0) + transcript_pages(d.ing)
        return [n for _, n in sorted(by_day.items())]

    def ticked_keys(self) -> list[str]:
        """The Attorney.key() of each attorney ticked on this day who gets an invoice, each once (see
        CaseInfo.invoice_orderers): who can order its pages."""
        return [a.key() for a in self.case.invoice_orderers() if a is not None]

    def invoice_opts(self) -> InvoiceOpts:
        """The invoice's pages (of each day), ordering parties (the ticked attorneys unless set), who ordered
        which pages, the attorneys invoiced already by a run that stopped part way, and the job's own Extras
        and granular detail choices."""
        return InvoiceOpts(self.invoice_pages(), self.parties or max(1, len(self.ticked_keys())),
                           days=self.invoice_days(), email=self.invoice_email, index=self.invoice_index,
                           show=self.invoice_show, detail=self.invoice_detail, orders=self.invoice_orders(),
                           skip=self.billed_keys(), my_pages=self.invoice_pages(),
                           total_pages=self.transcript_pages(), reporters=self.reporters_text())

    def reporter_pages(self, docs: list[Doc] | None = None) -> dict[str, int]:
        """How many pages of the job's transcripts (or of these of them) each reporter wrote, by initials
        ({"pr": 65, "ds": 85}; "" for pages nobody's initials are on). Empty when no page has initials."""
        out: dict[str, int] = {}
        for d in self.transcripts() if docs is None else docs:
            owners, found = d.owners(), d.reporters()
            front = found[0] if len(found) == 1 else self.front_owner.get(d.key(), "")
            for o in owners:
                who = o or (front if front != "none" else "")
                out[who] = out.get(who, 0) + 1
        return out

    def reporters_text(self) -> str:
        """'PR 65, DS 85' (see reporter_pages), for the records; "" when no page has initials."""
        return reporters_text(self.reporter_pages())

    def day_reporters(self) -> list[dict[str, int]]:
        """reporter_pages of each day of invoice_days, in the same order (a firm's invoice of some of the days
        names who wrote those days only)."""
        days = self.invoice_days()
        if len(days) < 2:
            return [self.reporter_pages()] if days else []
        by_day: dict[date | None, list[Doc]] = {}
        for d in self.transcripts():
            by_day.setdefault(self._day_of(d), []).append(d)
        return [self.reporter_pages(docs) for _, docs in sorted(by_day.items())]

    def billed_keys(self) -> list[str]:
        """The attorneys not to invoice again for this day: those a stopped run invoiced already. Empty once
        the day is invoiced (Generate this job bills it again when asked)."""
        return [] if self.invoiced else list(self.invoiced_keys)

    def unbill(self) -> None:
        """New documents may mean new pages to bill: the day is billable again, for every attorney."""
        self.invoiced = False
        self.invoiced_keys = []

    def portions_unavailable(self) -> str:
        """Why the pages of this job can't be split between attorneys (Excerpts...), or "" when they can:
        it needs pages to bill, one day (a job of several days is split day by day) and two attorneys ticked
        on it."""
        days = self.invoice_days()
        if not days:
            return "There are no transcript pages to bill yet."
        if len(days) > 1:
            return ("This job covers several days, and who ordered which pages is set one day at a time.\n"
                    "Untick Settings → Options → \"Batches: put all days of the same case on one form\"\n"
                    "so that each day is a job of its own.")
        if len(self.ticked_keys()) < 2:
            return "Tick at least two attorneys on this day first: with one, that attorney ordered every page."
        return ""

    def portions_fit_pages(self) -> bool:
        """Job.portions (when there are any) are rows that end in order, the last on the day's pages (all of
        them, see portion_pages)."""
        rows = self.portions
        try:
            ends = [int(last) for last, _ in rows]
            return bool(ends) and ends[-1] == self.portion_pages() and all(a < b for a, b in zip([0] + ends, ends))
        except (TypeError, ValueError):
            return False

    def portions_problem(self) -> str:
        """Why Job.portions can't bill the day as they are ("" when they can, or there are none): the day no
        longer has one day of pages or two attorneys ticked, the rows don't end on its pages, or a row names
        an attorney not ticked on it (unticked or renamed since). The rows are kept for the user to check:
        no invoice is made for the day until they do (see the window's Excerpts...)."""
        if self.portions is None:
            return ""
        if not self.invoice_days() or len(self.invoice_days()) > 1:
            return "it is for one day, and this job no longer is"
        if not self.portions_fit_pages():
            return f"its rows don't end on this day's {self.portion_pages()} pages"
        ticked = set(self.ticked_keys())
        if any(not keys or any(k not in ticked for k in keys) for _, keys in self.portions):
            return "it names an attorney no longer ticked"
        if len(ticked) < 2:
            return "fewer than two attorneys are ticked"
        return ""

    def portions_check(self) -> str:
        """The warning for portions_problem ("" when there is none): 'Excerpts… needs checking (…)'."""
        why = self.portions_problem()
        return f"Excerpts… needs checking ({why})" if why else ""

    def valid_portions(self) -> list | None:
        """Job.portions when they bill the day as they are (see portions_problem), else None. Nothing is
        changed: rows that don't fit are kept until the user checks them."""
        return self.portions if self.portions is not None and not self.portions_problem() else None

    def rename_in_portions(self, old: str, new: str) -> None:
        """An attorney's name (or firm) was changed, from Attorney.key() `old` to `new`: the Excerpts... rows
        follow it (a name cleared drops it from them)."""
        if self.portions is None or not old or old == new:
            return
        self.portions = [(last, list(dict.fromkeys(new if k == old else k for k in keys if k != old or new)))
                         for last, keys in self.portions]

    def invoice_orders(self) -> list[DayOrder]:
        """Who ordered the pages of each day billed (see invoice.DayOrder): the job's Excerpts... rows when
        they fit it, else every ticked attorney orders every page, shared by the Parties number when it was
        set. A row counts the billed pages in it (only the user's, on a transcript of several reporters). (Rows
        that need checking must stop the invoice before this: see portions_problem.)"""
        days, totals, counts = self.invoice_days(), self.day_totals(), self.day_reporters()
        rows = self.valid_portions()
        if rows and len(days) == 1:
            out, start = [], 0
            mask, printed = self.day_mask(), self.printed_pages()
            for last, keys in rows:
                # the printed numbers when they are known and run in order through the stretch; else (Pages
                # typed in, volumes that start their numbering again) its place in the day: "pages 1-20"
                nums = printed[start:last]
                ok = bool(nums) and None not in nums and all(a <= b for a, b in zip(nums, nums[1:]))
                span = f"pp. {nums[0]}–{nums[-1]}" if ok else f"pages {start + 1}–{last}"
                out.append(Portion(sum(mask[start:last]) if mask is not None else last - start, list(keys),
                                   span=span))
                start = last
            return [DayOrder(days[0][0], days[0][1], out, totals[0] if totals else 0, counts[0] if counts else {})]
        keys = self.ticked_keys()
        n = self.parties or len(keys)  # with nobody ticked: every attorney on the invoice (see firm_pages)
        totals += [0] * (len(days) - len(totals))
        counts += [{}] * (len(days) - len(counts))
        return [DayOrder(day, pages, [Portion(pages, keys, n)], total, who)
                for (day, pages), total, who in zip(days, totals, counts)]

    def order_lines(self, attorneys: list[Attorney] | None = None) -> list[OrderLine]:
        """Who orders what, spelled out for the window's "Who ordered what" card: a line per day and attorney,
        with the stretches of pages it ordered (whole day or an excerpt), who shares each of them, and how many
        of the user's pages that bills. attorneys: also give a line ("orders nothing") to these when they are not
        ticked on the day (the other days' attorneys of a joint invoice). [] without pages to bill."""
        days = self.invoice_days()
        if not days and not self.ownership_problem():
            return []
        held = self.invoice_hold()  # (the wording the Invoice panel and Generate all use)
        ticked = [a for a in self.case.invoice_orderers() if a is not None]
        keys = [a.key() for a in ticked]
        names = {a.key(): a.name or a.firm for a in ticked}
        others = [a for a in attorneys or [] if a.key() not in names]

        def nothing(day: str) -> list[OrderLine]:
            """The lines of the attorneys of the other days who aren't ticked on this one."""
            return [OrderLine(day, a.key(), a.name or a.firm, note="not ticked on this day: orders nothing")
                    for a in others]

        out: list[OrderLine] = []
        if held or not days:
            day = days[0][0] if len(days) == 1 else self.case.get("dates")
            return [OrderLine(day, k, names[k], note="⚠ " + held) for k in keys] + nothing(day)
        if len(days) > 1:  # a job of several days (Settings: all days on one form): every day is whole
            for (day, billed), whole in zip(days, self.day_totals()):
                for k in keys:
                    line = OrderLine(day, k, names[k], [(1, whole)], True, {o: [(1, whole)] for o in keys
                                                                            if o != k}, billed, whole)
                    line.parties = self.parties  # invoice_orders shares each day that many ways when typed
                    out.append(line)
                out += nothing(day)
            return out
        day, billed_day = days[0]
        pages = self.portion_pages()
        rows = self.valid_portions() or [(pages, keys)]
        mask, printed = self.day_mask(), self.printed_pages()
        stretches, start = [], 0  # (first, last, keys), 1-based
        for last, ks in rows:
            stretches.append((start + 1, last, [k for k in ks if k in names]))
            start = last

        def billed(first: int, last: int) -> int:
            """The pages billed among first..last (the user's own, when several reporters wrote the day)."""
            return sum(mask[first - 1:last]) if mask is not None else last - first + 1

        for k in keys:
            mine = [(a, b) for a, b, ks in stretches if k in ks]
            spans = _join(mine)
            shared: dict[str, list[tuple[int, int]]] = {}
            for a, b, ks in stretches:
                if k in ks:
                    for o in ks:
                        if o != k:
                            shared.setdefault(o, []).append((a, b))
            line = OrderLine(day, k, names[k], spans, spans == [(1, pages)],
                             {o: _join(s) for o, s in shared.items()}, sum(billed(a, b) for a, b in spans), pages)
            line.printed = [_printed_span(printed, a, b) for a, b in spans]
            line.parties = self.parties if self.valid_portions() is None else 0
            line.typed = self.pages_typed()
            out.append(line)
        return out + nothing(day)

    def output_problems(self, outputs) -> list[str]:
        """What stops the chosen outputs: missing fields for the agreement or MOFR, no transcript for an
        invoice or run sheet, or Excerpts... rows to check for the invoice."""
        out = self.problems() if {"agreement", "mofr"} & set(outputs) else []
        if "invoice" in outputs and self.invoice_hold():
            out.append(self.invoice_hold())
        elif "invoice" in outputs and not self.invoice_pages():
            out.append(NO_INVOICE)
        if "runsheet" in outputs and not self.transcript_pages():
            out.append(NO_RUNSHEET)
        return out

    def makeable(self, outputs) -> list[str]:
        """The outputs this job can have: all of them, less the run sheet when there is no transcript and the
        invoice when there are no pages to bill (no transcript, or the Pages field says 0). An invoice held until
        Whose pages... says whose pages to bill is kept: it is held, with the reason (see invoice_hold)."""
        return [o for o in outputs if not (o == "runsheet" and not self.transcript_pages())
                and not (o == "invoice" and not self.invoice_pages() and not self.ownership_problem())]

    def left_out_reason(self) -> str:
        """Why makeable() left something out: no transcript, or (with one, only the invoice) no pages to bill."""
        return "the Pages field says 0" if self.transcript_pages() else "no transcript PDF among the inputs"

    def file_count(self, outputs) -> int:
        """How many agreements and MOFRs generate() makes for this job (files_to_make counts the invoices and
        run sheets, which the days of a case share)."""
        per = {"agreement": self.form_count(), "mofr": 1}
        return sum(per.get(o, 0) for o in outputs)


@dataclass
class OrderLine:
    """One attorney's order of one day, as the "Who ordered what" card shows it (see Job.order_lines). spans:
    the stretches of the day's pages it ordered, counted from 1 over every page of the day's transcripts
    ([(20, 40)]); whole: every page of the day; shared: the other attorneys who ordered some of the same
    pages, and which; billed: the user's pages among them (what its invoice counts); pages: the day's pages;
    printed: the printed page numbers of each span ("pp. 120-140", "" when not known); note: instead of spans,
    why there is nothing to show ("not ticked on this day", a choice still to make)."""
    day: str
    key: str
    name: str
    spans: list = field(default_factory=list)
    whole: bool = False
    shared: dict = field(default_factory=dict)
    billed: int = 0
    pages: int = 0
    printed: list = field(default_factory=list)
    note: str = ""
    parties: int = 0  # the Parties number typed for the day (its price is shared that many ways); 0 = not typed
    typed: bool = False  # the pages are the Pages field's number, typed in, not the transcript's pages


def _join(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Stretches of pages that touch made one: [(1, 19), (20, 40)] -> [(1, 40)]."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _printed_span(printed: list, first: int, last: int) -> str:
    """'pp. 120-140': the printed numbers of pages first to last of the day, when known and in order; ""."""
    nums = printed[first - 1:last] if printed else []
    if not nums or None in nums or any(a > b for a, b in zip(nums, nums[1:])):
        return ""
    return f"p. {nums[0]}" if len(nums) == 1 else f"pp. {nums[0]}–{nums[-1]}"


def make_doc(ing: Ingested, regex: Extraction, s: Settings, path: str = "") -> Doc:
    """A Doc, with what it says about its case worked out from its regex fields."""
    return Doc(ing, regex, path=path, ident=ident(merge([regex], s)))


def remerge(job: Job, s: Settings) -> None:
    """Rebuilds job.case from its documents, keeping what the user entered."""
    prev = job.case
    new = merge([e for d in job.docs for e in d.extractions()], s, previous=prev)
    if job.proc_touched:
        new.proc_types = prev.proc_types
    if job.att_touched:
        known = prev.attorneys
        keys = [(a, a.key()) for a in known]
        new.attorneys = dedupe_attorneys(known + list(new.attorneys), s.profile)
        for a in new.attorneys[len(known):]:
            a.checked = False
        for a, k in keys:  # a name filled in from a duplicate: Excerpts... follows the attorney
            if any(a is b for b in new.attorneys) and a.key() != k and k not in {b.key() for b in new.attorneys}:
                job.rename_in_portions(k, a.key())
    if job.batch:
        _all_dates(new, job)
    job.own = tuple(sorted(my_initials(s.profile.name, s.profile.initials)))
    _all_pages(new, job)
    refresh_rate(new, s)  # a speed chosen by the user was restored after the defaults were applied
    if s.fill_delivery_date:
        refresh_delivery_date(new, s)
    job.case = new


def _date_key(d: str) -> tuple:
    """'6/2/2026' -> (2026, 6, 2), to sort M/D/YYYY dates."""
    m, day, y = (int(x) for x in d.split("/"))
    return y, m, day


def _all_dates(case: CaseInfo, job: Job) -> None:
    """When the grouped documents name different days, the form lists all of them (to review)."""
    fs = case.fields["dates"]
    if fs.source == SRC_USER:
        return
    have = {d for _, _, d in find_dates(fs.value)}
    every = have.union(*(d.ident.dates for d in job.docs))
    if every - have:
        value = ", ".join(sorted(every, key=_date_key))
        case.fields["dates"] = FieldState(value, fs.source or SRC_REGEX, 0.55,
                                          [value] + [a for a in fs.alternatives if a != value])


def _all_pages(case: CaseInfo, job: Job) -> None:
    """Several transcripts in one job (the days of a trial, volumes): their pages are added up. A transcript of
    several reporters counts the user's own pages (see Job.billed_mask); the whole count stays a suggestion."""
    fs = case.fields["est_pages"]
    if fs.source == SRC_USER:
        return
    counts = [transcript_pages(d.ing) for d in job.transcripts()]
    own = job.own_pages() if counts else 0
    if own and own != sum(counts):
        value, whole = str(own), str(sum(counts))
        case.fields["est_pages"] = FieldState(value, SRC_REGEX, 0.9, [value, whole] + [
            a for a in fs.alternatives if a not in (value, whole)])
    elif len(counts) > 1:
        total = str(sum(counts))
        case.fields["est_pages"] = FieldState(total, SRC_REGEX, 0.9,
                                              [total] + [a for a in fs.alternatives if a != total])


def group(docs: list[Doc], s: Settings, jobs: list[Job] | None = None) -> list[Job]:
    """Sorts documents into jobs: into one of `jobs` when they match it, else into new ones. A document that
    matches several jobs joins them into one. Returns every job, old and new."""
    jobs = list(jobs or [])
    changed: list[Job] = []
    any_date = s.batch_combine_dates

    def put(doc: Doc, hits: list[Job]) -> None:
        if hits:
            job = hits[0]
            for other in hits[1:]:  # this document links jobs that were separate
                job.docs += other.docs
                job.proc_touched |= other.proc_touched
                job.att_touched |= other.att_touched
                # the user's invoice and run sheet choices of either job are kept
                job.parties = job.parties or other.parties
                job.invoice_detail |= other.invoice_detail
                for name in ("invoice_email", "invoice_index", "invoice_show", "portions", "runsheet_to"):
                    if getattr(job, name) is None:
                        setattr(job, name, getattr(other, name))
                job.page_basis = {**other.page_basis, **job.page_basis}
                job.front_owner = {**other.front_owner, **job.front_owner}
                jobs.remove(other)
        else:
            job = Job()
            jobs.append(job)
        job.docs.append(doc)
        job.unbill()  # a new document may mean new pages to bill
        if job not in changed:
            changed.append(job)

    # Documents that name a day go first; the others then join the case if there is only one candidate.
    for doc in [d for d in docs if d.ident and d.ident.dates]:
        same = [j for j in jobs if any(same_case(doc.ident, i) for i in j.idents())]
        hits = [j for j in same if any(same_case(doc.ident, i) and i.dates and (any_date or doc.ident.dates & i.dates)
                                       for i in j.idents())]
        undated = [j for j in same if not any(i.dates for i in j.idents())]
        put(doc, hits or (undated if len(undated) == 1 else []))
    for doc in [d for d in docs if d.ident and not d.ident.dates]:
        same = [j for j in jobs if any(same_case(doc.ident, i) for i in j.idents())]
        put(doc, same if len(same) == 1 else [])
    for doc in [d for d in docs if not d.ident]:  # no index number or caption: can't be matched
        put(doc, [])

    for job in changed:
        job.batch = True
        remerge(job, s)
        if not job.idents():
            job.include = False  # nothing says which case this is: no form unless the user ticks it
    return jobs


# ------------------------------------------------------------ files in, PDFs out

def expand_paths(paths: list[str]) -> list[str]:
    """Files as given, folders replaced by the documents in them; no duplicates."""
    out, seen = [], set()

    def add(p: Path) -> None:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(str(p))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                # forms this app filled earlier are not source documents
                if (f.is_file() and f.suffix.lower() in BATCH_EXT
                        and not f.name.startswith(("Minute Agreement", "MOFR", "~$")) and not is_generated(f)):
                    add(f)
        elif p.is_file():
            add(p)
    return out


def read_docs(paths: list[str], s: Settings, progress: Progress | None = None) -> tuple[list[Doc], list[str]]:
    """Reads and extracts every file. Returns the documents and a message per unreadable file."""
    return read_loaders([lambda p=p: ingest_file(p) for p in paths], [Path(p).name for p in paths], s, paths,
                        progress)


def read_loaders(loaders: list[Callable[[], Ingested]], names: list[str], s: Settings,
                 paths: list[str] | None = None, progress: Progress | None = None) -> tuple[list[Doc], list[str]]:
    """read_docs for any inputs (files, pasted text, images): each loader returns the Ingested document.
    One input that can't be read doesn't stop the others; it gets a message instead."""
    extractor = RegexExtractor(s.profile, s.title_case_names)
    paths = paths or [""] * len(loaders)
    docs, errors = [], []
    for i, (load, name, path) in enumerate(zip(loaders, names, paths)):
        if progress:
            progress(i, len(loaders), name)
        try:
            ing = load()
            docs.append(make_doc(ing, extractor.extract(ing), s, path))
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.info("skipped a document that could not be read")
    return docs, errors


def out_dir_for(job: Job, s: Settings) -> Path:
    """Where a job's files go: Settings.output_dir, else the folder of its first document, else
    Documents/Minute Agreements."""
    if s.output_dir:
        return Path(s.output_dir)
    for d in job.docs:
        if d.path:
            return Path(d.path).parent
    return Path.home() / "Documents" / "Minute Agreements"


def input_folders(job: Job) -> list[Path]:
    """The folders of the job's documents (a run sheet may be kept next to the transcripts)."""
    return list(dict.fromkeys(Path(d.path).parent for d in job.docs if d.path))


def _first_day(job: Job) -> tuple:
    """The job's earliest date as a sort key; undated jobs last."""
    days = sorted((d for _, _, d in find_dates(job.case.get("dates"))), key=_date_key)
    return _date_key(days[0]) if days else (9999,)


def invoice_groups(jobs: list[Job], s: Settings) -> list[list[Job]]:
    """The jobs that share an invoice, earliest day first. With Settings.invoice_joint, every day of one case
    (the same index number, or a matching caption); otherwise each job alone. Jobs without a transcript have
    no invoice and are left out."""
    billable = [j for j in jobs if j.invoice_pages()]
    if not s.invoice_joint:
        return [[j] for j in billable]
    groups: list[list[Job]] = []
    for job in billable:
        i = ident(job.case)
        home = next((g for g in groups if i and any(same_case(i, ident(k.case)) for k in g)), None) if i else None
        if home is None:
            groups.append([job])
        else:
            home.append(job)
    for g in groups:
        g.sort(key=_first_day)
    return groups


def group_attorneys(group: list[Job]) -> list[Attorney]:
    """The attorneys ticked on any day of the group who get an invoice, each once (see
    CaseInfo.invoice_orderers: no placeholders or blank rows)."""
    out, seen = [], set()
    for job in group:
        for a in job.case.invoice_orderers():
            if a is not None and a.key() not in seen:
                seen.add(a.key())
                out.append(a)
    return out


def joint_invoice(group: list[Job]) -> tuple[CaseInfo, InvoiceOpts]:
    """The case and choices for one invoice covering a group of days (see invoice_groups): the first day's
    case with every date and every attorney ticked on any day, the pages of each day and who ordered them
    (each attorney's invoice then bills only its own days and pages, see invoice.firm_invoices). Each of the
    Extras, the granular choices and the parties comes from the earliest day that has one of its own, so a
    choice isn't lost when an earlier day joins the group."""
    first = group[0]
    if len(group) == 1:
        return first.case, first.invoice_opts()
    case = deepcopy(first.case)
    days = [d for job in group for d in job.invoice_days()]
    case.set("dates", ", ".join(day for day, _ in days if day), case.fields["dates"].source)
    case.attorneys = [deepcopy(a) for a in group_attorneys(group)]
    opts = first.invoice_opts()
    opts.days, opts.pages = days, sum(n for _, n in days)
    opts.orders = [o for job in group for o in job.invoice_orders()]
    opts.my_pages, opts.total_pages = opts.pages, sum(j.transcript_pages() for j in group)
    opts.reporters = reporters_text(day_reporters(opts.orders))

    def own(name: str):
        return next((getattr(j, name) for j in group if getattr(j, name) is not None), None)

    opts.email, opts.index, opts.show = own("invoice_email"), own("invoice_index"), own("invoice_show")
    opts.detail = any(j.invoice_detail for j in group)  # ticked on any day of the invoice
    opts.parties = next((j.parties for j in group if j.parties), 0) or max(1, len(case.attorneys))
    # an attorney whose invoice for these days a stopped run made already (on every one of them) isn't billed
    # again; when a day was added since, it is, as its invoice now covers that day too
    opts.skip = [k for k in first.billed_keys() if all(k in j.billed_keys() for j in group)]
    return case, opts


def fill_jobs(jobs: list[Job], s: Settings, progress: Progress | None = None,
              batch: list[Job] | None = None, outputs: list[str] | None = None) -> list[Path]:
    """Makes the outputs (default: Settings.outputs) of every job; problems are recorded in job.error
    instead of stopping the batch. A job without a transcript gets no invoice or run sheet (noted in job.error).
    A job already invoiced (Job.invoiced) is not billed again, nor an attorney a stopped run invoiced already
    (Job.invoiced_keys). A job whose invoice is held (Job.invoice_hold: Excerpts... rows to check, or whose
    pages to bill) gets no invoice,
    nor the days of a case with a day nobody is ticked on (group_problem): their errors say so. `batch` is the
    whole batch when only some of its jobs are filled: jobs for several days of one case get the date in their
    file names."""
    outputs = list(s.outputs if outputs is None else outputs)
    ledger = ledger_for(s)
    names = [j.name_key() for j in batch or jobs]
    unchecked = {id(j) for j in jobs if j.invoice_hold()} if "invoice" in outputs else set()
    to_bill = [j for j in jobs if not j.invoiced and id(j) not in unchecked]
    joint = [g for g in invoice_groups(to_bill, s) if len(g) > 1] if "invoice" in outputs else []
    in_joint = {id(j) for g in joint for j in g}  # these days are billed together, after the loop
    steps = len(jobs) + len(joint)
    done: list[Path] = []
    started: list[Path] = []  # run sheets started by this batch: the other days of the trial go on them too
    made_by_group: dict[int, Path] = {}  # the run sheet of each case the window asked about (Job.runsheet_group)
    for i, job in enumerate(jobs):
        if progress:
            progress(i, steps, job.title())
        sheet = None
        keys: list[str] = []  # the attorneys invoiced for this job by generate
        try:
            want = job.makeable(outputs)
            billed_elsewhere = job.invoiced or id(job) in in_joint  # billed before, or on the joint invoice
            unchecked_here = id(job) in unchecked and "invoice" in want and not billed_elsewhere
            if billed_elsewhere or unchecked_here:
                want = [o for o in want if o != "invoice"]
            sheet = job.runsheet_opts(s) if "runsheet" in want else None
            if sheet and job.runsheet_group in made_by_group:
                sheet.target = str(made_by_group[job.runsheet_group])
            elif sheet and sheet.target is None and s.runsheet_existing != "new":
                sheet.target = _started_for(job, started, s)
            job.saved = generate(job.case, s, out_dir_for(job, s), want, job.invoice_opts(), ledger,
                                 dated=names.count(job.name_key()) > 1, runsheet=sheet,
                                 folders=input_folders(job), invoiced=keys)
            job.invoiced |= "invoice" in want
            left_out = [OUTPUTS[o].lower() for o in outputs if o not in want
                        and not (o == "invoice" and (billed_elsewhere or unchecked_here))]
            problems = [f"no {' or '.join(left_out)}: {job.left_out_reason()}"] if left_out else []
            if unchecked_here and job.portions_problem():
                problems.append(f"{NOT_INVOICED} check Excerpts… for {job.case.get('dates') or job.title()}")
            elif unchecked_here:
                problems.append(f"{NOT_INVOICED} choose under Whose pages… ({job.ownership_problem()})")
            job.error = "; ".join(problems)
            done += [p for p in job.saved if p not in done]  # one run sheet takes several days
        except Exception as e:
            job.saved = list(getattr(e, "made", []))  # what was made before the problem
            job.error = f"{type(e).__name__}: {e}"
            if keys:  # some attorneys' invoices were made: the next run leaves them out
                job.invoiced_keys = list(dict.fromkeys(job.invoiced_keys + keys))
            done += [p for p in job.saved if p not in done]  # one run sheet takes several days
            log_error("could not save the forms of a job", e)
        if sheet and sheet.path:
            if job.runsheet_group is not None:
                made_by_group.setdefault(job.runsheet_group, sheet.path)
            if sheet.created and sheet.path not in started:
                started.append(sheet.path)
    for k, g in enumerate(joint):  # one invoice for all the days of a case
        first = g[0]
        if progress:
            progress(len(jobs) + k, steps, f"Invoice for {first.title()}")
        keys = []  # the attorneys invoiced
        held = group_problem(g)
        if held:  # a day with nobody ticked: no invoice until it says who ordered it
            for job in g:
                job.error = "; ".join(x for x in (job.error, f"{NOT_INVOICED} {held}") if x)
            continue
        try:
            case, opts = joint_invoice(g)
            made = generate(case, s, out_dir_for(first, s), ["invoice"], opts, ledger, invoiced=keys)
            problem = ""
        except Exception as e:
            made = list(getattr(e, "made", []))
            problem = f"invoice: {str(e) or type(e).__name__}"  # every day of it says so (plain text, for the window's message)
            log_error("could not make the invoice for several days of a case", e)
        for job in g:  # the invoice is each day's: none of them is billed again
            job.saved += [p for p in made if p not in job.saved]
            if problem:
                job.error = "; ".join(x for x in (job.error, problem) if x)
                # the attorneys whose invoices were made before the problem aren't billed again by the next run
                job.invoiced_keys = list(dict.fromkeys(job.billed_keys() + keys))
            else:
                job.invoiced = True
        done += made
    log.info("saved %d form(s) for %d job(s)", len(done), len(jobs))
    return done


def _started_for(job: Job, started: list[Path], s: Settings) -> str | None:
    """A run sheet this batch started that is this job's case too (another day of the trial): the same index
    number, or the same case name when Settings say to add to such run sheets. None: as Settings say."""
    for p in started:
        info = read_info(p)
        why = matches(info, job.case.get("case_name"), job.case.get("index_no")) if info else ""
        if why == "index" or (why == "name" and s.runsheet_existing == "add"):
            return str(p)
    return None


def group_problem(group: list[Job]) -> str:
    """Why the joint invoice of these days can't be made yet ("" when it can): a day with nobody ticked while
    other days have attorneys ticked. Nobody can be billed for its pages until it says who ordered them, so
    the window and the batch warn instead of guessing. Days with no attorney ticked at all (a transcript
    without appearances) still make one invoice with a blank Bill To."""
    if len(group) < 2:
        return ""
    empty = [j for j in group if not j.ticked_keys()]
    if not empty or len(empty) == len(group):
        return ""
    days = ", ".join(j.case.get("dates") or j.title() for j in empty)
    return f"nobody is ticked on {days}: tick who ordered {'that day' if len(empty) == 1 else 'those days'}"


def files_to_make(jobs: list[Job], outputs, s: Settings | None = None) -> int:
    """How many files fill_jobs will make for these jobs: the days of one case share a run sheet, and (as
    Settings.invoice_joint says) the invoices, one for each attorney ticked on any of its days who ordered
    pages and was not invoiced yet. Days already invoiced (Job.invoiced), and days whose invoice is held
    (Excerpts... rows to check, or Whose pages... to choose: Job.invoice_hold), get none."""
    count = sum(j.file_count(outputs) for j in jobs)
    if "invoice" in outputs:
        groups = invoice_groups([j for j in jobs if not j.invoiced and not j.invoice_hold()], s or Settings())
        count += sum(invoice_count(*joint_invoice(g)) for g in groups if not group_problem(g))
    cases: list[Ident] = []
    for j in jobs:
        if "runsheet" in j.makeable(outputs):
            i = ident(j.case)
            if not i or not any(same_case(i, k) for k in cases):
                cases.append(i)
    return count + len(cases)
